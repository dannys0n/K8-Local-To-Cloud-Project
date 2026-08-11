package main

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	locationIndexKey    = "tcp-lab:locations"
	topologyRevisionKey = "tcp-lab:topology:revision"
	topologyChannel     = "tcp-lab:topology:changed"
)

var claimLocationScript = redis.NewScript(`
if redis.call('HGET', KEYS[1], 'enabled') ~= '1' then return {} end
local now = redis.call('TIME')
local nowms = now[1] * 1000 + math.floor(now[2] / 1000)
local owner = redis.call('HGET', KEYS[1], 'owner') or ''
local lease = tonumber(redis.call('HGET', KEYS[1], 'lease_until') or '0')
local generation = tonumber(redis.call('HGET', KEYS[1], 'generation') or '0')
if owner ~= ARGV[1] then
  if owner ~= '' and lease > nowms then return {} end
  generation = redis.call('HINCRBY', KEYS[1], 'generation', 1)
end
lease = nowms + tonumber(ARGV[2])
redis.call('HSET', KEYS[1], 'owner', ARGV[1], 'lease_until', lease)
local values = redis.call('HMGET', KEYS[1], 'server_id', 'location_id', 'latitude', 'longitude')
return {values[1], values[2], values[3], values[4], tostring(generation), tostring(lease)}
`)

var renewLocationScript = redis.NewScript(`
if redis.call('HGET', KEYS[1], 'enabled') ~= '1' then return nil end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] then return nil end
if tonumber(redis.call('HGET', KEYS[1], 'generation') or '0') ~= tonumber(ARGV[2]) then return nil end
local now = redis.call('TIME')
local lease = now[1] * 1000 + math.floor(now[2] / 1000) + tonumber(ARGV[3])
redis.call('HSET', KEYS[1], 'lease_until', lease)
return tostring(lease)
`)

var releaseLocationScript = redis.NewScript(`
if redis.call('HGET', KEYS[1], 'owner') == ARGV[1]
   and tonumber(redis.call('HGET', KEYS[1], 'generation') or '0') == tonumber(ARGV[2]) then
  redis.call('HSET', KEYS[1], 'owner', '', 'lease_until', 0)
  return 1
end
return 0
`)

var storeEntityScript = redis.NewScript(`
local old_location = tonumber(redis.call('HGET', KEYS[1], 'owner_location') or '0')
local old_generation = tonumber(redis.call('HGET', KEYS[1], 'owner_generation') or '0')
if old_location == tonumber(ARGV[1]) and old_generation > tonumber(ARGV[2]) then return 0 end
local entity_generation = redis.call('HINCRBY', KEYS[1], 'entity_generation', 1)
redis.call('HSET', KEYS[1],
  'owner_location', ARGV[1], 'owner_server', ARGV[3], 'owner_generation', ARGV[2],
  'latitude', ARGV[4], 'longitude', ARGV[5])
return entity_generation
`)

var prepareHandoffScript = redis.NewScript(`
local old_location = tonumber(redis.call('HGET', KEYS[1], 'owner_location') or ARGV[1])
local old_generation = tonumber(redis.call('HGET', KEYS[1], 'owner_generation') or ARGV[2])
if old_location ~= tonumber(ARGV[1]) or old_generation ~= tonumber(ARGV[2]) then return 0 end
local entity_generation = redis.call('HINCRBY', KEYS[1], 'entity_generation', 1)
redis.call('HSET', KEYS[1],
  'owner_location', ARGV[3], 'owner_server', '', 'owner_generation', 0,
  'latitude', ARGV[4], 'longitude', ARGV[5])
return entity_generation
`)

