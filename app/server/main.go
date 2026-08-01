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
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
)

type server struct {
	name     string
	location string
	db       *sql.DB
	redis    *redis.Client
}

type response struct {
	Server   string `json:"server"`
	Location string `json:"location"`
	Counter  uint64 `json:"counter"`
	Message  string `json:"message"`
	Time     string `json:"time"`
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	name := envOrDefault("POD_NAME", hostname())
	location := locationFor(name)
	db, cache, err := connectDatabases(ctx, logger)
	if err != nil {
		logger.Error("connect databases", "error", err)
		os.Exit(1)
	}
	defer db.Close()
	defer cache.Close()

	s := &server{name: name, location: location, db: db, redis: cache}
	counter, err := s.register(ctx)
	if err != nil {
		logger.Error("register server", "error", err)
		os.Exit(1)
	}
	go s.heartbeat(ctx, logger)

	listenAddr := envOrDefault("LISTEN_ADDR", ":7000")
	listener, err := net.Listen("tcp", listenAddr)
	if err != nil {
		logger.Error("listen", "address", listenAddr, "error", err)
		os.Exit(1)
	}
	defer listener.Close()

	logger.Info("server ready", "server", s.name, "location", s.location, "address", listenAddr, "counter", counter)
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
	_ = s.redis.Del(cleanupCtx, s.presenceKey()).Err()
	logger.Info("server stopped", "server", s.name)
}

func connectDatabases(ctx context.Context, logger *slog.Logger) (*sql.DB, *redis.Client, error) {
	postgresDSN := strings.TrimSpace(os.Getenv("POSTGRES_DSN"))
	redisAddr := strings.TrimSpace(os.Getenv("REDIS_ADDR"))
	if postgresDSN == "" || redisAddr == "" {
		return nil, nil, errors.New("POSTGRES_DSN and REDIS_ADDR are required")
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
		postgresErr := db.PingContext(startupCtx)
		if postgresErr == nil {
			break
		}
		logger.Info("waiting for postgres", "error", postgresErr)
		select {
		case <-startupCtx.Done():
			db.Close()
			cache.Close()
			return nil, nil, fmt.Errorf("database startup timeout: %w", startupCtx.Err())
		case <-time.After(time.Second):
		}
	}
	if err := cache.Ping(startupCtx).Err(); err != nil {
		logger.Warn("redis unavailable; continuing without cache", "error", err)
	}

	const schema = `
		CREATE TABLE IF NOT EXISTS tcp_server_state (
			server_id TEXT PRIMARY KEY,
			location TEXT NOT NULL,
			counter BIGINT NOT NULL DEFAULT 0 CHECK (counter >= 0),
			updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		)`
	if _, err := db.ExecContext(startupCtx, schema); err != nil {
		db.Close()
		cache.Close()
		return nil, nil, fmt.Errorf("create schema: %w", err)
	}
	return db, cache, nil
}

func (s *server) register(ctx context.Context) (uint64, error) {
	const query = `
		INSERT INTO tcp_server_state (server_id, location)
		VALUES ($1, $2)
		ON CONFLICT (server_id) DO UPDATE
		SET location = EXCLUDED.location, updated_at = NOW()
		RETURNING counter`
	var counter uint64
	if err := s.db.QueryRowContext(ctx, query, s.name, s.location).Scan(&counter); err != nil {
		return 0, err
	}
	_ = s.redis.Set(ctx, s.counterKey(), counter, 0).Err()
	return counter, nil
}

func (s *server) heartbeat(ctx context.Context, logger *slog.Logger) {
	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()
	for {
		if err := s.redis.Set(ctx, s.presenceKey(), s.location, 10*time.Second).Err(); err != nil && ctx.Err() == nil {
			logger.Warn("refresh redis presence", "server", s.name, "error", err)
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
	logger.Info("client connected", "server", s.name, "remote", remote)
	defer logger.Info("client disconnected", "server", s.name, "remote", remote)

	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 4096), 64*1024)
	writer := bufio.NewWriter(conn)
	for scanner.Scan() {
		message := strings.TrimSpace(scanner.Text())
		if message == "" {
			continue
		}

		responseMessage := message
		var count uint64
		var err error
		if requested, found := strings.CutPrefix(message, "@location "); found {
			if requested != "any" && requested != s.location {
				_, _ = fmt.Fprintf(writer, `{"error":"wrong location","server":%q,"location":%q}`+"\n", s.name, s.location)
				_ = writer.Flush()
				return
			}
			count, err = s.currentCounter(ctx)
			responseMessage = "connected"
		} else {
			count, err = s.increment(ctx)
		}
		if err != nil {
			logger.Error("database operation", "server", s.name, "error", err)
			_, _ = fmt.Fprintln(writer, `{"error":"database unavailable"}`)
			_ = writer.Flush()
			return
		}

		body, err := json.Marshal(response{
			Server: s.name, Location: s.location, Counter: count,
			Message: responseMessage, Time: time.Now().UTC().Format(time.RFC3339Nano),
		})
		if err != nil {
			return
		}
		if _, err := writer.Write(append(body, '\n')); err != nil {
			return
		}
		if err := writer.Flush(); err != nil {
			return
		}
	}
	if err := scanner.Err(); err != nil {
		logger.Debug("connection read ended", "remote", remote, "error", err)
	}
}

func (s *server) currentCounter(ctx context.Context) (uint64, error) {
	var counter uint64
	err := s.db.QueryRowContext(ctx, `SELECT counter FROM tcp_server_state WHERE server_id = $1`, s.name).Scan(&counter)
	return counter, err
}

func (s *server) increment(ctx context.Context) (uint64, error) {
	var counter uint64
	err := s.db.QueryRowContext(ctx, `
		UPDATE tcp_server_state
		SET counter = counter + 1, updated_at = NOW()
		WHERE server_id = $1
		RETURNING counter`, s.name).Scan(&counter)
	if err == nil {
		_ = s.redis.Set(ctx, s.counterKey(), counter, 0).Err()
	}
	return counter, err
}

func (s *server) presenceKey() string { return "tcp-lab:server:" + s.name + ":presence" }
func (s *server) counterKey() string  { return "tcp-lab:server:" + s.name + ":counter" }

func locationFor(serverName string) string {
	locations := []string{"los-angeles", "new-york", "london", "singapore", "frankfurt"}
	separator := strings.LastIndexByte(serverName, '-')
	if separator < 0 {
		return "unknown"
	}
	ordinal, err := strconv.Atoi(serverName[separator+1:])
	if err != nil || ordinal < 0 || ordinal >= len(locations) {
		return "unknown"
	}
	return locations[ordinal]
}

func envOrDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func hostname() string {
	value, err := os.Hostname()
	if err != nil || value == "" {
		return "unknown-server"
	}
	return value
}
