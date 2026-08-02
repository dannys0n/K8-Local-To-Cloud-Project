package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
)

const (
	defaultLeaseDuration = 3 * time.Second
	defaultRenewInterval = 500 * time.Millisecond
)

var locations = []struct {
	serverID string
	location string
}{
	{"tcp-server-0", "los-angeles"},
	{"tcp-server-1", "new-york"},
	{"tcp-server-2", "london"},
	{"tcp-server-3", "singapore"},
	{"tcp-server-4", "frankfurt"},
}

type assignment struct {
	ServerID   string
	Location   string
	Generation int64
	LeaseUntil time.Time
}

type server struct {
	instanceID    string
	podName       string
	db            *sql.DB
	redis         *redis.Client
	leaseDuration time.Duration
	renewInterval time.Duration
	mu            sync.RWMutex
	assignment    *assignment
}

type response struct {
	Server     string `json:"server"`
	Location   string `json:"location"`
	Instance   string `json:"instance"`
	Generation int64  `json:"generation"`
	Counter    uint64 `json:"counter"`
	Message    string `json:"message"`
	Time       string `json:"time"`
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	podName := envOrDefault("POD_NAME", hostname())
	instanceID := envOrDefault("POD_UID", podName)
	db, cache, err := connectDatabases(ctx, logger)
	if err != nil {
		logger.Error("connect databases", "error", err)
		os.Exit(1)
	}
	defer db.Close()
	defer cache.Close()

	leaseDuration, err := durationFromEnv("ASSIGNMENT_LEASE_DURATION", defaultLeaseDuration)
	if err != nil {
		logger.Error("invalid assignment lease duration", "error", err)
		os.Exit(1)
	}
	renewInterval, err := durationFromEnv("ASSIGNMENT_RENEW_INTERVAL", defaultRenewInterval)
	if err != nil {
		logger.Error("invalid assignment renew interval", "error", err)
		os.Exit(1)
	}
	if leaseDuration < 3*renewInterval {
		logger.Error("assignment lease must cover at least three renew intervals", "lease", leaseDuration, "renew_interval", renewInterval)
		os.Exit(1)
	}

	s := &server{
		instanceID: instanceID, podName: podName, db: db, redis: cache,
		leaseDuration: leaseDuration, renewInterval: renewInterval,
	}
	go s.manageAssignment(ctx, logger)
	go s.heartbeat(ctx, logger)

	listenAddr := envOrDefault("LISTEN_ADDR", ":7000")
	listener, err := net.Listen("tcp", listenAddr)
	if err != nil {
		logger.Error("listen", "address", listenAddr, "error", err)
		os.Exit(1)
	}
	defer listener.Close()
	logger.Info("pool server ready", "instance", podName, "address", listenAddr,
		"assignment_lease", leaseDuration, "assignment_renew_interval", renewInterval)

	go func() {
		<-ctx.Done()
		_ = listener.Close()
	}()
	var connections sync.WaitGroup
	for {
		conn, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				break
			}
			logger.Warn("accept connection", "error", err)
			continue
		}
		connections.Add(1)
		go func() {
			defer connections.Done()
			s.handleConnection(ctx, conn, logger)
		}()
	}

	connections.Wait()
	cleanupCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	s.release(cleanupCtx)
	_ = s.redis.Del(cleanupCtx, s.presenceKey()).Err()
	logger.Info("pool server stopped", "instance", podName)
}

