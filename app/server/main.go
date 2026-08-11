package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/binary"
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

	"github.com/redis/go-redis/v9"
)

const (
	defaultLeaseDuration    = 3 * time.Second
	defaultRenewInterval    = 500 * time.Millisecond
	defaultTickInterval     = 50 * time.Millisecond
	movementPixelsPerSecond = 120.0
	minimumViewZoom         = 2.0
	maximumViewZoom         = 18.0
	inputTimeoutTicks       = 8
	visibilityActiveTicks   = 20
	visibilityRadiusPixels  = 250.0
	entityCleanupTicks      = 600
	mercatorLatitudeLimit   = 85.05112878
)

type locationDefinition struct {
	serverID   string
	locationID int64
	latitude   float64
	longitude  float64
	generation int64
}

type assignment struct {
	ServerID   string
	LocationID int64
	Latitude   float64
	Longitude  float64
	Generation int64
	LeaseUntil time.Time
}

type server struct {
	instanceID               string
	podName                  string
	routeAddress             string
	valkey                   *redis.ClusterClient
	leaseDuration            time.Duration
	renewInterval            time.Duration
	tickInterval             time.Duration
	tick                     atomic.Uint64
	nextRoute                atomic.Uint64
	inputs                   chan inputCommand
	entityMu                 sync.Mutex
	entities                 map[string]*entityState
	remoteEntityMu           sync.RWMutex
	remoteEntities           map[spatialCell]map[int64]visibilityCellSnapshot
	topologyMu               sync.RWMutex
	topology                 []locationDefinition
	locationIndex            *locationNode
	visibilityIndex          *visibilityLocationNode
	topologyRevision         atomic.Int64
	mu                       sync.RWMutex
	assignment               *assignment
	activeConnections        atomic.Int64
	acceptedConnections      atomic.Uint64
	inputCommands            atomic.Uint64
	inputQueueFull           atomic.Uint64
	handoffAttempts          atomic.Uint64
	handoffFailures          atomic.Uint64
	stateErrors              atomic.Uint64
	tickDurationBits         atomic.Uint64
	maxTickDurationBits      atomic.Uint64
	visibilityManifestReads  atomic.Int64
	visibilitySnapshotReads  atomic.Int64
	visibilityRemoteEntities atomic.Int64
	visibilityDurationBits   atomic.Uint64
	visibilityFailures       atomic.Uint64
}

type response struct {
	Server          string          `json:"server"`
	LocationID      int64           `json:"location_id"`
	Latitude        float64         `json:"latitude"`
	Longitude       float64         `json:"longitude"`
	Instance        string          `json:"instance"`
	Generation      int64           `json:"generation"`
	Message         string          `json:"message"`
	Time            string          `json:"time"`
	Tick            uint64          `json:"tick"`
	ClientUID       string          `json:"client_uid,omitempty"`
	ClientLatitude  float64         `json:"client_latitude"`
	ClientLongitude float64         `json:"client_longitude"`
	InputSequence   uint64          `json:"input_sequence,omitempty"`
	Reroute         int64           `json:"reroute,omitempty"`
	EntityCount     int             `json:"entity_count"`
	Entities        []visibleEntity `json:"entities,omitempty"`
}

type visibleEntity struct {
	UID       string  `json:"uid"`
	Latitude  float64 `json:"latitude"`
	Longitude float64 `json:"longitude"`
	Sequence  uint64  `json:"sequence"`
}

type inputIntent struct {
	ClientUID            string
	Sequence             uint64
	X                    float64
	Y                    float64
	Zoom                 float64
	ServerRelevance      bool
	SpatialRelevance     bool
	CrossServerRelevance bool
	Teleport             bool
	Resume               bool
	Latitude             float64
	Longitude            float64
}

type inputCommand struct {
	intent inputIntent
	claim  bool
	result chan inputResult
}

type inputResult struct {
	latitude  float64
	longitude float64
	sequence  uint64
	tick      uint64
	zoom      float64
	claim     bool
	err       error
	reroute   int64
}

