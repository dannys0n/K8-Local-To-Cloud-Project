package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"net"
	"net/http"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const statsPage = `<!doctype html><html><head><meta charset="utf-8"><title>Gateway stats</title><style>body{background:#0d1117;color:#e6edf3;font:14px monospace;margin:2rem}pre{background:#161b22;border:1px solid #30363d;padding:1rem;overflow:auto}</style></head><body><h1>Gateway replica</h1><p>Read-only live state; refreshes every two seconds.</p><pre id="data">loading</pre><script>async function r(){let x=await fetch('/stats',{cache:'no-store'});document.querySelector('#data').textContent=JSON.stringify(await x.json(),null,2)}r();setInterval(r,2000)</script></body></html>`

type backend struct {
	Server     string  `json:"server"`
	LocationID int64   `json:"location_id"`
	Latitude   float64 `json:"latitude"`
	Longitude  float64 `json:"longitude"`
	Instance   string  `json:"instance"`
	Address    string  `json:"address"`
	Generation int64   `json:"generation"`
}

type discoveryResponse struct {
	Status     string  `json:"status"`
	Server     string  `json:"server"`
	LocationID int64   `json:"location_id"`
	Latitude   float64 `json:"latitude"`
	Longitude  float64 `json:"longitude"`
	Instance   string  `json:"instance"`
	Generation int64   `json:"generation"`
}

type publicLocation struct {
	Server     string  `json:"server"`
	LocationID int64   `json:"location_id"`
	Latitude   float64 `json:"latitude"`
	Longitude  float64 `json:"longitude"`
	Generation int64   `json:"generation"`
}

type session struct {
	ID          string    `json:"id"`
	Client      string    `json:"client"`
	ClientUID   string    `json:"client_uid,omitempty"`
	LocationID  int64     `json:"location_id"`
	Latitude    float64   `json:"latitude"`
	Longitude   float64   `json:"longitude"`
	Server      string    `json:"server"`
	Instance    string    `json:"instance"`
	Address     string    `json:"address"`
	Generation  int64     `json:"generation"`
	ConnectedAt time.Time `json:"connected_at"`
	Protocol    string    `json:"protocol"`
}

type gateway struct {
	instance         string
	serverHost       string
	serverPort       string
	discoveryEvery   time.Duration
	discoveryTimeout time.Duration
	routeTimeout     time.Duration
	backendTimeout   time.Duration
	logger           *slog.Logger
	mu               sync.RWMutex
	routes           map[string]backend
	sessions         map[string]session
	accepted         atomic.Uint64
	nextSession      atomic.Uint64
	nextAny          atomic.Uint64
}

type backendConnection struct {
	info   backend
	conn   net.Conn
	reader *bufio.Reader
	writer *bufio.Writer
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	discoveryEvery, err := durationFromEnv("DISCOVERY_INTERVAL", 500*time.Millisecond)
	if err != nil {
		logger.Error("invalid discovery interval", "error", err)
		os.Exit(1)
	}
	discoveryTimeout, err := durationFromEnv("DISCOVERY_TIMEOUT", 500*time.Millisecond)
	if err != nil {
		logger.Error("invalid discovery timeout", "error", err)
		os.Exit(1)
	}
	routeTimeout, err := durationFromEnv("ROUTE_TIMEOUT", 10*time.Second)
	if err != nil {
		logger.Error("invalid route timeout", "error", err)
		os.Exit(1)
	}
	backendTimeout, err := durationFromEnv("BACKEND_TIMEOUT", 3*time.Second)
	if err != nil {
		logger.Error("invalid backend timeout", "error", err)
		os.Exit(1)
	}

	g := &gateway{
		instance:       envOrDefault("POD_NAME", hostname()),
		serverHost:     envOrDefault("SERVER_HOST", "tcp-server-headless"),
		serverPort:     envOrDefault("SERVER_PORT", "7000"),
		discoveryEvery: discoveryEvery, discoveryTimeout: discoveryTimeout, routeTimeout: routeTimeout,
		backendTimeout: backendTimeout, logger: logger,
		routes: make(map[string]backend), sessions: make(map[string]session),
	}
	go g.discoverLoop(ctx)
	go g.serveHTTP(ctx, envOrDefault("STATS_ADDR", ":8404"))

	listener, err := net.Listen("tcp", envOrDefault("LISTEN_ADDR", ":9000"))
	if err != nil {
		logger.Error("listen", "error", err)
		os.Exit(1)
	}
	defer listener.Close()
	go func() { <-ctx.Done(); _ = listener.Close() }()
	logger.Info("gateway ready", "instance", g.instance, "server_host", g.serverHost)

	var clients sync.WaitGroup
	for {
		conn, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				break
			}
			logger.Warn("accept client", "error", err)
			continue
		}
		clients.Add(1)
		go func() {
			defer clients.Done()
			g.handleClient(ctx, conn)
		}()
	}
	clients.Wait()
}

