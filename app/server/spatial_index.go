package main

import (
	"math"
	"sort"
)

type locationPoint struct {
	location locationDefinition
	coord    [3]float64
}

type locationNode struct {
	point       locationPoint
	axis        int
	left, right *locationNode
}

func buildLocationIndex(locations []locationDefinition) *locationNode {
	points := make([]locationPoint, 0, len(locations))
	for _, location := range locations {
		latitude := location.latitude * math.Pi / 180
		longitude := location.longitude * math.Pi / 180
		points = append(points, locationPoint{location: location, coord: [3]float64{
			math.Cos(latitude) * math.Cos(longitude),
			math.Cos(latitude) * math.Sin(longitude),
			math.Sin(latitude),
		}})
	}
	return buildLocationNode(points, 0)
}

func buildLocationNode(points []locationPoint, depth int) *locationNode {
	if len(points) == 0 {
		return nil
	}
	axis := depth % 3
	sort.Slice(points, func(i, j int) bool { return points[i].coord[axis] < points[j].coord[axis] })
	middle := len(points) / 2
	return &locationNode{
		point: points[middle], axis: axis,
		left: buildLocationNode(points[:middle], depth+1),
		right: buildLocationNode(points[middle+1:], depth+1),
	}
}

func (root *locationNode) nearest(latitude, longitude float64, excluded int64) int64 {
	if root == nil {
		return 0
	}
	latitude *= math.Pi / 180
	longitude *= math.Pi / 180
	target := [3]float64{
		math.Cos(latitude) * math.Cos(longitude),
		math.Cos(latitude) * math.Sin(longitude),
		math.Sin(latitude),
	}
	bestID, bestDistance := int64(0), math.Inf(1)
	var search func(*locationNode)
	search = func(node *locationNode) {
		if node == nil {
			return
		}
		delta := target[node.axis] - node.point.coord[node.axis]
		near, far := node.left, node.right
		if delta > 0 {
			near, far = node.right, node.left
		}
		search(near)
		if node.point.location.locationID != excluded {
			distance := squaredDistance(target, node.point.coord)
			id := node.point.location.locationID
			if distance < bestDistance || (distance == bestDistance && (bestID == 0 || id < bestID)) {
				bestID, bestDistance = id, distance
			}
		}
		if delta*delta <= bestDistance {
			search(far)
		}
	}
	search(root)
	return bestID
}

func squaredDistance(left, right [3]float64) float64 {
	x, y, z := left[0]-right[0], left[1]-right[1], left[2]-right[2]
	return x*x + y*y + z*z
}
