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
	return s.redis.Set(cacheCtx, routeKey(current.LocationID), body, s.leaseDuration).Err()
}

func (s *server) resolveRoute(ctx context.Context, requested string) (routeRecord, error) {
	if requested == "any" {
		routes, err := s.resolveRoutes(ctx)
		if err != nil || len(routes) == 0 {
			return routeRecord{}, errors.New("route unavailable")
		}
		return routes[int(s.nextRoute.Add(1)-1)%len(routes)], nil
	}
	locationID, err := strconv.ParseInt(requested, 10, 64)
	if err != nil || locationID <= 0 {
		return routeRecord{}, errors.New("invalid location")
	}
	cacheCtx, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
	defer cancel()
	body, err := s.redis.Get(cacheCtx, routeKey(locationID)).Bytes()
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
	values, err := s.redis.MGet(cacheCtx, keys...).Result()
	if err != nil {
		return nil, err
	}
	routes := make([]routeRecord, 0, len(values))
	for index, value := range values {
		text, ok := value.(string)
		if !ok {
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

func (s *server) nearestRoute(ctx context.Context, latitude, longitude float64) (routeRecord, error) {
	routes, err := s.resolveRoutes(ctx)
	if err != nil || len(routes) == 0 {
		return routeRecord{}, errors.New("no active locations available")
	}
	latitudeRadians := latitude * math.Pi / 180
	best := routes[0]
	bestDistance := math.Inf(1)
	for _, route := range routes {
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
	return best, nil
}

func parseRouteCoordinates(arguments string) (float64, float64, error) {
	fields := strings.Fields(arguments)
	if len(fields) != 2 {
		return 0, 0, errors.New("coordinates require latitude and longitude")
	}
	latitude, latitudeErr := strconv.ParseFloat(fields[0], 64)
	longitude, longitudeErr := strconv.ParseFloat(fields[1], 64)
	if latitudeErr != nil || longitudeErr != nil || math.IsNaN(latitude) || math.IsNaN(longitude) ||
		math.IsInf(latitude, 0) || math.IsInf(longitude, 0) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180 {
		return 0, 0, errors.New("coordinates are invalid")
	}
	return latitude, longitude, nil
}

func decodeRoute(body []byte, expectedLocation int64) (routeRecord, error) {
	var route routeRecord
	if err := json.Unmarshal(body, &route); err != nil || route.LocationID != expectedLocation ||
		route.Server == "" || route.Instance == "" || route.Address == "" || route.Generation <= 0 {
		return routeRecord{}, errors.New("invalid route")
	}
	return route, nil
}

func routeKey(locationID int64) string { return routePrefix + strconv.FormatInt(locationID, 10) }

const routePrefix = "tcp-lab:route:"
