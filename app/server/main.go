package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

type server struct {
	name      string
	location  string
	statePath string
	mu        sync.Mutex
	counter   uint64
}

type response struct {
	Server   string `json:"server"`
	Location string `json:"location"`
	Counter  uint64 `json:"counter"`
	Message  string `json:"message"`
	Time     string `json:"time"`
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	listenAddr := envOrDefault("LISTEN_ADDR", ":7000")
	statePath := envOrDefault("STATE_PATH", "/data/counter")
	name := envOrDefault("POD_NAME", hostname())

	s := &server{name: name, location: locationFor(name), statePath: statePath}
	if err := s.load(); err != nil {
		logger.Error("load state", "error", err)
		os.Exit(1)
	}

	listener, err := net.Listen("tcp", listenAddr)
	if err != nil {
		logger.Error("listen", "address", listenAddr, "error", err)
		os.Exit(1)
	}
	defer listener.Close()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	logger.Info("server ready", "server", s.name, "address", listenAddr, "counter", s.counter)

	go func() {
		<-ctx.Done()
		_ = listener.Close()
	}()

	var connections sync.WaitGroup
	for {
		conn, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) {
				break
			}
			logger.Warn("accept connection", "error", err)
			continue
		}

		connections.Add(1)
		go func() {
			defer connections.Done()
			s.handleConnection(ctx, conn, logger)
		}()
	}

	connections.Wait()
	logger.Info("server stopped", "server", s.name)
}

func (s *server) handleConnection(ctx context.Context, conn net.Conn, logger *slog.Logger) {
	defer conn.Close()
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			_ = conn.Close()
		case <-done:
		}
	}()

	remote := conn.RemoteAddr().String()
	logger.Info("client connected", "server", s.name, "remote", remote)
	defer logger.Info("client disconnected", "server", s.name, "remote", remote)

	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 4096), 64*1024)
	writer := bufio.NewWriter(conn)

	for scanner.Scan() {
		select {
		case <-ctx.Done():
			return
		default:
		}

		message := strings.TrimSpace(scanner.Text())
		if message == "" {
			continue
		}

		responseMessage := message
		var count uint64
		if requested, found := strings.CutPrefix(message, "@location "); found {
			if requested != "any" && requested != s.location {
				_, _ = fmt.Fprintf(writer, `{"error":"wrong location","server":%q,"location":%q}`+"\n", s.name, s.location)
				_ = writer.Flush()
				return
			}
			count = s.currentCounter()
			responseMessage = "connected"
		} else {
			var err error
			count, err = s.increment()
			if err != nil {
				logger.Error("persist counter", "server", s.name, "error", err)
				_, _ = fmt.Fprintln(writer, `{"error":"failed to persist counter"}`)
				_ = writer.Flush()
				return
			}
		}

		body, err := json.Marshal(response{
			Server:   s.name,
			Location: s.location,
			Counter:  count,
			Message:  responseMessage,
			Time:     time.Now().UTC().Format(time.RFC3339Nano),
		})
		if err != nil {
			return
		}

		if _, err := writer.Write(append(body, '\n')); err != nil {
			return
		}
		if err := writer.Flush(); err != nil {
			return
		}
	}

	if err := scanner.Err(); err != nil {
		logger.Debug("connection read ended", "remote", remote, "error", err)
	}
}

func (s *server) currentCounter() uint64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.counter
}

func locationFor(serverName string) string {
	locations := []string{"los-angeles", "new-york", "london", "singapore", "frankfurt"}
	separator := strings.LastIndexByte(serverName, '-')
	if separator < 0 {
		return "unknown"
	}
	ordinal, err := strconv.Atoi(serverName[separator+1:])
	if err != nil || ordinal < 0 || ordinal >= len(locations) {
		return "unknown"
	}
	return locations[ordinal]
}

func (s *server) load() error {
	data, err := os.ReadFile(s.statePath)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}

	value := strings.TrimSpace(string(data))
	if value == "" {
		return nil
	}
	counter, err := strconv.ParseUint(value, 10, 64)
	if err != nil {
		return fmt.Errorf("parse counter: %w", err)
	}
	s.counter = counter
	return nil
}

func (s *server) increment() (uint64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.counter++
	if err := os.MkdirAll(filepath.Dir(s.statePath), 0o750); err != nil {
		return 0, err
	}

	temporary := s.statePath + ".tmp"
	if err := os.WriteFile(temporary, []byte(strconv.FormatUint(s.counter, 10)+"\n"), 0o640); err != nil {
		return 0, err
	}
	if err := os.Rename(temporary, s.statePath); err != nil {
		return 0, err
	}
	return s.counter, nil
}

func envOrDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func hostname() string {
	value, err := os.Hostname()
	if err != nil || value == "" {
		return "unknown-server"
	}
	return value
}
