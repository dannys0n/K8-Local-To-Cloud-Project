package main

import "math"

// visibleEntities applies the existing spatial relevance radius only to
// entities currently authoritative on this server. Cross-server visibility is
// intentionally outside this infrastructure lab's application path.
func (s *server) visibleEntities(excludeUID string, latitude, longitude, zoom float64, spatialRelevance bool) ([]visibleEntity, int) {
	currentTick := s.tick.Load()
	worldPixels, x, y := 0.0, 0.0, 0.0
	if spatialRelevance {
		worldPixels = 256 * math.Exp2(zoom)
		x, y = normalizedPosition(latitude, longitude)
	}

	s.entityMu.Lock()
	entities := make([]visibleEntity, 0, len(s.entities))
	entityCount := len(s.entities)
	for uid, entity := range s.entities {
		if uid == excludeUID || currentTick-entity.lastInputTick >= visibilityActiveTicks {
			continue
		}
		if spatialRelevance {
			entityX, entityY := normalizedPosition(entity.latitude, entity.longitude)
			if math.Hypot(wrappedDistance(x, entityX), wrappedDistance(y, entityY))*worldPixels > visibilityRadiusPixels {
				continue
			}
		}
		entities = append(entities, visibleEntity{
			UID: uid, Latitude: entity.latitude, Longitude: entity.longitude, Sequence: entity.sequence,
		})
	}
	s.entityMu.Unlock()
	return entities, entityCount
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
