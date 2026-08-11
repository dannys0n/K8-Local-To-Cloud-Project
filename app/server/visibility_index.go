package main

import (
	"sort"
)

type visibilityLocationPoint struct {
	locationID int64
	coord      [2]float64
}

type visibilityLocationNode struct {
	point       visibilityLocationPoint
	axis        int
	left, right *visibilityLocationNode
}

func buildVisibilityLocationIndex(locations []locationDefinition) *visibilityLocationNode {
	points := make([]visibilityLocationPoint, 0, len(locations))
	for _, location := range locations {
		x, y := normalizedPosition(location.latitude, location.longitude)
		points = append(points, visibilityLocationPoint{locationID: location.locationID, coord: [2]float64{x, y}})
	}
	return buildVisibilityLocationNode(points, 0)
}

func buildVisibilityLocationNode(points []visibilityLocationPoint, depth int) *visibilityLocationNode {
	if len(points) == 0 {
		return nil
	}
	axis := depth % 2
	sort.Slice(points, func(i, j int) bool { return points[i].coord[axis] < points[j].coord[axis] })
	middle := len(points) / 2
	return &visibilityLocationNode{
		point: points[middle], axis: axis,
		left:  buildVisibilityLocationNode(points[:middle], depth+1),
		right: buildVisibilityLocationNode(points[middle+1:], depth+1),
	}
}

func (root *visibilityLocationNode) within(latitude, longitude, radius float64, excluded int64) []int64 {
	if root == nil || radius < 0 {
		return nil
	}
	x, y := normalizedPosition(latitude, longitude)
	matched := make(map[int64]struct{})
	for _, wrappedX := range []float64{x - 1, x, x + 1} {
		for _, wrappedY := range []float64{y - 1, y, y + 1} {
			root.collectWithin([2]float64{wrappedX, wrappedY}, radius*radius, excluded, matched)
		}
	}
	ids := make([]int64, 0, len(matched))
	for id := range matched {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids
}

func (root *visibilityLocationNode) collectWithin(target [2]float64, radiusSquared float64, excluded int64, matched map[int64]struct{}) {
	if root == nil {
		return
	}
	delta := target[root.axis] - root.point.coord[root.axis]
	near, far := root.left, root.right
	if delta > 0 {
		near, far = root.right, root.left
	}
	near.collectWithin(target, radiusSquared, excluded, matched)
	dx := target[0] - root.point.coord[0]
	dy := target[1] - root.point.coord[1]
	if root.point.locationID != excluded && dx*dx+dy*dy <= radiusSquared {
		matched[root.point.locationID] = struct{}{}
	}
	if delta*delta <= radiusSquared {
		far.collectWithin(target, radiusSquared, excluded, matched)
	}
}
