package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"net"
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

type backend struct {
	Server     string  `json:"server"`
	LocationID int64   `json:"location_id"`
	Latitude   float64 `json:"latitude"`
	Longitude  float64 `json:"longitude"`
	Instance   string  `json:"instance"`
	Address    string  `json:"address"`
	Generation int64   `json:"generation"`
}

type publicLocation struct {
	Server     string  `json:"server"`
	LocationID int64   `json:"location_id"`
	Latitude   float64 `json:"latitude"`
	Longitude  float64 `json:"longitude"`
	Generation int64   `json:"generation"`
}

type backendResponse struct {
	Error           string  `json:"error"`
	Message         string  `json:"message"`
	Reroute         int64   `json:"reroute"`
	ClientUID       string  `json:"client_uid"`
	InputSequence   uint64  `json:"input_sequence"`
	ClientLatitude  float64 `json:"client_latitude"`
	ClientLongitude float64 `json:"client_longitude"`
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
	Status      string    `json:"status"`
}

type gateway struct {
	instance        string
	gatewayMetadata []byte
	serverHost      string
	serverPort      string
	routeTimeout    time.Duration
	backendTimeout  time.Duration
	logger          *slog.Logger
	mu              sync.RWMutex
	catalog         map[string]backend
	sessions        map[string]session
	accepted        atomic.Uint64
	nextSession     atomic.Uint64
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

	instance := envOrDefault("POD_NAME", hostname())
	encodedInstance, _ := json.Marshal(instance)
	g := &gateway{
		instance:        instance,
		gatewayMetadata: append([]byte(`"gateway":`), encodedInstance...),
		serverHost:      envOrDefault("SERVER_HOST", "tcp-server.tcp-lab.svc.cluster.local"),
		serverPort:      envOrDefault("SERVER_PORT", "7000"),
		routeTimeout:    routeTimeout, backendTimeout: backendTimeout, logger: logger,
		catalog: make(map[string]backend), sessions: make(map[string]session),
	}
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
	g.mu.Lock()
	g.sessions[id] = session{ID: id, Client: client.RemoteAddr().String(), ConnectedAt: time.Now().UTC(), Protocol: "TCP", Status: "gateway"}
	g.mu.Unlock()
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
			if location != "any" {
				writeJSONError(clientWriter, "clients cannot select a server location")
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
			clientLatitude = candidate.info.Latitude
			clientLongitude = candidate.info.Longitude
			g.updateSession(id, client.RemoteAddr().String(), candidate.info, clientLatitude, clientLongitude)
			hello = g.withGatewayMetadata(hello)
			if _, err := clientWriter.Write(hello); err != nil || clientWriter.Flush() != nil {
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
			locations, err := g.publicLocations(ctx)
			if err != nil {
				writeJSONError(clientWriter, err.Error())
				continue
			}
			body, _ := json.Marshal(map[string]any{"gateway": g.instance, "locations": locations})
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
		state, valid := decodeBackendResponse(response)
		if err != nil || !valid || state.Error != "" {
			failed := downstream.info
			downstream.conn.Close()
			downstream = nil
			g.setSessionStatus(id, "gateway")
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
			state, valid = decodeBackendResponse(response)
		}
		if err != nil || !valid || state.Error != "" {
			if downstream != nil {
				downstream.conn.Close()
				downstream = nil
			}
			g.setSessionStatus(id, "gateway")
			writeJSONError(clientWriter, "backend request failed")
			continue
		}
		if state.hasAuthoritativePosition() {
			clientLatitude = state.ClientLatitude
			clientLongitude = state.ClientLongitude
		}
		if state.Reroute != 0 {
			nextLocation := strconv.FormatInt(state.Reroute, 10)
			candidate, _, routeErr := g.openRoute(ctx, nextLocation, nil)
			if routeErr != nil {
				writeJSONError(clientWriter, routeErr.Error())
				continue
			}
			resume := fmt.Sprintf("@resume %s %d %.8f %.8f", state.ClientUID, state.InputSequence, state.ClientLatitude, state.ClientLongitude)
			resumed, resumeErr := g.exchange(candidate, resume)
			resumedState, resumeValid := decodeBackendResponse(resumed)
			if resumeErr != nil || !resumeValid || resumedState.Error != "" {
				candidate.conn.Close()
				writeJSONError(clientWriter, "server handoff failed")
				continue
			}
			downstream.conn.Close()
			downstream = candidate
			requested = nextLocation
			clientLatitude = state.ClientLatitude
			clientLongitude = state.ClientLongitude
			g.updateSession(id, client.RemoteAddr().String(), candidate.info, clientLatitude, clientLongitude)
			response = resumed
			state = resumedState
		}
		response = g.withGatewayMetadata(response)
		g.updateSessionFromResponse(id, state)
		if _, err := clientWriter.Write(response); err != nil || clientWriter.Flush() != nil {
			return
		}
	}
}

func (g *gateway) withGatewayMetadata(body []byte) []byte {
	trimmed := bytes.TrimSpace(body)
	if len(trimmed) < 2 || trimmed[0] != '{' || trimmed[len(trimmed)-1] != '}' {
		return body
	}
	result := make([]byte, 0, len(trimmed)+len(g.gatewayMetadata)+2)
	result = append(result, trimmed[:len(trimmed)-1]...)
	if len(trimmed) > 2 {
		result = append(result, ',')
	}
	result = append(result, g.gatewayMetadata...)
	return append(result, '}', '\n')
}

func (g *gateway) openRoute(ctx context.Context, requested string, previous *backend) (*backendConnection, []byte, error) {
	if requested == "" {
		return nil, nil, errors.New("location is required")
	}
	deadline := time.Now().Add(g.routeTimeout)
	for {
		candidate, err := g.resolveRoute(ctx, requested)
		if err == nil && (previous == nil || candidate.Address != previous.Address || candidate.Generation > previous.Generation) {
			connection, hello, err := g.connectBackend(candidate)
			if err == nil {
				return connection, hello, nil
			}
		}
		if time.Now().After(deadline) || ctx.Err() != nil {
			return nil, nil, fmt.Errorf("location %q unavailable", requested)
		}
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

func (g *gateway) resolveRoute(ctx context.Context, requested string) (backend, error) {
	var route backend
	err := g.resolve(ctx, "@route "+requested, &route)
	if err != nil || route.LocationID == 0 || route.Address == "" {
		return backend{}, errors.New("route unavailable")
	}
	return route, nil
}

func (g *gateway) resolve(ctx context.Context, command string, response any) error {
	endpoint := net.JoinHostPort(g.serverHost, g.serverPort)
	dialer := net.Dialer{Timeout: g.backendTimeout}
	conn, err := dialer.DialContext(ctx, "tcp", endpoint)
	if err != nil {
		return err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(g.backendTimeout))
	if _, err = fmt.Fprintln(conn, command); err != nil {
		return err
	}
	return json.NewDecoder(conn).Decode(response)
}

func (g *gateway) routesSnapshot() []backend {
	g.mu.RLock()
	routes := make([]backend, 0, len(g.catalog))
	for _, route := range g.catalog {
		routes = append(routes, route)
	}
	g.mu.RUnlock()
	sort.Slice(routes, func(i, j int) bool { return routes[i].Server < routes[j].Server })
	return routes
}

func (g *gateway) publicLocations(ctx context.Context) ([]publicLocation, error) {
	var response struct {
		Routes []backend `json:"routes"`
	}
	if err := g.resolve(ctx, "@routes", &response); err != nil {
		return nil, errors.New("locations unavailable")
	}
	routes := response.Routes
	locations := make([]publicLocation, 0, len(routes))
	refreshed := make(map[string]backend, len(routes))
	for _, route := range routes {
		refreshed[strconv.FormatInt(route.LocationID, 10)] = route
		locations = append(locations, publicLocation{
			Server: route.Server, LocationID: route.LocationID,
			Latitude: route.Latitude, Longitude: route.Longitude,
			Generation: route.Generation,
		})
	}
	g.mu.Lock()
	g.catalog = refreshed
	g.mu.Unlock()
	return locations, nil
}

func (g *gateway) locationFor(ctx context.Context, latitude, longitude float64) (string, error) {
	var route backend
	command := fmt.Sprintf("@nearest %.8f %.8f", latitude, longitude)
	if err := g.resolve(ctx, command, &route); err != nil || route.LocationID == 0 {
		return "", errors.New("no active locations available")
	}
	return strconv.FormatInt(route.LocationID, 10), nil
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

func (g *gateway) updateSession(id, client string, route backend, latitude, longitude float64) {
	g.mu.Lock()
	connectedAt := time.Now().UTC()
	if current, ok := g.sessions[id]; ok {
		connectedAt = current.ConnectedAt
	}
	g.sessions[id] = session{ID: id, Client: client, LocationID: route.LocationID, Latitude: latitude, Longitude: longitude, Server: route.Server, Instance: route.Instance, Address: route.Address, Generation: route.Generation, ConnectedAt: connectedAt, Protocol: "TCP", Status: "ready"}
	g.mu.Unlock()
}

func (g *gateway) setSessionStatus(id, status string) {
	g.mu.Lock()
	if current, ok := g.sessions[id]; ok {
		current.Status = status
		g.sessions[id] = current
	}
	g.mu.Unlock()
}

func (g *gateway) updateSessionFromResponse(id string, state backendResponse) {
	if !state.hasAuthoritativePosition() {
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

func (state backendResponse) hasAuthoritativePosition() bool {
	return state.ClientUID != "" && (state.Message == "state" || state.Message == "teleport")
}

func decodeBackendResponse(body []byte) (backendResponse, bool) {
	var response backendResponse
	err := json.Unmarshal(body, &response)
	return response, err == nil
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
