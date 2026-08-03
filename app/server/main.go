package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
)

const (
	defaultLeaseDuration = 3 * time.Second
	defaultRenewInterval = 500 * time.Millisecond
	defaultTickInterval  = 50 * time.Millisecond
	movementSpeed        = 40.0
)

type locationDefinition struct {
	serverID  string
	location  string
	latitude  float64
	longitude float64
}

var locations = []locationDefinition{
	{"tcp-server-0", "los-angeles", 34.0522, -118.2437},
	{"tcp-server-1", "new-york", 40.7128, -74.0060},
	{"tcp-server-2", "london", 51.5074, -0.1278},
	{"tcp-server-3", "singapore", 1.3521, 103.8198},
	{"tcp-server-4", "frankfurt", 50.1109, 8.6821},
}

type assignment struct {
	ServerID   string
	Location   string
	Latitude   float64
	Longitude  float64
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
	tickInterval  time.Duration
	tick          atomic.Uint64
	durable       chan durableCommand
	inputs        chan inputCommand
	entityMu      sync.Mutex
	entities      map[string]*entityState
	mu            sync.RWMutex
	assignment    *assignment
}

type response struct {
	Server          string  `json:"server"`
	Location        string  `json:"location"`
	Latitude        float64 `json:"latitude"`
	Longitude       float64 `json:"longitude"`
	Instance        string  `json:"instance"`
	Generation      int64   `json:"generation"`
	Counter         uint64  `json:"counter"`
	Message         string  `json:"message"`
	Time            string  `json:"time"`
	Tick            uint64  `json:"tick"`
	ClientUID       string  `json:"client_uid,omitempty"`
	OperationID     string  `json:"operation_id,omitempty"`
	ClientLatitude  float64 `json:"client_latitude"`
	ClientLongitude float64 `json:"client_longitude"`
	InputSequence   uint64  `json:"input_sequence,omitempty"`
	Reroute         string  `json:"reroute,omitempty"`
}

type durableRequest struct {
	Kind        string
	ClientUID   string
	OperationID string
}

type durableCommand struct {
	assignment assignment
	request    durableRequest
	result     chan durableResult
}

type durableResult struct {
	counter   uint64
	latitude  float64
	longitude float64
	tick      uint64
	err       error
}

type inputIntent struct {
	ClientUID string
	Sequence  uint64
	X         float64
	Y         float64
	Teleport  bool
	Resume    bool
	Latitude  float64
	Longitude float64
}

type inputCommand struct {
	intent inputIntent
	claim  bool
	result chan inputResult
}

type inputResult struct {
	latitude  float64
	longitude float64
	counter   uint64
	sequence  uint64
	tick      uint64
	claim     bool
	err       error
	reroute   string
}

type entityState struct {
	latitude      float64
	longitude     float64
	counter       uint64
	sequence      uint64
	axisX         float64
	axisY         float64
	lastInputTick uint64
	reroute       string
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
	tickInterval, err := durationFromEnv("SERVER_TICK_INTERVAL", defaultTickInterval)
	if err != nil || tickInterval <= 0 {
		logger.Error("invalid server tick interval", "error", err)
		os.Exit(1)
	}

	s := &server{
		instanceID: instanceID, podName: podName, db: db, redis: cache,
		leaseDuration: leaseDuration, renewInterval: renewInterval, tickInterval: tickInterval,
		durable: make(chan durableCommand, 4096),
		inputs:  make(chan inputCommand, 8192), entities: make(map[string]*entityState),
	}
	go s.manageAssignment(ctx, logger)
	go s.heartbeat(ctx, logger)
	go s.runTicks(ctx)
	go s.runDurableCommands(ctx)

	listenAddr := envOrDefault("LISTEN_ADDR", ":7000")
	listener, err := net.Listen("tcp", listenAddr)
	if err != nil {
		logger.Error("listen", "address", listenAddr, "error", err)
		os.Exit(1)
	}
	defer listener.Close()
	logger.Info("pool server ready", "instance", podName, "address", listenAddr,
		"assignment_lease", leaseDuration, "assignment_renew_interval", renewInterval,
		"tick_interval", tickInterval)

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

func (s *server) runTicks(ctx context.Context) {
	ticker := time.NewTicker(s.tickInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			currentTick := s.tick.Add(1)
			s.applyTransientInputs(currentTick)
		}
	}
}

