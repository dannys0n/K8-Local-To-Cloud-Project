package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"time"
)

const statsPage = `<!doctype html><html><head><meta charset="utf-8"><title>Gateway stats</title><style>body{background:#0d1117;color:#e6edf3;font:14px monospace;margin:2rem}pre{background:#161b22;border:1px solid #30363d;padding:1rem;overflow:auto}</style></head><body><h1>Gateway replica</h1><p>Read-only live state; refreshes every two seconds.</p><pre id="data">loading</pre><script>async function r(){let x=await fetch('/stats',{cache:'no-store'});document.querySelector('#data').textContent=JSON.stringify(await x.json(),null,2)}r();setInterval(r,2000)</script></body></html>`

func (g *gateway) serveHTTP(ctx context.Context, address string) {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusOK)
		_, _ = writer.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/stats", func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		sessions := []session{}
		includeSessions := request.URL.Query().Get("include_sessions") != "false"
		if includeSessions {
			g.mu.RLock()
			sessions = make([]session, 0, len(g.sessions))
			for _, item := range g.sessions {
				sessions = append(sessions, item)
			}
			g.mu.RUnlock()
			sort.Slice(sessions, func(i, j int) bool { return sessions[i].ID < sessions[j].ID })
		}
		g.mu.RLock()
		sessionCount := len(g.sessions)
		g.mu.RUnlock()
		routes := g.routesSnapshot()
		_ = json.NewEncoder(writer).Encode(map[string]any{"instance": g.instance, "accepted": g.accepted.Load(), "session_count": sessionCount, "ready_session_count": g.readySessions.Load(), "sessions": sessions, "sessions_included": includeSessions, "routes": routes})
	})
	mux.HandleFunc("/metrics", func(writer http.ResponseWriter, _ *http.Request) {
		g.mu.RLock()
		sessionCount := len(g.sessions)
		g.mu.RUnlock()
		writer.Header().Set("Content-Type", "text/plain; version=0.0.4")
		_, _ = fmt.Fprintf(writer,
			"# TYPE tcp_gateway_sessions gauge\n"+
				"tcp_gateway_sessions %d\n"+
				"# TYPE tcp_gateway_ready_sessions gauge\n"+
				"tcp_gateway_ready_sessions %d\n"+
				"# TYPE tcp_gateway_accepted_total counter\n"+
				"tcp_gateway_accepted_total %d\n",
			sessionCount, g.readySessions.Load(), g.accepted.Load(),
		)
	})
	mux.HandleFunc("/", func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = writer.Write([]byte(statsPage))
	})
	server := &http.Server{Addr: address, Handler: mux, ReadHeaderTimeout: 2 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
	}()
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		g.logger.Error("stats server", "error", err)
	}
}
