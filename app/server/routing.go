package main

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

type routeRecord struct {
	Server     string  `json:"server"`
	LocationID int64   `json:"location_id"`
	Latitude   float64 `json:"latitude"`
	Longitude  float64 `json:"longitude"`
	Instance   string  `json:"instance"`
	Address    string  `json:"address"`
	Generation int64   `json:"generation"`
}

func (s *server) publishRoute(ctx context.Context, current *assignment) error {
	record := routeRecord{
		Server: current.ServerID, LocationID: current.LocationID,
		Latitude: current.Latitude, Longitude: current.Longitude,
		Instance: s.podName, Address: s.routeAddress, Generation: current.Generation,
	}
	body, err := json.Marshal(record)
	if err != nil {
		return err
	}
	cacheCtx, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
	defer cancel()
	return s.valkey.Set(cacheCtx, routeKey(current.LocationID), body, 2*s.renewInterval).Err()
}

func (s *server) resolveRoute(ctx context.Context, requested string) (routeRecord, error) {
	if requested == "any" {
		s.topologyMu.RLock()
		locationIDs := make([]int64, 0, len(s.topology))
		for _, location := range s.topology {
			locationIDs = append(locationIDs, location.locationID)
		}
		s.topologyMu.RUnlock()
		if len(locationIDs) == 0 {
			return routeRecord{}, errors.New("route unavailable")
		}
		start := int(s.nextRoute.Add(1)-1) % len(locationIDs)
		for offset := range locationIDs {
			if route, err := s.resolveLocationRoute(ctx, locationIDs[(start+offset)%len(locationIDs)]); err == nil {
				return route, nil
			}
		}
		return routeRecord{}, errors.New("route unavailable")
	}
	locationID, err := strconv.ParseInt(requested, 10, 64)
	if err != nil || locationID <= 0 {
		return routeRecord{}, errors.New("invalid location")
	}
	return s.resolveLocationRoute(ctx, locationID)
}

func (s *server) resolveLocationRoute(ctx context.Context, locationID int64) (routeRecord, error) {
	cacheCtx, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
	defer cancel()
	body, err := s.valkey.Get(cacheCtx, routeKey(locationID)).Bytes()
	if err != nil {
		return routeRecord{}, errors.New("route unavailable")
	}
	return decodeRoute(body, locationID)
}

func (s *server) resolveRoutes(ctx context.Context) ([]routeRecord, error) {
	s.topologyMu.RLock()
	locationIDs := make([]int64, 0, len(s.topology))
	keys := make([]string, 0, len(s.topology))
	for _, location := range s.topology {
		locationIDs = append(locationIDs, location.locationID)
		keys = append(keys, routeKey(location.locationID))
	}
	s.topologyMu.RUnlock()
	if len(keys) == 0 {
		return nil, nil
	}
	cacheCtx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
	defer cancel()
	commands := make([]*redis.StringCmd, len(keys))
	_, err := s.valkey.Pipelined(cacheCtx, func(pipe redis.Pipeliner) error {
		for index, key := range keys {
			commands[index] = pipe.Get(cacheCtx, key)
		}
		return nil
	})
	if err != nil && !errors.Is(err, redis.Nil) {
		return nil, err
	}
	routes := make([]routeRecord, 0, len(commands))
	for index, command := range commands {
		text, commandErr := command.Result()
		if commandErr != nil {
			continue
		}
		route, err := decodeRoute([]byte(text), locationIDs[index])
		if err == nil {
			routes = append(routes, route)
		}
	}
	sort.Slice(routes, func(i, j int) bool { return routes[i].LocationID < routes[j].LocationID })
	return routes, nil
}

func (s *server) nearestRoute(ctx context.Context, latitude, longitude float64, excludedLocation int64) (routeRecord, error) {
	s.topologyMu.RLock()
	locationID := s.locationIndex.nearest(latitude, longitude, excludedLocation)
	s.topologyMu.RUnlock()
	if locationID != 0 {
		if route, err := s.resolveLocationRoute(ctx, locationID); err == nil {
			return route, nil
		}
	}
	// A location can temporarily have no owner during replacement. Keep the
	// exhaustive path as failure recovery rather than paying for it normally.
	routes, err := s.resolveRoutes(ctx)
	if err != nil || len(routes) == 0 {
		return routeRecord{}, errors.New("no active locations available")
	}
	latitudeRadians := latitude * math.Pi / 180
	best := routeRecord{}
	bestDistance := math.Inf(1)
	for _, route := range routes {
		if route.LocationID == excludedLocation {
			continue
		}
		locationLatitude := route.Latitude * math.Pi / 180
		deltaLatitude := locationLatitude - latitudeRadians
		deltaLongitude := (route.Longitude - longitude) * math.Pi / 180
		a := math.Sin(deltaLatitude/2)*math.Sin(deltaLatitude/2) +
			math.Cos(latitudeRadians)*math.Cos(locationLatitude)*math.Sin(deltaLongitude/2)*math.Sin(deltaLongitude/2)
		a = math.Max(0, math.Min(1, a))
		distance := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
		if distance < bestDistance || distance == bestDistance && route.LocationID < best.LocationID {
			bestDistance = distance
			best = route
		}
	}
	if best.LocationID == 0 {
		return routeRecord{}, errors.New("no active locations available")
	}
	return best, nil
}

func parseRouteCoordinates(arguments string) (float64, float64, int64, error) {
	fields := strings.Fields(arguments)
	if len(fields) < 2 || len(fields) > 3 {
		return 0, 0, 0, errors.New("coordinates require latitude, longitude, and optional excluded location")
	}
	latitude, latitudeErr := strconv.ParseFloat(fields[0], 64)
	longitude, longitudeErr := strconv.ParseFloat(fields[1], 64)
	excludedLocation := int64(0)
	var excludedErr error
	if len(fields) == 3 {
		excludedLocation, excludedErr = strconv.ParseInt(fields[2], 10, 64)
	}
	if latitudeErr != nil || longitudeErr != nil || math.IsNaN(latitude) || math.IsNaN(longitude) ||
		math.IsInf(latitude, 0) || math.IsInf(longitude, 0) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180 || excludedErr != nil || excludedLocation < 0 {
		return 0, 0, 0, errors.New("coordinates are invalid")
	}
	return latitude, longitude, excludedLocation, nil
}

func decodeRoute(body []byte, expectedLocation int64) (routeRecord, error) {
	var route routeRecord
	if err := json.Unmarshal(body, &route); err != nil || route.LocationID != expectedLocation ||
		route.Server == "" || route.Instance == "" || route.Address == "" || route.Generation <= 0 {
		return routeRecord{}, errors.New("invalid route")
	}
	return route, nil
}

func routeKey(locationID int64) string {
	id := strconv.FormatInt(locationID, 10)
	return routePrefix + "{" + id + "}"
}

const routePrefix = "tcp-lab:route:"