func (s *server) runDurableCommands(ctx context.Context) {
	ticker := time.NewTicker(s.tickInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.commitDurableCommands(ctx, s.tick.Load())
		}
	}
}

func (s *server) commitDurableCommands(ctx context.Context, tick uint64) {
	batch := make([]durableCommand, 0, 256)
	for len(batch) < 1024 {
		select {
		case command := <-s.durable:
			batch = append(batch, command)
		default:
			if len(batch) > 0 {
				s.applyDurableBatch(ctx, tick, batch)
			}
			return
		}
	}
	s.applyDurableBatch(ctx, tick, batch)
}

func (s *server) applyDurableBatch(ctx context.Context, tick uint64, batch []durableCommand) {
	queryCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	tx, err := s.db.BeginTx(queryCtx, nil)
	if err != nil {
		s.finishDurableBatch(batch, nil, nil, nil, nil, tick, err)
		return
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(queryCtx, `SET LOCAL synchronous_commit = on`); err != nil {
		s.finishDurableBatch(batch, nil, nil, nil, nil, tick, err)
		return
	}
	results := make([]uint64, len(batch))
	latitudes := make([]float64, len(batch))
	longitudes := make([]float64, len(batch))
	applied := make([]bool, len(batch))
	for index, queued := range batch {
		var lockedServer string
		err = tx.QueryRowContext(queryCtx, `
			SELECT server_id FROM tcp_server_assignment
			WHERE server_id = $1 AND owner_instance_id = $2 AND generation = $3
				AND lease_until > NOW()
			FOR UPDATE`, queued.assignment.ServerID, s.instanceID, queued.assignment.Generation).Scan(&lockedServer)
		if err != nil {
			break
		}
		_, err = tx.ExecContext(queryCtx, `
			INSERT INTO client_state (client_uid) VALUES ($1)
			ON CONFLICT (client_uid) DO NOTHING`, queued.request.ClientUID)
		if err != nil {
			break
		}
		err = tx.QueryRowContext(queryCtx, `
			SELECT resulting_counter, latitude, longitude FROM client_operation
			WHERE client_uid = $1 AND operation_id = $2`,
			queued.request.ClientUID, queued.request.OperationID).
			Scan(&results[index], &latitudes[index], &longitudes[index])
		if err == nil {
			continue
		}
		if !errors.Is(err, sql.ErrNoRows) {
			break
		}
		err = tx.QueryRowContext(queryCtx, `
			UPDATE client_state SET counter = counter + 1, updated_at = NOW()
			WHERE client_uid = $1
			RETURNING counter, latitude, longitude`, queued.request.ClientUID).
			Scan(&results[index], &latitudes[index], &longitudes[index])
		if err != nil {
			break
		}
		_, err = tx.ExecContext(queryCtx, `
			INSERT INTO client_operation (client_uid, operation_id, operation_type, resulting_counter, latitude, longitude)
			VALUES ($1, $2, $3, $4, $5, $6)`, queued.request.ClientUID, queued.request.OperationID,
			queued.request.Kind, results[index], latitudes[index], longitudes[index])
		if err != nil {
			break
		}
		applied[index] = true
	}
	if err == nil {
		err = tx.Commit()
	}
	s.finishDurableBatch(batch, results, latitudes, longitudes, applied, tick, err)
}

func (s *server) finishDurableBatch(batch []durableCommand, counters []uint64, latitudes, longitudes []float64, applied []bool, tick uint64, err error) {
	for index, queued := range batch {
		result := durableResult{tick: tick, err: err}
		if err == nil {
			result.counter = counters[index]
			result.latitude = latitudes[index]
			result.longitude = longitudes[index]
			if applied[index] {
				s.entityMu.Lock()
				if entity := s.entities[queued.request.ClientUID]; entity != nil {
					entity.counter = result.counter
				} else {
					s.entities[queued.request.ClientUID] = &entityState{latitude: result.latitude, longitude: result.longitude, counter: result.counter}
				}
				s.entityMu.Unlock()
			}
		}
		queued.result <- result
	}
}

func (s *server) applyTransientInputs(tick uint64) {
	commands := make([]inputCommand, 0, 256)
	for len(commands) < 4096 {
		select {
		case command := <-s.inputs:
			commands = append(commands, command)
		default:
			goto drained
		}
	}

drained:
	s.entityMu.Lock()
	for _, command := range commands {
		entity := s.entities[command.intent.ClientUID]
		if entity != nil && (command.intent.Sequence > entity.sequence || (command.intent.Resume && command.intent.Sequence >= entity.sequence)) {
			entity.sequence = command.intent.Sequence
			entity.lastInputTick = tick
			if command.intent.Teleport {
				entity.latitude = command.intent.Latitude
				entity.longitude = command.intent.Longitude
				entity.axisX = 0
				entity.axisY = 0
			} else {
				magnitude := math.Hypot(command.intent.X, command.intent.Y)
				if magnitude > 1 {
					entity.axisX = command.intent.X / magnitude
					entity.axisY = command.intent.Y / magnitude
				} else {
					entity.axisX = command.intent.X
					entity.axisY = command.intent.Y
				}
			}
		}
	}
	distance := movementSpeed * s.tickInterval.Seconds()
	for _, entity := range s.entities {
		if tick-entity.lastInputTick > 4 {
			entity.axisX = 0
			entity.axisY = 0
		}
		entity.latitude = math.Max(-90, math.Min(90, entity.latitude+entity.axisY*distance))
		entity.longitude += entity.axisX * distance
		if entity.longitude > 180 {
			entity.longitude -= 360
		} else if entity.longitude < -180 {
			entity.longitude += 360
		}
	}
	current := s.currentAssignment()
	if current != nil {
		for _, entity := range s.entities {
			if entity.reroute == "" {
				destination := nearestLocation(entity.latitude, entity.longitude)
				if destination != current.Location {
					entity.reroute = destination
					entity.axisX = 0
					entity.axisY = 0
				}
			}
		}
	}
	handoffs := make(map[string]string)
	for _, command := range commands {
		if entity := s.entities[command.intent.ClientUID]; entity != nil {
			reroute := entity.reroute
			if reroute != "" {
				handoffs[command.intent.ClientUID] = reroute
			}
			command.result <- inputResult{
				latitude: entity.latitude, longitude: entity.longitude, counter: entity.counter,
				sequence: entity.sequence, tick: tick, claim: command.claim, reroute: reroute,
			}
		}
	}
	for clientUID := range handoffs {
		delete(s.entities, clientUID)
	}
	s.entityMu.Unlock()
}

func nearestLocation(latitude, longitude float64) string {
	best := ""
	bestDistance := math.Inf(1)
	latitudeRadians := latitude * math.Pi / 180
	for _, location := range locations {
		locationLatitude := location.latitude * math.Pi / 180
		deltaLatitude := locationLatitude - latitudeRadians
		deltaLongitude := (location.longitude - longitude) * math.Pi / 180
		a := math.Sin(deltaLatitude/2)*math.Sin(deltaLatitude/2) +
			math.Cos(latitudeRadians)*math.Cos(locationLatitude)*math.Sin(deltaLongitude/2)*math.Sin(deltaLongitude/2)
		a = math.Max(0, math.Min(1, a))
		distance := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
		if distance < bestDistance || (distance == bestDistance && location.location < best) {
			bestDistance = distance
			best = location.location
		}
	}
	return best
}

func (s *server) ensureEntity(ctx context.Context, clientUID string, refreshCounter bool) (bool, error) {
	s.entityMu.Lock()
	_, exists := s.entities[clientUID]
	s.entityMu.Unlock()
	if exists && !refreshCounter {
		return false, nil
	}
	loaded := &entityState{}
	queryCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	err := s.db.QueryRowContext(queryCtx, `
		SELECT COALESCE(client.counter, 0), COALESCE(entity.latitude, 0), COALESCE(entity.longitude, 0)
		FROM (SELECT $1::TEXT AS entity_uid) AS requested
		LEFT JOIN client_state AS client ON client.client_uid = requested.entity_uid
		LEFT JOIN entity_state AS entity ON entity.entity_uid = requested.entity_uid`, clientUID).
		Scan(&loaded.counter, &loaded.latitude, &loaded.longitude)
	if err != nil {
		return false, err
	}
	s.entityMu.Lock()
	if entity, exists := s.entities[clientUID]; exists {
		if refreshCounter {
			entity.counter = loaded.counter
		}
	} else {
		s.entities[clientUID] = loaded
	}
	s.entityMu.Unlock()
	return !exists, nil
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
			latitude DOUBLE PRECISION NOT NULL DEFAULT 0,
			longitude DOUBLE PRECISION NOT NULL DEFAULT 0,
			counter BIGINT NOT NULL DEFAULT 0 CHECK (counter >= 0),
			updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);
		CREATE TABLE IF NOT EXISTS tcp_server_assignment (
			server_id TEXT PRIMARY KEY REFERENCES tcp_server_state(server_id),
			owner_instance_id TEXT,
			generation BIGINT NOT NULL DEFAULT 0,
			lease_until TIMESTAMPTZ
		);
		CREATE TABLE IF NOT EXISTS client_state (
			client_uid TEXT PRIMARY KEY,
			counter BIGINT NOT NULL DEFAULT 0 CHECK (counter >= 0),
			latitude DOUBLE PRECISION NOT NULL DEFAULT 0,
			longitude DOUBLE PRECISION NOT NULL DEFAULT 0,
			updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);
		CREATE TABLE IF NOT EXISTS entity_state (
			entity_uid TEXT PRIMARY KEY,
			server_id TEXT NOT NULL REFERENCES tcp_server_state(server_id),
			location TEXT NOT NULL,
			latitude DOUBLE PRECISION NOT NULL,
			longitude DOUBLE PRECISION NOT NULL,
			server_generation BIGINT NOT NULL,
			entity_generation BIGINT NOT NULL DEFAULT 1,
			updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);
		CREATE TABLE IF NOT EXISTS client_operation (
			client_uid TEXT NOT NULL REFERENCES client_state(client_uid),
			operation_id TEXT NOT NULL,
			operation_type TEXT NOT NULL DEFAULT 'increment',
			resulting_counter BIGINT NOT NULL CHECK (resulting_counter >= 0),
			latitude DOUBLE PRECISION NOT NULL,
			longitude DOUBLE PRECISION NOT NULL,
			committed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
			PRIMARY KEY (client_uid, operation_id)
		);
		CREATE UNIQUE INDEX IF NOT EXISTS tcp_server_state_location
			ON tcp_server_state (location);
		ALTER TABLE tcp_server_state ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION NOT NULL DEFAULT 0;
		ALTER TABLE tcp_server_state ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION NOT NULL DEFAULT 0;
		ALTER TABLE client_operation ADD COLUMN IF NOT EXISTS operation_type TEXT NOT NULL DEFAULT 'increment';
		ALTER TABLE client_operation ALTER COLUMN operation_type SET DEFAULT 'increment'`
	if _, err := db.ExecContext(ctx, schema); err != nil {
		return fmt.Errorf("create schema: %w", err)
	}
	for _, item := range locations {
		if _, err := db.ExecContext(ctx, `
			INSERT INTO tcp_server_state (server_id, location, latitude, longitude)
			VALUES ($1, $2, $3, $4)
			ON CONFLICT (server_id) DO UPDATE SET
				location = EXCLUDED.location,
				latitude = EXCLUDED.latitude,
				longitude = EXCLUDED.longitude`,
			item.serverID, item.location, item.latitude, item.longitude); err != nil {
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
		SELECT c.server_id, s.location, s.latitude, s.longitude,
			c.generation, c.lease_until
		FROM claimed c JOIN tcp_server_state s USING (server_id)`
	claimed := &assignment{}
	err := s.db.QueryRowContext(ctx, query, s.instanceID, s.leaseDuration.Seconds()).Scan(
		&claimed.ServerID, &claimed.Location, &claimed.Latitude, &claimed.Longitude,
		&claimed.Generation, &claimed.LeaseUntil,
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
			value["latitude"] = current.Latitude
			value["longitude"] = current.Longitude
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
		if message == "@discover" {
			current := s.currentAssignment()
			body := map[string]any{"status": "spare", "instance": s.podName}
			if current != nil {
				body["status"] = "active"
				body["server"] = current.ServerID
				body["location"] = current.Location
				body["latitude"] = current.Latitude
				body["longitude"] = current.Longitude
				body["generation"] = current.Generation
			}
			encoded, _ := json.Marshal(body)
			_, _ = writer.Write(append(encoded, '\n'))
			_ = writer.Flush()
			return
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
		if teleport, found, err := parseTeleport(message); found {
			if err != nil {
				_, _ = fmt.Fprintf(writer, `{"error":%q}`+"\n", err.Error())
				_ = writer.Flush()
				continue
			}
			if !s.handleInputCommand(ctx, writer, bound, teleport, logger) {
				return
			}
			continue
		}
		if resume, found, err := parseResume(message); found {
			if err != nil {
				_, _ = fmt.Fprintf(writer, `{"error":%q}`+"\n", err.Error())
				_ = writer.Flush()
				continue
			}
			if !s.handleInputCommand(ctx, writer, bound, resume, logger) {
				return
			}
			continue
		}
		if increment, found, err := parseIncrement(message); found {
			if err != nil {
				_, _ = fmt.Fprintf(writer, `{"error":%q}`+"\n", err.Error())
				_ = writer.Flush()
				continue
			}
			if !s.handleDurableCommand(ctx, writer, bound, increment, logger) {
				return
			}
			continue
		}
		if intent, found, err := parseInput(message); found {
			if err != nil {
				_, _ = fmt.Fprintf(writer, `{"error":%q}`+"\n", err.Error())
				_ = writer.Flush()
				continue
			}
			if !s.handleInputCommand(ctx, writer, bound, intent, logger) {
				return
			}
			continue
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

func (s *server) handleInputCommand(ctx context.Context, writer *bufio.Writer, bound *assignment, intent inputIntent, logger *slog.Logger) bool {
	created, err := s.ensureEntity(ctx, intent.ClientUID, intent.Teleport)
	if err != nil {
		s.writeDatabaseError(writer, logger, err)
		return true
	}
	command := inputCommand{
		intent: intent,
		claim:  created || intent.Teleport,
		result: make(chan inputResult, 1),
	}
	select {
	case s.inputs <- command:
	case <-ctx.Done():
		return false
	default:
		_, _ = fmt.Fprintln(writer, `{"error":"input queue full"}`)
		_ = writer.Flush()
		return true
	}
	select {
	case result := <-command.result:
		if result.err != nil {
			s.writeDatabaseError(writer, logger, result.err)
			return true
		}
		if result.claim {
			if err := s.storeEntityClaim(ctx, bound, intent.ClientUID, result.latitude, result.longitude); err != nil {
				s.writeDatabaseError(writer, logger, err)
				return true
			}
		}
		return s.writeInputResponse(writer, bound, intent, result)
	case <-ctx.Done():
		return false
	}
}

func (s *server) storeEntityClaim(ctx context.Context, current *assignment, entityUID string, latitude, longitude float64) error {
	queryCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	result, err := s.db.ExecContext(queryCtx, `
		INSERT INTO entity_state (
			entity_uid, server_id, location, latitude, longitude, server_generation
		)
		SELECT $1, assignment.server_id, state.location, $2, $3, assignment.generation
		FROM tcp_server_assignment AS assignment
		JOIN tcp_server_state AS state USING (server_id)
		WHERE assignment.server_id = $4
			AND assignment.owner_instance_id = $5
			AND assignment.generation = $6
			AND assignment.lease_until > NOW()
		ON CONFLICT (entity_uid) DO UPDATE SET
			server_id = EXCLUDED.server_id,
			location = EXCLUDED.location,
			latitude = EXCLUDED.latitude,
			longitude = EXCLUDED.longitude,
			server_generation = EXCLUDED.server_generation,
			entity_generation = entity_state.entity_generation + 1,
			updated_at = NOW()`,
		entityUID, latitude, longitude, current.ServerID, s.instanceID, current.Generation)
	if err != nil {
		return err
	}
	rows, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if rows != 1 {
		return errors.New("server lost ownership before entity claim")
	}
	return nil
}

func (s *server) handleDurableCommand(ctx context.Context, writer *bufio.Writer, bound *assignment, request durableRequest, logger *slog.Logger) bool {
	command := durableCommand{assignment: *bound, request: request, result: make(chan durableResult, 1)}
	select {
	case s.durable <- command:
	case <-ctx.Done():
		return false
	default:
		_, _ = fmt.Fprintln(writer, `{"error":"durable command queue full"}`)
		_ = writer.Flush()
		return true
	}
	select {
	case result := <-command.result:
		if result.err != nil {
			s.writeDatabaseError(writer, logger, result.err)
			return true
		}
		return s.writeDurableResponse(writer, bound, request, result)
	case <-ctx.Done():
		return false
	}
}

func parseTeleport(message string) (inputIntent, bool, error) {
	return parseAbsoluteInput(message, "@teleport ", false)
}

func parseResume(message string) (inputIntent, bool, error) {
	return parseAbsoluteInput(message, "@resume ", true)
}

func parseAbsoluteInput(message, prefix string, resume bool) (inputIntent, bool, error) {
	arguments, found := strings.CutPrefix(message, prefix)
	if !found {
		return inputIntent{}, false, nil
	}
	fields := strings.Fields(arguments)
	if len(fields) != 4 || !validIdentifier(fields[0]) || !validIdentifier(fields[1]) {
		return inputIntent{}, true, errors.New("teleport requires client UID, sequence, latitude, and longitude")
	}
	sequence, sequenceErr := strconv.ParseUint(fields[1], 10, 64)
	latitude, latitudeErr := strconv.ParseFloat(fields[2], 64)
	longitude, longitudeErr := strconv.ParseFloat(fields[3], 64)
	if sequenceErr != nil || latitudeErr != nil || longitudeErr != nil || math.IsNaN(latitude) || math.IsNaN(longitude) ||
		math.IsInf(latitude, 0) || math.IsInf(longitude, 0) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180 {
		return inputIntent{}, true, errors.New("teleport coordinates are invalid")
	}
	return inputIntent{ClientUID: fields[0], Sequence: sequence, Teleport: true, Resume: resume, Latitude: latitude, Longitude: longitude}, true, nil
}

func parseIncrement(message string) (durableRequest, bool, error) {
	arguments, found := strings.CutPrefix(message, "@increment ")
	if !found {
		return durableRequest{}, false, nil
	}
	fields := strings.Fields(arguments)
	if len(fields) != 2 || !validIdentifier(fields[0]) || !validIdentifier(fields[1]) {
		return durableRequest{}, true, errors.New("increment requires client UID and operation ID")
	}
	return durableRequest{Kind: "increment", ClientUID: fields[0], OperationID: fields[1]}, true, nil
}

func parseInput(message string) (inputIntent, bool, error) {
	arguments, found := strings.CutPrefix(message, "@input ")
	if !found {
		return inputIntent{}, false, nil
	}
	fields := strings.Fields(arguments)
	if len(fields) != 4 || !validIdentifier(fields[0]) {
		return inputIntent{}, true, errors.New("input requires client UID, sequence, x axis, and y axis")
	}
	sequence, sequenceErr := strconv.ParseUint(fields[1], 10, 64)
	x, xErr := strconv.ParseFloat(fields[2], 64)
	y, yErr := strconv.ParseFloat(fields[3], 64)
	if sequenceErr != nil || xErr != nil || yErr != nil || math.IsNaN(x) || math.IsNaN(y) ||
		math.IsInf(x, 0) || math.IsInf(y, 0) || x < -1 || x > 1 || y < -1 || y > 1 {
		return inputIntent{}, true, errors.New("input axes must be finite values from -1 to 1")
	}
	return inputIntent{ClientUID: fields[0], Sequence: sequence, X: x, Y: y}, true, nil
}

func validIdentifier(value string) bool {
	if value == "" || len(value) > 128 {
		return false
	}
	for _, character := range value {
		if !((character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
			(character >= '0' && character <= '9') || strings.ContainsRune("-_.:", character)) {
			return false
		}
	}
	return true
}

func (s *server) writeDurableResponse(writer *bufio.Writer, current *assignment, command durableRequest, result durableResult) bool {
	message := command.Kind
	body, err := json.Marshal(response{
		Server: current.ServerID, Location: current.Location,
		Latitude: current.Latitude, Longitude: current.Longitude, Instance: s.podName,
		Generation: current.Generation, Counter: result.counter, Message: message,
		Time: time.Now().UTC().Format(time.RFC3339Nano), Tick: result.tick,
		ClientUID: command.ClientUID, OperationID: command.OperationID,
		ClientLatitude: result.latitude, ClientLongitude: result.longitude,
	})
	if err != nil {
		return false
	}
	if _, err := writer.Write(append(body, '\n')); err != nil {
		return false
	}
	return writer.Flush() == nil
}

func (s *server) writeInputResponse(writer *bufio.Writer, current *assignment, intent inputIntent, result inputResult) bool {
	message := "state"
	if intent.Teleport {
		message = "teleport"
	}
	body, err := json.Marshal(response{
		Server: current.ServerID, Location: current.Location,
		Latitude: current.Latitude, Longitude: current.Longitude, Instance: s.podName,
		Generation: current.Generation, Counter: result.counter, Message: message,
		Time: time.Now().UTC().Format(time.RFC3339Nano), Tick: result.tick,
		ClientUID: intent.ClientUID, ClientLatitude: result.latitude,
		ClientLongitude: result.longitude, InputSequence: result.sequence, Reroute: result.reroute,
	})
	if err != nil {
		return false
	}
	if _, err := writer.Write(append(body, '\n')); err != nil {
		return false
	}
	return writer.Flush() == nil
}

func (s *server) writeResponse(writer *bufio.Writer, current *assignment, counter uint64, message string) bool {
	body, err := json.Marshal(response{
		Server: current.ServerID, Location: current.Location,
		Latitude: current.Latitude, Longitude: current.Longitude,
		Instance:   s.podName,
		Generation: current.Generation, Counter: counter, Message: message,
		Time: time.Now().UTC().Format(time.RFC3339Nano), Tick: s.tick.Load(),
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
