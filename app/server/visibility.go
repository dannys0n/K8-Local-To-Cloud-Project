package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"time"

	"github.com/redis/go-redis/v9"
)

const visibilityCellCount = 32

type spatialGrid struct {
	columns int
	rows    int
	wrapX   bool
	wrapY   bool
}

var visibilityGrid = spatialGrid{
	columns: visibilityCellCount,
	rows:    visibilityCellCount,
	wrapX:   true,
	wrapY:   true,
}

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
	groups := make(map[string][]visibleEntity)
	currentTick := s.tick.Load()
	s.entityMu.Lock()
	for uid, entity := range s.entities {
		if currentTick-entity.lastInputTick >= visibilityActiveTicks {
			continue
		}
		visible := visibleEntity{
			UID: uid, Latitude: entity.latitude, Longitude: entity.longitude, Sequence: entity.sequence,
		}
		x, y := normalizedPosition(entity.latitude, entity.longitude)
		cellX, cellY := visibilityGrid.cell(x, y)
		key := visibilityCellKey(cellX, cellY)
		groups[key] = append(groups[key], visible)
	}
	s.entityMu.Unlock()

	field := visibilityPublisher(current)
	now := time.Now().UnixMilli()
	pipe := s.redis.Pipeline()
	currentCells := make(map[string]struct{}, len(groups))
	for key, entities := range groups {
		body, err := json.Marshal(visibilitySnapshot{
			Generation: current.Generation, UpdatedAt: now, Entities: entities,
		})
		if err != nil {
			continue
		}
		currentCells[key] = struct{}{}
		pipe.HSet(ctx, key, field, body)
		pipe.Expire(ctx, key, visibilityLease)
	}

	s.publishedMu.Lock()
	for key := range s.publishedCells {
		if _, exists := currentCells[key]; !exists || s.publishedField != field {
			pipe.HDel(ctx, key, s.publishedField)
		}
	}
	_, err := pipe.Exec(ctx)
	if err == nil {
		s.publishedField = field
		s.publishedCells = currentCells
	}
	s.publishedMu.Unlock()
}

func (s *server) pullVisibility(ctx context.Context) {
	keys := s.interestCells()
	if len(keys) == 0 {
		s.storeVisible(nil)
		return
	}

	pipe := s.redis.Pipeline()
	results := make([]*redis.MapStringStringCmd, len(keys))
	for index, key := range keys {
		results[index] = pipe.HGetAll(ctx, key)
	}
	if _, err := pipe.Exec(ctx); err != nil {
		s.expireVisible()
		return
	}

	cutoff := time.Now().Add(-visibilityLease).UnixMilli()
	latest := make(map[string]visibleEntity)
	updated := make(map[string]int64)
	for _, result := range results {
		for _, body := range result.Val() {
			var snapshot visibilitySnapshot
			if json.Unmarshal([]byte(body), &snapshot) != nil || snapshot.UpdatedAt < cutoff {
				continue
			}
			for _, entity := range snapshot.Entities {
				current, exists := latest[entity.UID]
				if !exists || entity.Sequence > current.Sequence ||
					entity.Sequence == current.Sequence && snapshot.UpdatedAt > updated[entity.UID] {
					latest[entity.UID] = entity
					updated[entity.UID] = snapshot.UpdatedAt
				}
			}
		}
	}
	visible := make([]visibleEntity, 0, len(latest))
	for _, entity := range latest {
		visible = append(visible, entity)
	}
	s.storeVisible(visible)
}

func (s *server) interestCells() []string {
	currentTick := s.tick.Load()
	cells := make(map[string]struct{})
	s.entityMu.Lock()
	for _, entity := range s.entities {
		if currentTick-entity.lastInputTick >= visibilityActiveTicks {
			continue
		}
		x, y := normalizedPosition(entity.latitude, entity.longitude)
		radius := visibilityRadiusPixels / (256 * math.Exp2(entity.viewZoom))
		for _, cell := range visibilityGrid.overlappingCells(x, y, radius) {
			cells[visibilityCellKey(cell[0], cell[1])] = struct{}{}
		}
	}
	s.entityMu.Unlock()
	keys := make([]string, 0, len(cells))
	for key := range cells {
		keys = append(keys, key)
	}
	return keys
}