func (g *gateway) handleClient(ctx context.Context, client net.Conn) {
	defer client.Close()
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			_ = client.Close()
		case <-done:
		}
	}()
	id := fmt.Sprintf("%s-%d", g.instance, g.nextSession.Add(1))
	g.accepted.Add(1)
	clientReader := bufio.NewReader(client)
	clientWriter := bufio.NewWriter(client)
	var downstream *backendConnection
	defer func() {
		if downstream != nil {
			downstream.conn.Close()
		}
		g.mu.Lock()
		delete(g.sessions, id)
		g.mu.Unlock()
	}()

	requested := ""
	clientLatitude := 0.0
	clientLongitude := 0.0
	for {
		line, err := clientReader.ReadString('\n')
		if err != nil {
			return
		}
		message := strings.TrimSpace(line)
		if message == "" {
			continue
		}
		if location, found := strings.CutPrefix(message, "@location "); found {
			location = strings.TrimSpace(location)
			candidate, hello, err := g.openRoute(ctx, location, nil)
			if err != nil {
				writeJSONError(clientWriter, err.Error())
				continue
			}
			if downstream != nil {
				downstream.conn.Close()
			}
			downstream = candidate
			requested = location
			clientLatitude = candidate.info.Latitude
			clientLongitude = candidate.info.Longitude
			g.updateSession(id, client.RemoteAddr().String(), candidate.info, clientLatitude, clientLongitude)
			hello = g.withGatewayMetadata(hello)
			if _, err := clientWriter.Write(hello); err != nil || clientWriter.Flush() != nil {
				return
			}
			continue
		}
		if latitude, longitude, found, err := parseTeleportRoute(message); found {
			if err != nil {
				writeJSONError(clientWriter, err.Error())
				continue
			}
			location, err := g.locationFor(ctx, latitude, longitude)
			if err != nil {
				writeJSONError(clientWriter, err.Error())
				continue
			}
			candidate, _, err := g.openRoute(ctx, location, nil)
			if err != nil {
				writeJSONError(clientWriter, err.Error())
				continue
			}
			response, err := g.exchange(candidate, message)
			if err != nil || responseHasError(response) {
				candidate.conn.Close()
				writeJSONError(clientWriter, "teleport failed")
				continue
			}
			if downstream != nil {
				downstream.conn.Close()
			}
			downstream = candidate
			requested = location
			clientLatitude = latitude
			clientLongitude = longitude
			g.updateSession(id, client.RemoteAddr().String(), candidate.info, clientLatitude, clientLongitude)
			response = g.withGatewayMetadata(response)
			g.updateSessionFromResponse(id, response)
			if _, err := clientWriter.Write(response); err != nil || clientWriter.Flush() != nil {
				return
			}
			continue
		}
		if latitude, longitude, found, err := parsePosition(message); found {
			if err != nil {
				writeJSONError(clientWriter, err.Error())
				continue
			}
			location, err := g.locationFor(ctx, latitude, longitude)
			if err != nil {
				writeJSONError(clientWriter, err.Error())
				continue
			}
			candidate, hello, err := g.openRoute(ctx, location, nil)
			if err != nil {
				writeJSONError(clientWriter, err.Error())
				continue
			}
			if downstream != nil {
				downstream.conn.Close()
			}
			downstream = candidate
			requested = location
			clientLatitude = latitude
			clientLongitude = longitude
			g.updateSession(id, client.RemoteAddr().String(), candidate.info, clientLatitude, clientLongitude)
			hello = g.withGatewayMetadata(hello)
			if _, err := clientWriter.Write(hello); err != nil || clientWriter.Flush() != nil {
				return
			}
			continue
		}
		if message == "@locations" {
			body, _ := json.Marshal(map[string]any{"gateway": g.instance, "locations": g.publicLocations()})
			if _, err := clientWriter.Write(append(body, '\n')); err != nil || clientWriter.Flush() != nil {
				return
			}
			continue
		}
		if downstream == nil {
			if requested == "" {
				writeJSONError(clientWriter, "send @location before application messages")
				continue
			}
			location, routeErr := g.locationFor(ctx, clientLatitude, clientLongitude)
			if routeErr != nil {
				writeJSONError(clientWriter, routeErr.Error())
				continue
			}
			candidate, _, routeErr := g.openRoute(ctx, location, nil)
			if routeErr != nil {
				writeJSONError(clientWriter, routeErr.Error())
				continue
			}
			downstream = candidate
			requested = location
			g.updateSession(id, client.RemoteAddr().String(), candidate.info, clientLatitude, clientLongitude)
		}

		response, err := g.exchange(downstream, message)
		if err != nil || responseHasError(response) {
			failed := downstream.info
			downstream.conn.Close()
			downstream = nil
			g.removeRoute(failed)
			candidate, _, routeErr := g.openRoute(ctx, requested, &failed)
			if routeErr != nil {
				location, nearestErr := g.locationFor(ctx, clientLatitude, clientLongitude)
				if nearestErr != nil {
					writeJSONError(clientWriter, nearestErr.Error())
					continue
				}
				candidate, _, routeErr = g.openRoute(ctx, location, nil)
				if routeErr != nil {
					writeJSONError(clientWriter, routeErr.Error())
					continue
				}
				requested = location
			}
			downstream = candidate
			g.updateSession(id, client.RemoteAddr().String(), candidate.info, clientLatitude, clientLongitude)
			response, err = g.exchange(downstream, message)
		}
		if err != nil {
			writeJSONError(clientWriter, "backend request failed")
			continue
		}
		var handoff struct {
			Reroute         int64   `json:"reroute"`
			ClientUID       string  `json:"client_uid"`
			InputSequence   uint64  `json:"input_sequence"`
			ClientLatitude  float64 `json:"client_latitude"`
			ClientLongitude float64 `json:"client_longitude"`
		}
		decodedHandoff := json.Unmarshal(response, &handoff) == nil
		if decodedHandoff && handoff.ClientUID != "" {
			clientLatitude = handoff.ClientLatitude
			clientLongitude = handoff.ClientLongitude
		}
		if decodedHandoff && handoff.Reroute != 0 {
			nextLocation := strconv.FormatInt(handoff.Reroute, 10)
			candidate, _, routeErr := g.openRoute(ctx, nextLocation, nil)
			if routeErr != nil {
				writeJSONError(clientWriter, routeErr.Error())
				continue
			}
			resume := fmt.Sprintf("@resume %s %d %.8f %.8f", handoff.ClientUID, handoff.InputSequence, handoff.ClientLatitude, handoff.ClientLongitude)
			resumed, resumeErr := g.exchange(candidate, resume)
			if resumeErr != nil || responseHasError(resumed) {
				candidate.conn.Close()
				writeJSONError(clientWriter, "server handoff failed")
				continue
			}
			downstream.conn.Close()
			downstream = candidate
			requested = nextLocation
			clientLatitude = handoff.ClientLatitude
			clientLongitude = handoff.ClientLongitude
			g.updateSession(id, client.RemoteAddr().String(), candidate.info, clientLatitude, clientLongitude)
			response = resumed
		}
		response = g.withGatewayMetadata(response)
		g.updateSessionFromResponse(id, response)
		if _, err := clientWriter.Write(response); err != nil || clientWriter.Flush() != nil {
			return
		}
	}
}