type entityState struct {
	latitude            float64
	longitude           float64
	sequence            uint64
	axisX               float64
	axisY               float64
	viewZoom            float64
	serverRelevant      bool
	crossServerRelevant bool
	lastInputTick       uint64
	reroute             int64
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	podName := envOrDefault("POD_NAME", hostname())
	instanceID := envOrDefault("POD_UID", podName)
	cache, err := connectValkey(ctx, logger)
	if err != nil {
		logger.Error("connect valkey cluster", "error", err)
		os.Exit(1)
	}
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
		instanceID: instanceID, podName: podName,
		routeAddress:  net.JoinHostPort(envOrDefault("POD_IP", hostname()), envOrDefault("SERVER_PORT", "7000")),
		valkey:        cache,
		leaseDuration: leaseDuration, renewInterval: renewInterval, tickInterval: tickInterval,
		inputs: make(chan inputCommand, 8192), entities: make(map[string]*entityState),
		remoteEntities: make(map[spatialCell]map[int64]visibilityCellSnapshot),
	}
	if err := s.refreshTopology(ctx); err != nil {
		logger.Error("load location topology", "error", err)
		os.Exit(1)
	}
	go s.manageAssignment(ctx, logger)
	go s.runTopologyRefresh(ctx, logger)
	go s.heartbeat(ctx, logger)
	go s.runTicks(ctx)
	go s.runVisibilityExchange(ctx)
	go s.serveMetrics(ctx, envOrDefault("STATS_ADDR", ":8405"), logger)

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
		s.acceptedConnections.Add(1)
		s.activeConnections.Add(1)
		go func() {
			defer connections.Done()
			defer s.activeConnections.Add(-1)
			s.handleConnection(ctx, conn, logger)
		}()
	}

	connections.Wait()
	cleanupCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	s.release(cleanupCtx)
	_ = s.valkey.Del(cleanupCtx, s.presenceKey()).Err()
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
			started := time.Now()
			currentTick := s.tick.Add(1)
			s.applyTransientInputs(currentTick)
			s.recordTickDuration(time.Since(started))
		}
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
				entity.viewZoom = command.intent.Zoom
				entity.serverRelevant = command.intent.ServerRelevance
				entity.crossServerRelevant = command.intent.CrossServerRelevance
			}
		}
	}
	for uid, entity := range s.entities {
		if tick-entity.lastInputTick >= entityCleanupTicks {
			delete(s.entities, uid)
			continue
		}
		if tick-entity.lastInputTick >= inputTimeoutTicks {
			entity.axisX = 0
			entity.axisY = 0
		}
		latitude := math.Max(-mercatorLatitudeLimit, math.Min(mercatorLatitudeLimit, entity.latitude))
		worldPixels := 256 * math.Exp2(entity.viewZoom)
		screenDistance := movementPixelsPerSecond * s.tickInterval.Seconds()
		longitudeDistance := screenDistance / worldPixels * 360
		projectedDistance := screenDistance / worldPixels * 2 * math.Pi
		projectedY := math.Log(math.Tan(math.Pi/4 + latitude*math.Pi/360))
		projectedY += entity.axisY * projectedDistance
		if projectedY > math.Pi {
			projectedY -= 2 * math.Pi
		} else if projectedY < -math.Pi {
			projectedY += 2 * math.Pi
		}
		entity.latitude = math.Atan(math.Sinh(projectedY)) * 180 / math.Pi
		entity.longitude += entity.axisX * longitudeDistance
		if entity.longitude > 180 {
			entity.longitude -= 360
		} else if entity.longitude < -180 {
			entity.longitude += 360
		}
	}
	current := s.currentAssignment()
	if current != nil {
		for _, entity := range s.entities {
			if entity.reroute == 0 {
				destination := s.nearestLocation(entity.latitude, entity.longitude)
				if destination != 0 && destination != current.LocationID {
					entity.reroute = destination
					entity.axisX = 0
					entity.axisY = 0
				}
			}
		}
	}
	handoffs := make(map[string]int64)
	for _, command := range commands {
		if entity := s.entities[command.intent.ClientUID]; entity != nil {
			reroute := entity.reroute
			if reroute != 0 {
				handoffs[command.intent.ClientUID] = reroute
			}
			command.result <- inputResult{
				latitude: entity.latitude, longitude: entity.longitude,
				sequence: entity.sequence, tick: tick, zoom: entity.viewZoom, claim: command.claim, reroute: reroute,
			}
		}
	}
	for clientUID := range handoffs {
		delete(s.entities, clientUID)
	}
	s.entityMu.Unlock()
}

