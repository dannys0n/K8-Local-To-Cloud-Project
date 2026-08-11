package main

import "math"

// visibleEntities combines authoritative local entities with the bounded,
// transient neighbor cache and applies the same spatial filter to both.
func (s *server) visibleEntities(excludeUID string, latitude, longitude, zoom float64, serverRelevance, spatialRelevance, crossServerRelevance bool) ([]visibleEntity, int) {
	currentTick := s.tick.Load()
	worldPixels, x, y := 0.0, 0.0, 0.0
	if spatialRelevance {
		worldPixels = 256 * math.Exp2(zoom)
		x, y = normalizedPosition(latitude, longitude)
	}

	s.entityMu.Lock()
	entityCount := len(s.entities)
	entities := make([]visibleEntity, 0, entityCount)
	seen := make(map[string]struct{}, entityCount)
	if serverRelevance {
		for uid, entity := range s.entities {
			if uid == excludeUID || currentTick-entity.lastInputTick >= visibilityActiveTicks {
				continue
			}
			visible := visibleEntity{UID: uid, Latitude: entity.latitude, Longitude: entity.longitude, Sequence: entity.sequence}
			if entityIsRelevant(visible, spatialRelevance, worldPixels, x, y) {
				entities = append(entities, visible)
				seen[uid] = struct{}{}
			}
		}
	}
	s.entityMu.Unlock()

	if crossServerRelevance {
		s.remoteEntityMu.RLock()
		cells := make([]spatialCell, 0, len(s.remoteEntities))
		if spatialRelevance {
			cells = cellsForInterest(x, y, visibilityRadiusPixels/worldPixels)
		} else {
			for cell := range s.remoteEntities {
				cells = append(cells, cell)
			}
		}
		type remoteCandidate struct {
			entity    visibleEntity
			published int64
		}
		remote := make(map[string]remoteCandidate)
		for _, cell := range cells {
			for _, snapshot := range s.remoteEntities[cell] {
				for _, entity := range snapshot.Entities {
					if entity.UID == excludeUID {
						continue
					}
					if _, exists := seen[entity.UID]; exists {
						continue
					}
					previous, exists := remote[entity.UID]
					if !exists || entity.Sequence > previous.entity.Sequence ||
						(entity.Sequence == previous.entity.Sequence && snapshot.Published > previous.published) {
						remote[entity.UID] = remoteCandidate{entity: entity, published: snapshot.Published}
					}
				}
			}
		}
		for uid, candidate := range remote {
			if entityIsRelevant(candidate.entity, spatialRelevance, worldPixels, x, y) {
				entities = append(entities, candidate.entity)
				seen[uid] = struct{}{}
			}
		}
		s.remoteEntityMu.RUnlock()
	}
	return entities, entityCount
}

func entityIsRelevant(entity visibleEntity, spatialRelevance bool, worldPixels, x, y float64) bool {
	if !spatialRelevance {
		return true
	}
	entityX, entityY := normalizedPosition(entity.Latitude, entity.Longitude)
	return math.Hypot(wrappedDistance(x, entityX), wrappedDistance(y, entityY))*worldPixels <= visibilityRadiusPixels
}

func normalizedPosition(latitude, longitude float64) (float64, float64) {
	return (longitude + 180) / 360, mercatorY(latitude)
}

func mercatorY(latitude float64) float64 {
	latitude = math.Max(-mercatorLatitudeLimit, math.Min(mercatorLatitudeLimit, latitude))
	projected := math.Log(math.Tan(math.Pi/4 + latitude*math.Pi/360))
	return (1 - projected/math.Pi) / 2
}

func wrappedDistance(a, b float64) float64 {
	distance := math.Abs(a - b)
	return math.Min(distance, 1-distance)
}
