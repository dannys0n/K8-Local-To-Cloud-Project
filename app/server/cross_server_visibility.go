package main

import (
	"context"
	"encoding/json"
	"math"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	visibilitySnapshotTTL = 2 * time.Second
	spatialGridSize       = 64
)

type spatialCell struct {
	X int `json:"x"`
	Y int `json:"y"`
}

type remoteCellKey struct {
	LocationID int64
	Cell       spatialCell
}

type visibilityCellSnapshot struct {
	LocationID int64           `json:"location_id"`
	Published  int64           `json:"published"`
	Entities   []visibleEntity `json:"entities"`
}

type visibilityCellManifest struct {
	LocationID int64         `json:"location_id"`
	Published  int64         `json:"published"`
	Cells      []spatialCell `json:"cells"`
}

func visibilityManifestKey(locationID int64) string {
	return "tcp-lab:visibility:{" + strconv.FormatInt(locationID, 10) + "}:cells"
}

func visibilityCellKey(key remoteCellKey) string {
	return "tcp-lab:visibility:{" + strconv.FormatInt(key.LocationID, 10) + "}:" +
		strconv.Itoa(key.Cell.X) + ":" + strconv.Itoa(key.Cell.Y)
}

func wrapCell(value int) int {
	value %= spatialGridSize
	if value < 0 {
		value += spatialGridSize
	}
	return value
}

func worldToCell(x, y float64) spatialCell {
	return spatialCell{
		X: wrapCell(int(math.Floor(x * spatialGridSize))),
		Y: wrapCell(int(math.Floor(y * spatialGridSize))),
	}
}

// cellsForInterest returns a conservative wrapped grid broadphase. Exact
// client-specific distance filtering remains in visibleEntities.
func cellsForInterest(x, y, radius float64) []spatialCell {
	minimumX := int(math.Floor((x - radius) * spatialGridSize))
	maximumX := int(math.Floor((x + radius) * spatialGridSize))
	minimumY := int(math.Floor((y - radius) * spatialGridSize))
	maximumY := int(math.Floor((y + radius) * spatialGridSize))

	cells := make(map[spatialCell]struct{}, (maximumX-minimumX+1)*(maximumY-minimumY+1))
	for cellX := minimumX; cellX <= maximumX; cellX++ {
		for cellY := minimumY; cellY <= maximumY; cellY++ {
			cells[spatialCell{X: wrapCell(cellX), Y: wrapCell(cellY)}] = struct{}{}
		}
	}
	result := make([]spatialCell, 0, len(cells))
	for cell := range cells {
		result = append(result, cell)
	}
	return result
}

func (s *server) runVisibilityExchange(ctx context.Context) {
	ticker := time.NewTicker(s.tickInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.exchangeVisibility(ctx)
		}
	}
}

