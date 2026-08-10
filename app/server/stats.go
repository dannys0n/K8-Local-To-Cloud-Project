package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"time"
)

func (s *server) recordTickDuration(duration time.Duration) {
	bits := math.Float64bits(duration.Seconds())
	s.tickDurationBits.Store(bits)
	for {
		current := s.maxTickDurationBits.Load()
		if math.Float64frombits(current) >= duration.Seconds() || s.maxTickDurationBits.CompareAndSwap(current, bits) {
			return
		}
	}
}

func (s *server) serveMetrics(ctx context.Context, address string, logger *slog.Logger) {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusOK)
		_, _ = writer.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/metrics", func(writer http.ResponseWriter, _ *http.Request) {
		s.entityMu.Lock()
		entityCount := len(s.entities)
		s.entityMu.Unlock()
		current := s.currentAssignment()
		assigned := 0
		serverID := ""
		locationID := int64(0)
		generation := int64(0)
		latitude := 0.0
		longitude := 0.0
		if current != nil {
			assigned = 1
			serverID = current.ServerID
			locationID = current.LocationID
			generation = current.Generation
			latitude = current.Latitude
			longitude = current.Longitude
		}
		writer.Header().Set("Content-Type", "text/plain; version=0.0.4")
		_, _ = fmt.Fprintf(writer,
			"# TYPE tcp_server_entities gauge\n"+
				"tcp_server_entities %d\n"+
				"# TYPE tcp_server_connections gauge\n"+
				"tcp_server_connections %d\n"+
				"# TYPE tcp_server_connections_accepted_total counter\n"+
				"tcp_server_connections_accepted_total %d\n"+
				"# TYPE tcp_server_input_commands_total counter\n"+
				"tcp_server_input_commands_total %d\n"+
				"# TYPE tcp_server_input_queue_full_total counter\n"+
				"tcp_server_input_queue_full_total %d\n"+
				"# TYPE tcp_server_handoff_attempts_total counter\n"+
				"tcp_server_handoff_attempts_total %d\n"+
				"# TYPE tcp_server_handoff_failures_total counter\n"+
				"tcp_server_handoff_failures_total %d\n"+
				"# TYPE tcp_server_state_errors_total counter\n"+
				"tcp_server_state_errors_total %d\n"+
				"# TYPE tcp_server_tick_duration_seconds gauge\n"+
				"tcp_server_tick_duration_seconds %g\n"+
				"# TYPE tcp_server_tick_duration_max_seconds gauge\n"+
				"tcp_server_tick_duration_max_seconds %g\n"+
				"# TYPE tcp_server_tick_interval_seconds gauge\n"+
				"tcp_server_tick_interval_seconds %g\n"+
				"# TYPE tcp_server_topology_revision gauge\n"+
				"tcp_server_topology_revision %d\n"+
				"# TYPE tcp_server_assignment gauge\n"+
				"tcp_server_assignment{server_id=%s,location_id=%q} %d\n"+
				"# TYPE tcp_server_assignment_generation gauge\n"+
				"tcp_server_assignment_generation{server_id=%s,location_id=%q} %d\n"+
				"# TYPE tcp_server_assignment_latitude_degrees gauge\n"+
				"tcp_server_assignment_latitude_degrees{server_id=%s,location_id=%q} %g\n"+
				"# TYPE tcp_server_assignment_longitude_degrees gauge\n"+
				"tcp_server_assignment_longitude_degrees{server_id=%s,location_id=%q} %g\n",
			entityCount, s.activeConnections.Load(), s.acceptedConnections.Load(),
			s.inputCommands.Load(), s.inputQueueFull.Load(), s.handoffAttempts.Load(),
			s.handoffFailures.Load(), s.stateErrors.Load(),
			math.Float64frombits(s.tickDurationBits.Load()), math.Float64frombits(s.maxTickDurationBits.Load()),
			s.tickInterval.Seconds(), s.topologyRevision.Load(),
			strconv.Quote(serverID), strconv.FormatInt(locationID, 10), assigned,
			strconv.Quote(serverID), strconv.FormatInt(locationID, 10), generation,
			strconv.Quote(serverID), strconv.FormatInt(locationID, 10), latitude,
			strconv.Quote(serverID), strconv.FormatInt(locationID, 10), longitude,
		)
	})
	server := &http.Server{Addr: address, Handler: mux, ReadHeaderTimeout: 2 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("metrics server", "error", err)
	}
}