func connectValkey(ctx context.Context, logger *slog.Logger) (*redis.ClusterClient, error) {
	raw := strings.TrimSpace(os.Getenv("VALKEY_ADDRS"))
	if raw == "" {
		return nil, errors.New("VALKEY_ADDRS is required")
	}
	addresses := strings.Split(raw, ",")
	for index := range addresses {
		addresses[index] = strings.TrimSpace(addresses[index])
	}
	options := &redis.ClusterOptions{
		Addrs: addresses, MaxRedirects: 8,
		Username:    strings.TrimSpace(os.Getenv("VALKEY_USERNAME")),
		Password:    os.Getenv("VALKEY_PASSWORD"),
		DialTimeout: time.Second, ReadTimeout: time.Second, WriteTimeout: time.Second,
		PoolSize: 16, MinIdleConns: 1,
	}
	if strings.EqualFold(strings.TrimSpace(os.Getenv("VALKEY_TLS")), "true") {
		options.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	}
	client := redis.NewClusterClient(options)
	startupCtx, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	for {
		info, err := client.ClusterInfo(startupCtx).Result()
		if err == nil && strings.Contains(info, "cluster_state:ok") {
			return client, nil
		}
		logger.Info("waiting for valkey cluster", "error", err)
		select {
		case <-startupCtx.Done():
			client.Close()
			return nil, fmt.Errorf("valkey startup timeout: %w", startupCtx.Err())
		case <-time.After(time.Second):
		}
	}
}

func locationKey(id int64) string {
	return "tcp-lab:location:{" + strconv.FormatInt(id, 10) + "}"
}

func entityKey(uid string) string { return "tcp-lab:entity:{" + uid + "}" }

func (s *server) readTopologyRevision(ctx context.Context) (int64, error) {
	value, err := s.valkey.Get(ctx, topologyRevisionKey).Int64()
	if errors.Is(err, redis.Nil) {
		return 0, nil
	}
	return value, err
}

func (s *server) loadTopology(ctx context.Context) ([]locationDefinition, int64, error) {
	queryCtx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	ids, err := s.valkey.ZRange(queryCtx, locationIndexKey, 0, -1).Result()
	if err != nil {
		return nil, 0, err
	}
	commands := make([]*redis.MapStringStringCmd, len(ids))
	_, err = s.valkey.Pipelined(queryCtx, func(pipe redis.Pipeliner) error {
		for index, id := range ids {
			commands[index] = pipe.HGetAll(queryCtx, locationKey(mustInt64(id)))
		}
		return nil
	})
	if err != nil && !errors.Is(err, redis.Nil) {
		return nil, 0, err
	}
	loaded := make([]locationDefinition, 0, len(ids))
	for _, command := range commands {
		values, commandErr := command.Result()
		if commandErr != nil || values["enabled"] != "1" {
			continue
		}
		id, idErr := strconv.ParseInt(values["location_id"], 10, 64)
		latitude, latErr := strconv.ParseFloat(values["latitude"], 64)
		longitude, lonErr := strconv.ParseFloat(values["longitude"], 64)
		generation, genErr := strconv.ParseInt(defaultString(values["generation"], "0"), 10, 64)
		if idErr != nil || latErr != nil || lonErr != nil || genErr != nil || values["server_id"] == "" {
			continue
		}
		loaded = append(loaded, locationDefinition{serverID: values["server_id"], locationID: id, latitude: latitude, longitude: longitude, generation: generation})
	}
	sort.Slice(loaded, func(i, j int) bool { return loaded[i].locationID < loaded[j].locationID })
	revision, err := s.readTopologyRevision(queryCtx)
	return loaded, revision, err
}

func (s *server) claim(ctx context.Context) (*assignment, error) {
	ids, err := s.valkey.ZRange(ctx, locationIndexKey, 0, -1).Result()
	if err != nil || len(ids) == 0 {
		return nil, err
	}
	start := int(s.nextRoute.Add(1)-1) % len(ids)
	for offset := range ids {
		id, parseErr := strconv.ParseInt(ids[(start+offset)%len(ids)], 10, 64)
		if parseErr != nil {
			continue
		}
		result, runErr := claimLocationScript.Run(ctx, s.valkey, []string{locationKey(id)}, s.instanceID, s.leaseDuration.Milliseconds()).Result()
		if runErr != nil {
			return nil, runErr
		}
		values, ok := result.([]interface{})
		if !ok || len(values) != 6 {
			continue
		}
		claimed, parseErr := assignmentFromScript(values)
		if parseErr == nil {
			return claimed, nil
		}
	}
	return nil, nil
}