func connectDatabases(ctx context.Context, logger *slog.Logger) (*sql.DB, *redis.Client, error) {
	postgresDSN := strings.TrimSpace(os.Getenv("POSTGRES_DSN"))
	redisAddr := strings.TrimSpace(os.Getenv("REDIS_ADDR"))
	if postgresDSN == "" || redisAddr == "" {
		return nil, nil, errors.New("POSTGRES_DSN and REDIS_ADDR are required")
	}
	if !strings.Contains(postgresDSN, "connect_timeout=") {
		separator := "&"
		if !strings.Contains(postgresDSN, "?") {
			separator = "?"
		}
		postgresDSN += separator + "connect_timeout=2"
	}
	db, err := sql.Open("postgres", postgresDSN)
	if err != nil {
		return nil, nil, err
	}
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(5)
	cache := redis.NewClient(&redis.Options{Addr: redisAddr})

	startupCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	for {
		if err := db.PingContext(startupCtx); err == nil {
			break
		} else {
			logger.Info("waiting for postgres", "error", err)
		}
		select {
		case <-startupCtx.Done():
			db.Close()
			cache.Close()
			return nil, nil, fmt.Errorf("postgres startup timeout: %w", startupCtx.Err())
		case <-time.After(time.Second):
		}
	}
	cacheCtx, cancelCache := context.WithTimeout(ctx, time.Second)
	cacheErr := cache.Ping(cacheCtx).Err()
	cancelCache()
	if cacheErr != nil {
		logger.Warn("redis unavailable; continuing without cache", "error", cacheErr)
	}
	if err := createSchema(startupCtx, db); err != nil {
		db.Close()
		cache.Close()
		return nil, nil, err
	}
	return db, cache, nil
}

func createSchema(ctx context.Context, db *sql.DB) error {
	const schema = `
		CREATE TABLE IF NOT EXISTS tcp_server_state (
			server_id TEXT PRIMARY KEY,
			location TEXT NOT NULL UNIQUE,
			counter BIGINT NOT NULL DEFAULT 0 CHECK (counter >= 0),
			updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);
		CREATE TABLE IF NOT EXISTS tcp_server_assignment (
			server_id TEXT PRIMARY KEY REFERENCES tcp_server_state(server_id),
			owner_instance_id TEXT,
			generation BIGINT NOT NULL DEFAULT 0,
			lease_until TIMESTAMPTZ
		);
		CREATE UNIQUE INDEX IF NOT EXISTS tcp_server_state_location
			ON tcp_server_state (location)`
	if _, err := db.ExecContext(ctx, schema); err != nil {
		return fmt.Errorf("create schema: %w", err)
	}
	for _, item := range locations {
		if _, err := db.ExecContext(ctx, `
			INSERT INTO tcp_server_state (server_id, location) VALUES ($1, $2)
			ON CONFLICT (server_id) DO UPDATE SET location = EXCLUDED.location`, item.serverID, item.location); err != nil {
			return fmt.Errorf("seed location %s: %w", item.location, err)
		}
		if _, err := db.ExecContext(ctx, `
			INSERT INTO tcp_server_assignment (server_id) VALUES ($1)
			ON CONFLICT (server_id) DO NOTHING`, item.serverID); err != nil {
			return fmt.Errorf("seed assignment %s: %w", item.serverID, err)
		}
	}
	return nil
}