func (s *server) visibleEntities(excludeUID string, latitude, longitude, zoom float64) []visibleEntity {
	worldPixels := 256 * math.Exp2(zoom)
	x, y := normalizedPosition(latitude, longitude)
	s.visibilityMu.RLock()
	entities := make([]visibleEntity, 0, len(s.visible))
	for _, entity := range s.visible {
		if entity.UID == excludeUID {
			continue
		}
		entityX, entityY := normalizedPosition(entity.Latitude, entity.Longitude)
		deltaX := visibilityGrid.distance(x, entityX, visibilityGrid.wrapX)
		deltaY := visibilityGrid.distance(y, entityY, visibilityGrid.wrapY)
		if math.Hypot(deltaX, deltaY)*worldPixels <= visibilityRadiusPixels {
			entities = append(entities, entity)
		}
	}
	s.visibilityMu.RUnlock()
	return entities
}

func (s *server) storeVisible(entities []visibleEntity) {
	s.visibilityMu.Lock()
	s.visible = entities
	s.visibilityReadAt = time.Now()
	s.visibilityMu.Unlock()
}

func (s *server) expireVisible() {
	s.visibilityMu.Lock()
	if s.visibilityReadAt.IsZero() || time.Since(s.visibilityReadAt) >= visibilityLease {
		s.visible = nil
	}
	s.visibilityMu.Unlock()
}

func (s *server) deleteVisibility(ctx context.Context, current *assignment) {
	field := visibilityPublisher(current)
	s.publishedMu.Lock()
	pipe := s.redis.Pipeline()
	for key := range s.publishedCells {
		if s.publishedField == field {
			pipe.HDel(ctx, key, field)
		}
	}
	_, _ = pipe.Exec(ctx)
	if s.publishedField == field {
		s.publishedField = ""
		s.publishedCells = nil
	}
	s.publishedMu.Unlock()
}

func normalizedPosition(latitude, longitude float64) (float64, float64) {
	return (longitude + 180) / 360, mercatorY(latitude)
}

func mercatorY(latitude float64) float64 {
	latitude = math.Max(-mercatorLatitudeLimit, math.Min(mercatorLatitudeLimit, latitude))
	projected := math.Log(math.Tan(math.Pi/4 + latitude*math.Pi/360))
	return (1 - projected/math.Pi) / 2
}

func (grid spatialGrid) cell(x, y float64) (int, int) {
	return grid.index(int(math.Floor(x*float64(grid.columns))), grid.columns, grid.wrapX),
		grid.index(int(math.Floor(y*float64(grid.rows))), grid.rows, grid.wrapY)
}

func (grid spatialGrid) overlappingCells(x, y, radius float64) [][2]int {
	minX := int(math.Floor((x - radius) * float64(grid.columns)))
	maxX := int(math.Floor((x + radius) * float64(grid.columns)))
	minY := int(math.Floor((y - radius) * float64(grid.rows)))
	maxY := int(math.Floor((y + radius) * float64(grid.rows)))
	result := make([][2]int, 0, (maxX-minX+1)*(maxY-minY+1))
	seen := make(map[[2]int]struct{})
	for cellY := minY; cellY <= maxY; cellY++ {
		for cellX := minX; cellX <= maxX; cellX++ {
			indexed := [2]int{
				grid.index(cellX, grid.columns, grid.wrapX),
				grid.index(cellY, grid.rows, grid.wrapY),
			}
			if indexed[0] < 0 || indexed[1] < 0 {
				continue
			}
			if _, exists := seen[indexed]; !exists {
				seen[indexed] = struct{}{}
				result = append(result, indexed)
			}
		}
	}
	return result
}

func (grid spatialGrid) index(value, size int, wrap bool) int {
	if wrap {
		return (value%size + size) % size
	}
	if value < 0 || value >= size {
		return -1
	}
	return value
}

func (grid spatialGrid) distance(a, b float64, wrap bool) float64 {
	distance := math.Abs(a - b)
	if wrap {
		distance = math.Min(distance, 1-distance)
	}
	return distance
}

func visibilityCellKey(x, y int) string {
	return fmt.Sprintf("tcp-lab:visibility:{%d:%d}", x, y)
}

func visibilityPublisher(current *assignment) string {
	return current.ServerID
}