func (g *gateway) withGatewayMetadata(body []byte) []byte {
	var response map[string]any
	if err := json.Unmarshal(body, &response); err != nil {
		return body
	}
	response["gateway"] = g.instance
	encoded, err := json.Marshal(response)
	if err != nil {
		return body
	}
	return append(encoded, '\n')
}

func (g *gateway) openRoute(ctx context.Context, requested string, previous *backend) (*backendConnection, []byte, error) {
	if requested == "" {
		return nil, nil, errors.New("location is required")
	}
	deadline := time.Now().Add(g.routeTimeout)
	for {
		candidates := g.routeCandidates(requested, previous)
		for _, candidate := range candidates {
			connection, hello, err := g.connectBackend(candidate)
			if err == nil {
				return connection, hello, nil
			}
			g.removeRoute(candidate)
		}
		if time.Now().After(deadline) || ctx.Err() != nil {
			return nil, nil, fmt.Errorf("location %q unavailable", requested)
		}
		g.discover(ctx)
		time.Sleep(100 * time.Millisecond)
	}
}

func (g *gateway) exchange(connection *backendConnection, message string) ([]byte, error) {
	_ = connection.conn.SetDeadline(time.Now().Add(g.backendTimeout))
	if _, err := connection.writer.WriteString(message + "\n"); err != nil {
		return nil, err
	}
	if err := connection.writer.Flush(); err != nil {
		return nil, err
	}
	return connection.reader.ReadBytes('\n')
}

