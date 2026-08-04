package main

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
)

func (s *server) runVisibility(ctx context.Context) {
	ticker := time.NewTicker(visibilityInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			current := s.currentAssignment()
			if current == nil {
				continue
			}
			visibilityCtx, cancel := context.WithTimeout(ctx, visibilityInterval)
			s.publishVisibility(visibilityCtx, current)
			s.pullVisibility(visibilityCtx)
			cancel()
		}
	}
}

func (s *server) publishVisibility(ctx context.Context, current *assignment) {
	s.entityMu.Lock()
	entities := make([]visibleEntity, 0, len(s.entities))
	currentTick := s.tick.Load()
	for uid, entity := range s.entities {
		if currentTick-entity.lastInputTick < visibilityActiveTicks {
			entities = append(entities, visibleEntity{
				UID: uid, Latitude: entity.latitude, Longitude: entity.longitude, Sequence: entity.sequence,
			})
		}
	}
	s.entityMu.Unlock()
	body, err := json.Marshal(visibilitySnapshot{
		Generation: current.Generation,
		UpdatedAt:  time.Now().UnixMilli(),
		Entities:   entities,
	})
	if err == nil {
		_ = s.redis.Set(ctx, visibilityKey(current.ServerID, current.Generation), body, visibilityLease).Err()
	}
}

func (s *server) pullVisibility(ctx context.Context) {
	s.topologyMu.RLock()
	keys := make([]string, 0, len(s.topology))
	for _, location := range s.topology {
		keys = append(keys, visibilityKey(location.serverID, location.generation))
	}
	s.topologyMu.RUnlock()
	if len(keys) == 0 {
		return
	}
	values, err := s.redis.MGet(ctx, keys...).Result()
	if err != nil {
		s.visibilityMu.Lock()
		if s.visibilityReadAt.IsZero() || time.Since(s.visibilityReadAt) >= visibilityLease {
			s.visible = nil
		}
		s.visibilityMu.Unlock()
		return
	}
	cutoff := time.Now().Add(-visibilityLease).UnixMilli()
	latest := make(map[string]visibleEntity)
	updated := make(map[string]int64)
	for _, value := range values {
		body, ok := value.(string)
		if !ok {
			continue
		}
		var snapshot visibilitySnapshot
		if json.Unmarshal([]byte(body), &snapshot) != nil || snapshot.UpdatedAt < cutoff {
			continue
		}
		for _, entity := range snapshot.Entities {
			current, exists := latest[entity.UID]
			if !exists || entity.Sequence > current.Sequence ||
				(entity.Sequence == current.Sequence && snapshot.UpdatedAt > updated[entity.UID]) {
				latest[entity.UID] = entity
				updated[entity.UID] = snapshot.UpdatedAt
			}
		}
	}
	visible := make([]visibleEntity, 0, len(latest))
	for _, entity := range latest {
		visible = append(visible, entity)
	}
	s.visibilityMu.Lock()
	s.visible = visible
	s.visibilityReadAt = time.Now()
	s.visibilityMu.Unlock()
}

func (s *server) visibleEntities(excludeUID string) []visibleEntity {
	s.visibilityMu.RLock()
	entities := make([]visibleEntity, 0, len(s.visible))
	for _, entity := range s.visible {
		if entity.UID != excludeUID {
			entities = append(entities, entity)
		}
	}
	s.visibilityMu.RUnlock()
	return entities
}

func visibilityKey(serverID string, generation int64) string {
	return fmt.Sprintf("tcp-lab:visibility:%s:%d", serverID, generation)
}

func (s *server) deleteVisibility(ctx context.Context, current *assignment) {
	_ = s.redis.Del(ctx, visibilityKey(current.ServerID, current.Generation)).Err()
}
