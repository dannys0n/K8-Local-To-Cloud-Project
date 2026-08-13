package main

import (
	"fmt"
	"io"
	"math"
	"sync/atomic"
	"time"
)

var latencyBuckets = [...]float64{0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1}

type latencyHistogram struct {
	count   atomic.Uint64
	sumBits atomic.Uint64
	buckets [len(latencyBuckets)]atomic.Uint64
}

func (h *latencyHistogram) observe(duration time.Duration) {
	seconds := duration.Seconds()
	h.count.Add(1)
	atomicFloatAdd(&h.sumBits, seconds)
	for index, upper := range latencyBuckets {
		if seconds <= upper {
			h.buckets[index].Add(1)
		}
	}
}

func (h *latencyHistogram) write(writer io.Writer, name, help string) {
	_, _ = fmt.Fprintf(writer, "# HELP %s %s\n# TYPE %s histogram\n", name, help, name)
	for index, upper := range latencyBuckets {
		_, _ = fmt.Fprintf(writer, "%s_bucket{le=%q} %d\n", name, fmt.Sprint(upper), h.buckets[index].Load())
	}
	_, _ = fmt.Fprintf(writer, "%s_bucket{le=\"+Inf\"} %d\n%s_sum %g\n%s_count %d\n",
		name, h.count.Load(), name, math.Float64frombits(h.sumBits.Load()), name, h.count.Load())
}

func atomicFloatAdd(value *atomic.Uint64, delta float64) {
	for {
		old := value.Load()
		if value.CompareAndSwap(old, math.Float64bits(math.Float64frombits(old)+delta)) {
			return
		}
	}
}