func (s *server) renew(ctx context.Context, current *assignment) (*assignment, error) {
	value, err := renewLocationScript.Run(ctx, s.valkey, []string{locationKey(current.LocationID)}, s.instanceID, current.Generation, s.leaseDuration.Milliseconds()).Text()
	if errors.Is(err, redis.Nil) {
		return nil, errors.New("location lease lost")
	}
	if err != nil {
		return nil, err
	}
	milliseconds, err := strconv.ParseInt(value, 10, 64)
	if err != nil {
		return nil, err
	}
	renewed := *current
	renewed.LeaseUntil = time.UnixMilli(milliseconds)
	return &renewed, nil
}

func (s *server) release(ctx context.Context) {
	current := s.currentAssignment()
	if current == nil {
		return
	}
	_ = releaseLocationScript.Run(ctx, s.valkey, []string{locationKey(current.LocationID)}, s.instanceID, current.Generation).Err()
}

func (s *server) loadEntity(ctx context.Context, uid string) (*entityState, bool, error) {
	queryCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	values, err := s.valkey.HGetAll(queryCtx, entityKey(uid)).Result()
	if err != nil {
		return nil, false, err
	}
	state := &entityState{viewZoom: minimumViewZoom, serverRelevant: true, crossServerRelevant: true}
	if len(values) == 0 {
		return state, false, nil
	}
	state.latitude, err = strconv.ParseFloat(values["latitude"], 64)
	if err != nil {
		return nil, false, err
	}
	state.longitude, err = strconv.ParseFloat(values["longitude"], 64)
	if err != nil {
		return nil, false, err
	}
	return state, true, nil
}

func (s *server) storeEntityClaim(ctx context.Context, current *assignment, uid string, latitude, longitude float64) error {
	queryCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	result, err := storeEntityScript.Run(queryCtx, s.valkey, []string{entityKey(uid)},
		current.LocationID, current.Generation, current.ServerID, latitude, longitude).Int64()
	if err != nil {
		return err
	}
	if result == 0 {
		return errors.New("stale entity owner generation")
	}
	return nil
}

func (s *server) prepareEntityHandoff(ctx context.Context, current *assignment, uid string, destination int64, latitude, longitude float64) error {
	queryCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	result, err := prepareHandoffScript.Run(queryCtx, s.valkey, []string{entityKey(uid)},
		current.LocationID, current.Generation, destination, latitude, longitude).Int64()
	if err != nil {
		return err
	}
	if result == 0 {
		return errors.New("entity ownership changed before handoff")
	}
	return nil
}

func assignmentFromScript(values []interface{}) (*assignment, error) {
	texts := make([]string, len(values))
	for index, value := range values {
		texts[index] = fmt.Sprint(value)
	}
	id, err := strconv.ParseInt(texts[1], 10, 64)
	if err != nil {
		return nil, err
	}
	latitude, err := strconv.ParseFloat(texts[2], 64)
	if err != nil {
		return nil, err
	}
	longitude, err := strconv.ParseFloat(texts[3], 64)
	if err != nil {
		return nil, err
	}
	generation, err := strconv.ParseInt(texts[4], 10, 64)
	if err != nil {
		return nil, err
	}
	lease, err := strconv.ParseInt(texts[5], 10, 64)
	if err != nil {
		return nil, err
	}
	return &assignment{ServerID: texts[0], LocationID: id, Latitude: latitude, Longitude: longitude, Generation: generation, LeaseUntil: time.UnixMilli(lease)}, nil
}

func mustInt64(value string) int64 { parsed, _ := strconv.ParseInt(value, 10, 64); return parsed }

func defaultString(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}