func (s *server) nearestLocation(latitude, longitude float64) int64 {
	s.topologyMu.RLock()
	defer s.topologyMu.RUnlock()
	return s.locationIndex.nearest(latitude, longitude, 0)
}

func (s *server) refreshTopology(ctx context.Context) error {
	loaded, revision, err := s.loadTopology(ctx)
	if err != nil {
		return err
	}
	if current := s.currentAssignment(); current != nil {
		enabled := false
		for _, item := range loaded {
			if item.locationID == current.LocationID {
				enabled = true
				break
			}
		}
		if !enabled {
			s.clearAssignment(current.Generation)
		}
	}
	s.topologyMu.Lock()
	s.topology = loaded
	s.locationIndex = buildLocationIndex(loaded)
	s.visibilityIndex = buildVisibilityLocationIndex(loaded)
	s.topologyMu.Unlock()
	s.topologyRevision.Store(revision)
	return nil
}

func (s *server) runTopologyRefresh(ctx context.Context, logger *slog.Logger) {
	updates := s.valkey.Subscribe(ctx, topologyChannel)
	defer updates.Close()
	updateChannel := updates.Channel()
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case _, open := <-updateChannel:
			if !open {
				return
			}
			if err := s.refreshTopology(ctx); err != nil && ctx.Err() == nil {
				logger.Warn("refresh changed topology", "error", err)
			}
		case <-ticker.C:
			revision, err := s.readTopologyRevision(ctx)
			if err != nil {
				if ctx.Err() == nil {
					logger.Warn("check topology revision", "error", err)
				}
			} else if revision != s.topologyRevision.Load() {
				if err := s.refreshTopology(ctx); err != nil && ctx.Err() == nil {
					logger.Warn("reconcile location topology", "error", err)
				}
			}
		}
	}
}

func (s *server) ensureEntity(ctx context.Context, clientUID string) (bool, error) {
	s.entityMu.Lock()
	_, exists := s.entities[clientUID]
	s.entityMu.Unlock()
	if exists {
		return false, nil
	}
	loaded, persisted, err := s.loadEntity(ctx, clientUID)
	if err != nil {
		return false, err
	}
	if !persisted {
		loaded.latitude, loaded.longitude, err = randomCoordinate()
		if err != nil {
			return false, err
		}
	}
	s.entityMu.Lock()
	if _, exists := s.entities[clientUID]; !exists {
		s.entities[clientUID] = loaded
	}
	s.entityMu.Unlock()
	return !exists, nil
}