func (s *server) manageAssignment(ctx context.Context, logger *slog.Logger) {
	ticker := time.NewTicker(s.renewInterval)
	defer ticker.Stop()
	for {
		current := s.currentAssignment()
		operationCtx, cancel := context.WithTimeout(ctx, time.Second)
		if current == nil {
			if claimed, err := s.claim(operationCtx); err != nil {
				if ctx.Err() == nil {
					logger.Warn("claim location", "instance", s.podName, "error", err)
				}
			} else if claimed != nil {
				s.setAssignment(claimed)
				logger.Info("location claimed", "instance", s.podName, "server", claimed.ServerID, "location", claimed.Location, "generation", claimed.Generation)
			}
		} else if renewed, err := s.renew(operationCtx, current); err != nil {
			if time.Now().After(current.LeaseUntil) {
				s.clearAssignment(current.Generation)
				logger.Warn("location lease lost", "instance", s.podName, "server", current.ServerID, "generation", current.Generation)
			}
		} else {
			s.setAssignment(renewed)
		}
		cancel()

		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *server) claim(ctx context.Context) (*assignment, error) {
	const query = `
		WITH candidate AS (
			SELECT server_id FROM tcp_server_assignment
			WHERE owner_instance_id IS NULL OR lease_until < NOW()
			ORDER BY server_id
			FOR UPDATE SKIP LOCKED LIMIT 1
		), claimed AS (
			UPDATE tcp_server_assignment AS a
			SET owner_instance_id = $1, generation = generation + 1,
				lease_until = NOW() + ($2 * INTERVAL '1 second')
			FROM candidate c WHERE a.server_id = c.server_id
			RETURNING a.server_id, a.generation, a.lease_until
		)
		SELECT c.server_id, s.location, c.generation, c.lease_until
		FROM claimed c JOIN tcp_server_state s USING (server_id)`
	claimed := &assignment{}
	err := s.db.QueryRowContext(ctx, query, s.instanceID, s.leaseDuration.Seconds()).Scan(
		&claimed.ServerID, &claimed.Location, &claimed.Generation, &claimed.LeaseUntil,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return claimed, err
}

func (s *server) renew(ctx context.Context, current *assignment) (*assignment, error) {
	const query = `
		UPDATE tcp_server_assignment
		SET lease_until = NOW() + ($4 * INTERVAL '1 second')
		WHERE server_id = $1 AND owner_instance_id = $2 AND generation = $3
		RETURNING lease_until`
	renewed := *current
	err := s.db.QueryRowContext(ctx, query, current.ServerID, s.instanceID, current.Generation, s.leaseDuration.Seconds()).Scan(&renewed.LeaseUntil)
	return &renewed, err
}

func (s *server) release(ctx context.Context) {
	current := s.currentAssignment()
	if current == nil {
		return
	}
	_, _ = s.db.ExecContext(ctx, `
		UPDATE tcp_server_assignment SET owner_instance_id = NULL, lease_until = NULL
		WHERE server_id = $1 AND owner_instance_id = $2 AND generation = $3`,
		current.ServerID, s.instanceID, current.Generation,
	)
}

func (s *server) heartbeat(ctx context.Context, logger *slog.Logger) {
	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()
	for {
		value := map[string]any{"instance": s.podName, "status": "spare"}
		if current := s.currentAssignment(); current != nil {
			value["status"] = "active"
			value["server"] = current.ServerID
			value["location"] = current.Location
			value["generation"] = current.Generation
		}
		body, _ := json.Marshal(value)
		cacheCtx, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
		err := s.redis.Set(cacheCtx, s.presenceKey(), body, 10*time.Second).Err()
		cancel()
		if err != nil && ctx.Err() == nil {
			logger.Warn("refresh redis presence", "instance", s.podName, "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *server) handleConnection(ctx context.Context, conn net.Conn, logger *slog.Logger) {
	defer conn.Close()
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			_ = conn.Close()
		case <-done:
		}
	}()

	remote := conn.RemoteAddr().String()
	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 4096), 64*1024)
	writer := bufio.NewWriter(conn)
	var bound *assignment

	for scanner.Scan() {
		message := strings.TrimSpace(scanner.Text())
		if message == "" {
			continue
		}
		if requested, found := strings.CutPrefix(message, "@probe "); found {
			current := s.currentAssignment()
			status := "spare"
			if current != nil && (requested == "any" || requested == current.Location) {
				status = "ready"
			}
			_, _ = fmt.Fprintf(writer, `{"status":%q}`+"\n", status)
			_ = writer.Flush()
			return
		}

		current := s.currentAssignment()
		if requested, found := strings.CutPrefix(message, "@location "); found {
			if current == nil || (requested != "any" && requested != current.Location) {
				_, _ = fmt.Fprintln(writer, `{"error":"location unavailable"}`)
				_ = writer.Flush()
				return
			}
			bound = current
			count, err := s.currentCounter(ctx, bound.ServerID)
			if err != nil {
				s.writeDatabaseError(writer, logger, err)
				return
			}
			if !s.writeResponse(writer, bound, count, "connected") {
				return
			}
			continue
		}

		if bound == nil || current == nil || current.ServerID != bound.ServerID || current.Generation != bound.Generation {
			_, _ = fmt.Fprintln(writer, `{"error":"assignment changed"}`)
			_ = writer.Flush()
			return
		}
		count, err := s.increment(ctx, bound)
		if err != nil {
			s.writeDatabaseError(writer, logger, err)
			return
		}
		if !s.writeResponse(writer, bound, count, message) {
			return
		}
	}
	if err := scanner.Err(); err != nil {
		logger.Debug("connection read ended", "instance", s.podName, "remote", remote, "error", err)
	}
}

func (s *server) writeResponse(writer *bufio.Writer, current *assignment, counter uint64, message string) bool {
	body, err := json.Marshal(response{
		Server: current.ServerID, Location: current.Location, Instance: s.podName,
		Generation: current.Generation, Counter: counter, Message: message,
		Time: time.Now().UTC().Format(time.RFC3339Nano),
	})
	if err != nil {
		return false
	}
	if _, err := writer.Write(append(body, '\n')); err != nil {
		return false
	}
	return writer.Flush() == nil
}

func (s *server) writeDatabaseError(writer *bufio.Writer, logger *slog.Logger, err error) {
	logger.Error("database operation", "instance", s.podName, "error", err)
	_, _ = fmt.Fprintln(writer, `{"error":"database unavailable or stale assignment"}`)
	_ = writer.Flush()
}

func (s *server) currentCounter(ctx context.Context, serverID string) (uint64, error) {
	queryCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	var counter uint64
	err := s.db.QueryRowContext(queryCtx, `SELECT counter FROM tcp_server_state WHERE server_id = $1`, serverID).Scan(&counter)
	return counter, err
}

func (s *server) increment(ctx context.Context, current *assignment) (uint64, error) {
	queryCtx, cancelQuery := context.WithTimeout(ctx, 2*time.Second)
	defer cancelQuery()
	const query = `
		UPDATE tcp_server_state AS state
		SET counter = state.counter + 1, updated_at = NOW()
		FROM tcp_server_assignment AS assignment
		WHERE state.server_id = $1
			AND assignment.server_id = state.server_id
			AND assignment.owner_instance_id = $2
			AND assignment.generation = $3
			AND assignment.lease_until > NOW()
		RETURNING state.counter`
	var counter uint64
	err := s.db.QueryRowContext(queryCtx, query, current.ServerID, s.instanceID, current.Generation).Scan(&counter)
	if err == nil {
		cacheCtx, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
		_ = s.redis.Set(cacheCtx, "tcp-lab:server:"+current.ServerID+":counter", counter, 0).Err()
		cancel()
	}
	return counter, err
}

func (s *server) currentAssignment() *assignment {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.assignment == nil || time.Now().After(s.assignment.LeaseUntil) {
		return nil
	}
	copy := *s.assignment
	return &copy
}

func (s *server) setAssignment(value *assignment) {
	s.mu.Lock()
	defer s.mu.Unlock()
	copy := *value
	s.assignment = &copy
}

func (s *server) clearAssignment(generation int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.assignment != nil && s.assignment.Generation == generation {
		s.assignment = nil
	}
}

func (s *server) presenceKey() string { return "tcp-lab:instance:" + s.podName + ":presence" }

func envOrDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func durationFromEnv(name string, fallback time.Duration) (time.Duration, error) {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback, nil
	}
	duration, err := time.ParseDuration(value)
	if err != nil || duration <= 0 {
		return 0, fmt.Errorf("%s must be a positive Go duration: %q", name, value)
	}
	return duration, nil
}

func hostname() string {
	value, err := os.Hostname()
	if err != nil || value == "" {
		return "unknown-server"
	}
	return value
}