func (s *server) exchangeVisibility(ctx context.Context) {
	current := s.currentAssignment()
	if current == nil {
		s.replaceRemoteVisibility(nil, time.Now())
		return
	}

	currentTick := s.tick.Load()
	localCells := make(map[spatialCell][]visibleEntity)
	requestedCells := make(map[int64]map[spatialCell]struct{})
	s.entityMu.Lock()
	for uid, entity := range s.entities {
		if currentTick-entity.lastInputTick >= visibilityActiveTicks {
			continue
		}
		visible := visibleEntity{UID: uid, Latitude: entity.latitude, Longitude: entity.longitude, Sequence: entity.sequence}
		x, y := normalizedPosition(entity.latitude, entity.longitude)
		cell := worldToCell(x, y)
		localCells[cell] = append(localCells[cell], visible)

		if !entity.crossServerRelevant {
			continue
		}
		radius := visibilityRadiusPixels / (256 * math.Exp2(entity.viewZoom))
		remoteLocations := s.candidateRemoteLocations(x, y, radius, current)
		cells := cellsForInterest(x, y, radius)
		for _, locationID := range remoteLocations {
			if requestedCells[locationID] == nil {
				requestedCells[locationID] = make(map[spatialCell]struct{})
			}
			for _, candidateCell := range cells {
				requestedCells[locationID][candidateCell] = struct{}{}
			}
		}
	}
	s.entityMu.Unlock()

	now := time.Now()
	manifest := visibilityCellManifest{
		LocationID: current.LocationID,
		Published:  now.UnixMilli(),
		Cells:      make([]spatialCell, 0, len(localCells)),
	}
	for cell := range localCells {
		manifest.Cells = append(manifest.Cells, cell)
	}
	manifestPayload, err := json.Marshal(manifest)
	if err != nil {
		return
	}

	queryCtx, cancel := context.WithTimeout(ctx, 200*time.Millisecond)
	defer cancel()
	manifestCommands := make(map[int64]*redis.StringCmd, len(requestedCells))
	_, _ = s.valkey.Pipelined(queryCtx, func(pipe redis.Pipeliner) error {
		pipe.Set(queryCtx, visibilityManifestKey(current.LocationID), manifestPayload, visibilitySnapshotTTL)
		for cell, entities := range localCells {
			payload, err := json.Marshal(visibilityCellSnapshot{
				LocationID: current.LocationID, Published: now.UnixMilli(), Entities: entities,
			})
			if err != nil {
				continue
			}
			key := remoteCellKey{LocationID: current.LocationID, Cell: cell}
			pipe.Set(queryCtx, visibilityCellKey(key), payload, visibilitySnapshotTTL)
		}
		for locationID := range requestedCells {
			manifestCommands[locationID] = pipe.Get(queryCtx, visibilityManifestKey(locationID))
		}
		return nil
	})

	wanted := make(map[remoteCellKey]struct{})
	for locationID, command := range manifestCommands {
		value, commandErr := command.Bytes()
		if commandErr != nil {
			continue
		}
		var remoteManifest visibilityCellManifest
		if json.Unmarshal(value, &remoteManifest) != nil || remoteManifest.LocationID != locationID ||
			now.Sub(time.UnixMilli(remoteManifest.Published)) >= visibilitySnapshotTTL {
			continue
		}
		for _, cell := range remoteManifest.Cells {
			if _, requested := requestedCells[locationID][cell]; requested {
				wanted[remoteCellKey{LocationID: locationID, Cell: cell}] = struct{}{}
			}
		}
	}

	commands := make(map[remoteCellKey]*redis.StringCmd, len(wanted))
	if len(wanted) > 0 {
		_, _ = s.valkey.Pipelined(queryCtx, func(pipe redis.Pipeliner) error {
			for key := range wanted {
				commands[key] = pipe.Get(queryCtx, visibilityCellKey(key))
			}
			return nil
		})
	}

	updates := make(map[remoteCellKey]visibilityCellSnapshot, len(commands))
	for key, command := range commands {
		value, err := command.Bytes()
		if err != nil {
			continue
		}
		var snapshot visibilityCellSnapshot
		if json.Unmarshal(value, &snapshot) == nil && snapshot.LocationID == key.LocationID {
			updates[key] = snapshot
		}
	}
	s.mergeRemoteVisibility(wanted, updates, now)
}

// candidateRemoteLocations uses the owner seed distance plus twice the AOI
// radius as a safe Voronoi broadphase. Cells and exact entity distance decide
// final visibility.
func (s *server) candidateRemoteLocations(x, y, radius float64, current *assignment) []int64 {
	ownerX, ownerY := normalizedPosition(current.Latitude, current.Longitude)
	candidateRadius := math.Hypot(wrappedDistance(x, ownerX), wrappedDistance(y, ownerY)) + 2*radius
	s.topologyMu.RLock()
	defer s.topologyMu.RUnlock()
	if s.visibilityIndex == nil {
		return nil
	}
	latitude := inverseMercatorY(y)
	longitude := x*360 - 180
	return s.visibilityIndex.within(latitude, longitude, candidateRadius, current.LocationID)
}

func inverseMercatorY(y float64) float64 {
	return math.Atan(math.Sinh(math.Pi*(1-2*y))) * 180 / math.Pi
}

func (s *server) mergeRemoteVisibility(wanted map[remoteCellKey]struct{}, updates map[remoteCellKey]visibilityCellSnapshot, now time.Time) {
	s.remoteEntityMu.Lock()
	defer s.remoteEntityMu.Unlock()
	for cell, locations := range s.remoteEntities {
		for locationID, snapshot := range locations {
			key := remoteCellKey{LocationID: locationID, Cell: cell}
			_, stillWanted := wanted[key]
			if !stillWanted || now.Sub(time.UnixMilli(snapshot.Published)) >= visibilitySnapshotTTL {
				delete(locations, locationID)
			}
		}
		if len(locations) == 0 {
			delete(s.remoteEntities, cell)
		}
	}
	for key, snapshot := range updates {
		if now.Sub(time.UnixMilli(snapshot.Published)) < visibilitySnapshotTTL {
			if s.remoteEntities[key.Cell] == nil {
				s.remoteEntities[key.Cell] = make(map[int64]visibilityCellSnapshot)
			}
			s.remoteEntities[key.Cell][key.LocationID] = snapshot
		}
	}
}

func (s *server) replaceRemoteVisibility(snapshots map[remoteCellKey]visibilityCellSnapshot, now time.Time) {
	s.remoteEntityMu.Lock()
	defer s.remoteEntityMu.Unlock()
	clear(s.remoteEntities)
	for key, snapshot := range snapshots {
		if now.Sub(time.UnixMilli(snapshot.Published)) < visibilitySnapshotTTL {
			if s.remoteEntities[key.Cell] == nil {
				s.remoteEntities[key.Cell] = make(map[int64]visibilityCellSnapshot)
			}
			s.remoteEntities[key.Cell][key.LocationID] = snapshot
		}
	}
}