func randomCoordinate() (float64, float64, error) {
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		return 0, 0, err
	}
	unit := func(value uint64) float64 {
		return float64(value>>11) / float64(uint64(1)<<53)
	}
	latitude := (unit(binary.LittleEndian.Uint64(bytes[:8]))*2 - 1) * mercatorLatitudeLimit
	longitude := unit(binary.LittleEndian.Uint64(bytes[8:]))*360 - 180
	return latitude, longitude, nil
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
				if err := s.publishRoute(ctx, claimed); err != nil && ctx.Err() == nil {
					logger.Warn("publish route", "instance", s.podName, "error", err)
				}
				logger.Info("location claimed", "instance", s.podName, "server", claimed.ServerID, "location_id", claimed.LocationID, "generation", claimed.Generation)
			}
		} else if renewed, err := s.renew(operationCtx, current); err != nil {
			if time.Now().After(current.LeaseUntil) {
				s.clearAssignment(current.Generation)
				logger.Warn("location lease lost", "instance", s.podName, "server", current.ServerID, "generation", current.Generation)
			}
		} else {
			s.setAssignment(renewed)
			if err := s.publishRoute(ctx, renewed); err != nil && ctx.Err() == nil {
				logger.Warn("publish route", "instance", s.podName, "error", err)
			}
		}
		cancel()

		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *server) heartbeat(ctx context.Context, logger *slog.Logger) {
	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()
	for {
		value := map[string]any{"instance": s.podName, "status": "unassigned"}
		if current := s.currentAssignment(); current != nil {
			value["status"] = "active"
			value["server"] = current.ServerID
			value["location_id"] = current.LocationID
			value["latitude"] = current.Latitude
			value["longitude"] = current.Longitude
			value["generation"] = current.Generation
			value["address"] = s.routeAddress
		}
		body, _ := json.Marshal(value)
		cacheCtx, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
		err := s.valkey.Set(cacheCtx, s.presenceKey(), body, 10*time.Second).Err()
		cancel()
		if err != nil && ctx.Err() == nil {
			logger.Warn("refresh valkey presence", "instance", s.podName, "error", err)
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
		if requested, found := strings.CutPrefix(message, "@route "); found {
			route, err := s.resolveRoute(ctx, strings.TrimSpace(requested))
			if err != nil {
				_, _ = fmt.Fprintf(writer, `{"error":%q}`+"\n", err.Error())
			} else {
				_ = json.NewEncoder(writer).Encode(route)
			}
			_ = writer.Flush()
			return
		}
		if message == "@routes" {
			routes, err := s.resolveRoutes(ctx)
			if err != nil {
				_, _ = fmt.Fprintf(writer, `{"error":%q}`+"\n", err.Error())
			} else {
				_ = json.NewEncoder(writer).Encode(map[string]any{"routes": routes})
			}
			_ = writer.Flush()
			return
		}
		if arguments, found := strings.CutPrefix(message, "@nearest "); found {
			latitude, longitude, excludedLocation, err := parseRouteCoordinates(arguments)
			if err == nil {
				var route routeRecord
				route, err = s.nearestRoute(ctx, latitude, longitude, excludedLocation)
				if err == nil {
					_ = json.NewEncoder(writer).Encode(route)
				}
			}
			if err != nil {
				_, _ = fmt.Fprintf(writer, `{"error":%q}`+"\n", err.Error())
			}
			_ = writer.Flush()
			return
		}

		current := s.currentAssignment()
		if requested, found := strings.CutPrefix(message, "@location "); found {
			if current == nil || (requested != "any" && requested != strconv.FormatInt(current.LocationID, 10)) {
				_, _ = fmt.Fprintln(writer, `{"error":"location unavailable"}`)
				_ = writer.Flush()
				return
			}
			bound = current
			if !s.writeResponse(writer, bound, "connected") {
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
		if !s.writeResponse(writer, bound, message) {
			return
		}
	}
	if err := scanner.Err(); err != nil {
		logger.Debug("connection read ended", "instance", s.podName, "remote", remote, "error", err)
	}
}

func (s *server) handleInputCommand(ctx context.Context, writer *bufio.Writer, bound *assignment, intent inputIntent, logger *slog.Logger) bool {
	s.inputCommands.Add(1)
	created, err := s.ensureEntity(ctx, intent.ClientUID)
	if err != nil {
		s.writeStateError(writer, logger, err)
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
		s.inputQueueFull.Add(1)
		_, _ = fmt.Fprintln(writer, `{"error":"input queue full"}`)
		_ = writer.Flush()
		return true
	}
	select {
	case result := <-command.result:
		if result.err != nil {
			s.writeStateError(writer, logger, result.err)
			return true
		}
		if result.claim {
			if err := s.storeEntityClaim(ctx, bound, intent.ClientUID, result.latitude, result.longitude); err != nil {
				s.writeStateError(writer, logger, err)
				return true
			}
		}
		if result.reroute != 0 {
			s.handoffAttempts.Add(1)
			if err := s.prepareEntityHandoff(ctx, bound, intent.ClientUID, result.reroute, result.latitude, result.longitude); err != nil {
				s.handoffFailures.Add(1)
				s.writeStateError(writer, logger, err)
				return true
			}
		}
		return s.writeInputResponse(writer, bound, intent, result)
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

func parseInput(message string) (inputIntent, bool, error) {
	arguments, found := strings.CutPrefix(message, "@input ")
	if !found {
		return inputIntent{}, false, nil
	}
	fields := strings.Fields(arguments)
	if (len(fields) != 5 && len(fields) != 7 && len(fields) != 8) || !validIdentifier(fields[0]) {
		return inputIntent{}, true, errors.New("input requires client UID, sequence, x axis, y axis, zoom, and optional relevance flags")
	}
	sequence, sequenceErr := strconv.ParseUint(fields[1], 10, 64)
	x, xErr := strconv.ParseFloat(fields[2], 64)
	y, yErr := strconv.ParseFloat(fields[3], 64)
	zoom, zoomErr := strconv.ParseFloat(fields[4], 64)
	if sequenceErr != nil || xErr != nil || yErr != nil || math.IsNaN(x) || math.IsNaN(y) ||
		zoomErr != nil || math.IsInf(x, 0) || math.IsInf(y, 0) || math.IsNaN(zoom) || math.IsInf(zoom, 0) ||
		x < -1 || x > 1 || y < -1 || y > 1 || zoom < minimumViewZoom || zoom > maximumViewZoom {
		return inputIntent{}, true, errors.New("input axes or zoom are outside their allowed ranges")
	}
	serverRelevance, spatialRelevance, crossServerRelevance := true, true, true
	if len(fields) >= 7 {
		var serverErr, spatialErr, crossServerErr error
		serverRelevance, serverErr = strconv.ParseBool(fields[5])
		spatialRelevance, spatialErr = strconv.ParseBool(fields[6])
		if len(fields) == 8 {
			crossServerRelevance, crossServerErr = strconv.ParseBool(fields[7])
		}
		if serverErr != nil || spatialErr != nil || crossServerErr != nil {
			return inputIntent{}, true, errors.New("input relevance flags must be true or false")
		}
	}
	return inputIntent{
		ClientUID: fields[0], Sequence: sequence, X: x, Y: y, Zoom: zoom,
		ServerRelevance: serverRelevance, SpatialRelevance: spatialRelevance,
		CrossServerRelevance: crossServerRelevance,
	}, true, nil
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

func (s *server) writeInputResponse(writer *bufio.Writer, current *assignment, intent inputIntent, result inputResult) bool {
	message := "state"
	if intent.Teleport {
		message = "teleport"
	}
	entities, entityCount := s.visibleEntities(
		intent.ClientUID, result.latitude, result.longitude, result.zoom,
		intent.ServerRelevance, intent.SpatialRelevance, intent.CrossServerRelevance,
	)
	body, err := json.Marshal(response{
		Server: current.ServerID, LocationID: current.LocationID,
		Latitude: current.Latitude, Longitude: current.Longitude, Instance: s.podName,
		Generation: current.Generation, Message: message,
		Time: time.Now().UTC().Format(time.RFC3339Nano), Tick: result.tick,
		ClientUID: intent.ClientUID, ClientLatitude: result.latitude,
		ClientLongitude: result.longitude, InputSequence: result.sequence, Reroute: result.reroute,
		EntityCount: entityCount, Entities: entities,
	})
	if err != nil {
		return false
	}
	if _, err := writer.Write(append(body, '\n')); err != nil {
		return false
	}
	return writer.Flush() == nil
}

func (s *server) writeResponse(writer *bufio.Writer, current *assignment, message string) bool {
	s.entityMu.Lock()
	entityCount := len(s.entities)
	s.entityMu.Unlock()
	body, err := json.Marshal(response{
		Server: current.ServerID, LocationID: current.LocationID,
		Latitude: current.Latitude, Longitude: current.Longitude,
		Instance:   s.podName,
		Generation: current.Generation, Message: message,
		Time: time.Now().UTC().Format(time.RFC3339Nano), Tick: s.tick.Load(),
		EntityCount: entityCount,
	})
	if err != nil {
		return false
	}
	if _, err := writer.Write(append(body, '\n')); err != nil {
		return false
	}
	return writer.Flush() == nil
}

func (s *server) writeStateError(writer *bufio.Writer, logger *slog.Logger, err error) {
	s.stateErrors.Add(1)
	logger.Error("authoritative state operation", "instance", s.podName, "error", err)
	_, _ = fmt.Fprintln(writer, `{"error":"authoritative state operation failed"}`)
	_ = writer.Flush()
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
	copy := *value
	s.assignment = &copy
	s.mu.Unlock()

	s.topologyMu.Lock()
	for index := range s.topology {
		if s.topology[index].serverID == value.ServerID {
			s.topology[index].generation = value.Generation
			break
		}
	}
	s.topologyMu.Unlock()
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