func (g *gateway) connectBackend(info backend) (*backendConnection, []byte, error) {
	conn, err := net.DialTimeout("tcp", info.Address, g.backendTimeout)
	if err != nil {
		return nil, nil, err
	}
	connection := &backendConnection{info: info, conn: conn, reader: bufio.NewReader(conn), writer: bufio.NewWriter(conn)}
	hello, err := g.exchange(connection, "@location "+strconv.FormatInt(info.LocationID, 10))
	if err != nil {
		conn.Close()
		return nil, nil, err
	}
	var confirmed backend
	if err := json.Unmarshal(hello, &confirmed); err != nil || confirmed.Server != info.Server || confirmed.Generation != info.Generation {
		conn.Close()
		return nil, nil, errors.New("backend ownership changed")
	}
	_ = conn.SetDeadline(time.Time{})
	return connection, hello, nil
}

func (g *gateway) discoverLoop(ctx context.Context) {
	ticker := time.NewTicker(g.discoveryEvery)
	defer ticker.Stop()
	for {
		g.discover(ctx)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (g *gateway) discover(ctx context.Context) {
	lookupCtx, cancel := context.WithTimeout(ctx, g.discoveryTimeout)
	addresses, err := net.DefaultResolver.LookupHost(lookupCtx, g.serverHost)
	cancel()
	if err != nil {
		return
	}
	type result struct {
		info backend
		ok   bool
	}
	results := make(chan result, len(addresses))
	for _, address := range addresses {
		endpoint := net.JoinHostPort(address, g.serverPort)
		go func() {
			conn, err := net.DialTimeout("tcp", endpoint, g.discoveryTimeout)
			if err != nil {
				results <- result{}
				return
			}
			defer conn.Close()
			_ = conn.SetDeadline(time.Now().Add(g.discoveryTimeout))
			if _, err = fmt.Fprintln(conn, "@discover"); err != nil {
				results <- result{}
				return
			}
			var discovered discoveryResponse
			err = json.NewDecoder(conn).Decode(&discovered)
			if err != nil || discovered.Status != "active" {
				results <- result{}
				return
			}
			results <- result{info: backend{
				Server: discovered.Server, LocationID: discovered.LocationID,
				Latitude: discovered.Latitude, Longitude: discovered.Longitude,
				Instance: discovered.Instance, Address: endpoint, Generation: discovered.Generation,
			}, ok: true}
		}()
	}
	found := make(map[string]backend)
	for range addresses {
		result := <-results
		key := strconv.FormatInt(result.info.LocationID, 10)
		if current, exists := found[key]; result.ok && (!exists || result.info.Generation > current.Generation) {
			found[key] = result.info
		}
	}
	g.mu.Lock()
	g.routes = found
	g.mu.Unlock()
}

func (g *gateway) routeCandidates(requested string, previous *backend) []backend {
	g.mu.RLock()
	result := make([]backend, 0, len(g.routes))
	if requested == "any" {
		for _, route := range g.routes {
			result = append(result, route)
		}
	} else if route, ok := g.routes[requested]; ok {
		result = append(result, route)
	}
	g.mu.RUnlock()
	sort.Slice(result, func(i, j int) bool { return result[i].Server < result[j].Server })
	if requested == "any" && len(result) > 1 {
		offset := int(g.nextAny.Add(1)-1) % len(result)
		result = append(result[offset:], result[:offset]...)
	}
	if previous != nil {
		filtered := result[:0]
		for _, route := range result {
			if route.Address != previous.Address || route.Generation > previous.Generation {
				filtered = append(filtered, route)
			}
		}
		result = filtered
	}
	return result
}

func (g *gateway) routesSnapshot() []backend {
	g.mu.RLock()
	routes := make([]backend, 0, len(g.routes))
	for _, route := range g.routes {
		routes = append(routes, route)
	}
	g.mu.RUnlock()
	sort.Slice(routes, func(i, j int) bool { return routes[i].Server < routes[j].Server })
	return routes
}

func (g *gateway) publicLocations() []publicLocation {
	routes := g.routesSnapshot()
	locations := make([]publicLocation, 0, len(routes))
	for _, route := range routes {
		locations = append(locations, publicLocation{
			Server: route.Server, LocationID: route.LocationID,
			Latitude: route.Latitude, Longitude: route.Longitude,
			Generation: route.Generation,
		})
	}
	return locations
}

func (g *gateway) nearestLocation(latitude, longitude float64) (string, error) {
	routes := g.routesSnapshot()
	if len(routes) == 0 {
		return "", errors.New("no active locations available")
	}
	toRadians := func(value float64) float64 { return value * math.Pi / 180 }
	lat1 := toRadians(latitude)
	var bestLocation int64
	bestDistance := math.Inf(1)
	for _, route := range routes {
		lat2 := toRadians(route.Latitude)
		deltaLatitude := lat2 - lat1
		deltaLongitude := toRadians(route.Longitude - longitude)
		a := math.Sin(deltaLatitude/2)*math.Sin(deltaLatitude/2) +
			math.Cos(lat1)*math.Cos(lat2)*math.Sin(deltaLongitude/2)*math.Sin(deltaLongitude/2)
		a = math.Max(0, math.Min(1, a))
		distance := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
		if distance < bestDistance || (distance == bestDistance && (bestLocation == 0 || route.LocationID < bestLocation)) {
			bestDistance = distance
			bestLocation = route.LocationID
		}
	}
	return strconv.FormatInt(bestLocation, 10), nil
}

func (g *gateway) locationFor(ctx context.Context, latitude, longitude float64) (string, error) {
	location, err := g.nearestLocation(latitude, longitude)
	if err == nil {
		return location, nil
	}
	g.discover(ctx)
	return g.nearestLocation(latitude, longitude)
}

func parsePosition(message string) (float64, float64, bool, error) {
	arguments, found := strings.CutPrefix(message, "@position ")
	if !found {
		return 0, 0, false, nil
	}
	fields := strings.Fields(arguments)
	if len(fields) != 2 {
		return 0, 0, true, errors.New("position requires latitude and longitude")
	}
	latitude, latitudeErr := strconv.ParseFloat(fields[0], 64)
	longitude, longitudeErr := strconv.ParseFloat(fields[1], 64)
	if latitudeErr != nil || longitudeErr != nil || math.IsNaN(latitude) || math.IsNaN(longitude) ||
		math.IsInf(latitude, 0) || math.IsInf(longitude, 0) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180 {
		return 0, 0, true, errors.New("position must be finite latitude [-90, 90] and longitude [-180, 180]")
	}
	return latitude, longitude, true, nil
}

func parseTeleportRoute(message string) (float64, float64, bool, error) {
	arguments, found := strings.CutPrefix(message, "@teleport ")
	if !found {
		return 0, 0, false, nil
	}
	fields := strings.Fields(arguments)
	if len(fields) != 4 {
		return 0, 0, true, errors.New("teleport requires client UID, sequence, latitude, and longitude")
	}
	latitude, latitudeErr := strconv.ParseFloat(fields[2], 64)
	longitude, longitudeErr := strconv.ParseFloat(fields[3], 64)
	if latitudeErr != nil || longitudeErr != nil || math.IsNaN(latitude) || math.IsNaN(longitude) ||
		math.IsInf(latitude, 0) || math.IsInf(longitude, 0) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180 {
		return 0, 0, true, errors.New("teleport coordinates are invalid")
	}
	return latitude, longitude, true, nil
}

func (g *gateway) removeRoute(failed backend) {
	g.mu.Lock()
	key := strconv.FormatInt(failed.LocationID, 10)
	if current, ok := g.routes[key]; ok && current.Address == failed.Address && current.Generation == failed.Generation {
		delete(g.routes, key)
	}
	g.mu.Unlock()
}

func (g *gateway) updateSession(id, client string, route backend, latitude, longitude float64) {
	g.mu.Lock()
	connectedAt := time.Now().UTC()
	if current, ok := g.sessions[id]; ok {
		connectedAt = current.ConnectedAt
	}
	g.sessions[id] = session{ID: id, Client: client, LocationID: route.LocationID, Latitude: latitude, Longitude: longitude, Server: route.Server, Instance: route.Instance, Address: route.Address, Generation: route.Generation, ConnectedAt: connectedAt, Protocol: "TCP"}
	g.mu.Unlock()
}

func (g *gateway) updateSessionFromResponse(id string, body []byte) {
	var state struct {
		ClientUID       string  `json:"client_uid"`
		ClientLatitude  float64 `json:"client_latitude"`
		ClientLongitude float64 `json:"client_longitude"`
	}
	if json.Unmarshal(body, &state) != nil || state.ClientUID == "" {
		return
	}
	g.mu.Lock()
	if current, ok := g.sessions[id]; ok {
		current.ClientUID = state.ClientUID
		current.Latitude = state.ClientLatitude
		current.Longitude = state.ClientLongitude
		g.sessions[id] = current
	}
	g.mu.Unlock()
}

func (g *gateway) serveHTTP(ctx context.Context, address string) {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusOK)
		_, _ = writer.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/stats", func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		g.mu.RLock()
		sessions := make([]session, 0, len(g.sessions))
		for _, item := range g.sessions {
			sessions = append(sessions, item)
		}
		g.mu.RUnlock()
		sort.Slice(sessions, func(i, j int) bool { return sessions[i].ID < sessions[j].ID })
		routes := g.routesSnapshot()
		_ = json.NewEncoder(writer).Encode(map[string]any{"instance": g.instance, "accepted": g.accepted.Load(), "sessions": sessions, "routes": routes})
	})
	mux.HandleFunc("/", func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = writer.Write([]byte(statsPage))
	})
	server := &http.Server{Addr: address, Handler: mux, ReadHeaderTimeout: 2 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		g.logger.Error("stats server", "error", err)
	}
}

func responseHasError(body []byte) bool {
	var response struct {
		Error string `json:"error"`
	}
	return json.Unmarshal(body, &response) == nil && response.Error != ""
}

func writeJSONError(writer *bufio.Writer, message string) {
	body, _ := json.Marshal(map[string]string{"error": message})
	_, _ = writer.Write(append(body, '\n'))
	_ = writer.Flush()
}

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
		return "unknown-gateway"
	}
	return value
}
