package main

import (
	"context"
	"fmt"
	"io"
	"math"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

var valkeyLatencyBuckets = [...]float64{0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1}

type valkeyLatencyHistogram struct {
	count   atomic.Uint64
	sumBits atomic.Uint64
	buckets [len(valkeyLatencyBuckets)]atomic.Uint64
}

func (h *valkeyLatencyHistogram) observe(duration time.Duration) {
	seconds := duration.Seconds()
	h.count.Add(1)
	for {
		old := h.sumBits.Load()
		if h.sumBits.CompareAndSwap(old, math.Float64bits(math.Float64frombits(old)+seconds)) {
			break
		}
	}
	for index, upper := range valkeyLatencyBuckets {
		if seconds <= upper {
			h.buckets[index].Add(1)
		}
	}
}

func (h *valkeyLatencyHistogram) write(writer io.Writer) {
	const name = "tcp_server_valkey_duration_seconds"
	_, _ = fmt.Fprintf(writer, "# HELP %s Server Valkey command or pipeline round-trip duration.\n# TYPE %s histogram\n", name, name)
	for index, upper := range valkeyLatencyBuckets {
		_, _ = fmt.Fprintf(writer, "%s_bucket{le=%q} %d\n", name, fmt.Sprint(upper), h.buckets[index].Load())
	}
	_, _ = fmt.Fprintf(writer, "%s_bucket{le=\"+Inf\"} %d\n%s_sum %g\n%s_count %d\n",
		name, h.count.Load(), name, math.Float64frombits(h.sumBits.Load()), name, h.count.Load())
}

type valkeyLatencyHook struct{ histogram *valkeyLatencyHistogram }

func (hook valkeyLatencyHook) DialHook(next redis.DialHook) redis.DialHook { return next }

func (hook valkeyLatencyHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, command redis.Cmder) error {
		started := time.Now()
		err := next(ctx, command)
		hook.histogram.observe(time.Since(started))
		return err
	}
}

func (hook valkeyLatencyHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, commands []redis.Cmder) error {
		started := time.Now()
		err := next(ctx, commands)
		hook.histogram.observe(time.Since(started))
		return err
	}
}
