package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"hash/fnv"
	"math"
	mathrand "math/rand"
	"net"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const (
	inputInterval = time.Second / 30
	ioTimeout     = time.Second
)

type counters struct{ disconnected, gateway, ready atomic.Int64 }

type client struct {
	host                string
	uid                 string
	state               int
	counts              *counters
	conn                net.Conn
	reader              *bufio.Reader
	latitude, longitude float64
	hasPosition         bool
}

func (c *client) setState(next int) {
	if c.state == next {
		return
	}
	c.adjust(c.state, -1)
	c.state = next
	c.adjust(next, 1)
}

func (c *client) adjust(state int, delta int64) {
	switch state {
	case 0:
		c.counts.disconnected.Add(delta)
	case 1:
		c.counts.gateway.Add(delta)
	case 2:
		c.counts.ready.Add(delta)
	}
}

func (c *client) close() {
	if c.conn != nil {
		_ = c.conn.Close()
	}
	c.conn, c.reader = nil, nil
	c.setState(0)
}

func (c *client) exchange(command string) (map[string]any, error) {
	if c.conn == nil {
		return nil, fmt.Errorf("not connected")
	}
	_ = c.conn.SetDeadline(time.Now().Add(ioTimeout))
	if _, err := fmt.Fprintf(c.conn, "%s\n", command); err != nil {
		c.close()
		return nil, err
	}
	line, err := c.reader.ReadBytes('\n')
	if err != nil {
		c.close()
		return nil, err
	}
	var body map[string]any
	if err = json.Unmarshal(line, &body); err != nil {
		c.close()
		return nil, err
	}
	if message, found := body["error"]; found {
		c.setState(1)
		return nil, fmt.Errorf("gateway: %v", message)
	}
	c.setState(2)
	return body, nil
}

func (c *client) connect() error {
	c.close()
	conn, err := net.DialTimeout("tcp", c.host, ioTimeout)
	if err != nil {
		return err
	}
	c.conn, c.reader = conn, bufio.NewReader(conn)
	c.setState(1)
	command := "@location any"
	if c.hasPosition {
		command = fmt.Sprintf("@position %.8f %.8f", c.latitude, c.longitude)
	}
	_, err = c.exchange(command)
	return err
}

// request mirrors GatewayClient: retry an application command once after an
// I/O disconnect, but leave routing errors for the normal reconnect interval.
func (c *client) request(command string) (map[string]any, error) {
	body, err := c.exchange(command)
	if err == nil || c.state != 0 {
		return body, err
	}
	if err = c.connect(); err != nil {
		return nil, err
	}
	return c.exchange(command)
}

func (c *client) run(ctx context.Context, rng *mathrand.Rand) {
	angle := rng.Float64() * 2 * math.Pi
	x, y := math.Cos(angle), math.Sin(angle)
	zoom := 2 + rng.Intn(17)
	now := time.Now()
	nextDirection := now.Add(time.Duration(rng.Float64() * float64(3*time.Second)))
	nextReconnect := now
	sequence := 0
	timer := time.NewTimer(time.Duration(rng.Float64() * float64(inputInterval)))
	select {
	case <-ctx.Done():
		timer.Stop()
		return
	case <-timer.C:
	}
	defer c.close()
	for {
		started := time.Now()
		if c.state != 2 {
			if !started.Before(nextReconnect) {
				_ = c.connect()
				nextReconnect = time.Now().Add(100 * time.Millisecond)
			}
		} else {
			if !started.Before(nextDirection) {
				angle = rng.Float64() * 2 * math.Pi
				x, y = math.Cos(angle), math.Sin(angle)
				nextDirection = started.Add(3 * time.Second)
			}
			if c.state == 2 {
				sequence++
				body, err := c.request(fmt.Sprintf("@input %s %d %.3f %.3f %.2f", c.uid, sequence, x, y, float64(zoom)))
				if err == nil {
					lat, latOK := body["client_latitude"].(float64)
					lon, lonOK := body["client_longitude"].(float64)
					if latOK && lonOK {
						c.latitude, c.longitude, c.hasPosition = lat, lon, true
					}
				}
			}
		}
		wait := inputInterval - time.Since(started)
		if wait < 0 {
			wait = 0
		}
		timer.Reset(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

func randomID() string {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(value)
}

func seeded(uid string) *mathrand.Rand {
	hash := fnv.New64a()
	_, _ = hash.Write([]byte(uid))
	return mathrand.New(mathrand.NewSource(int64(hash.Sum64())))
}

func main() {
	host := flag.String("host", "host.docker.internal", "gateway host")
	port := flag.Int("port", 9000, "gateway port")
	count := flag.Int("clients", 1, "number of clients")
	flag.Parse()
	if *count < 1 || *count > 500 {
		fmt.Fprintln(os.Stderr, "clients must be between 1 and 500")
		os.Exit(2)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	var counts counters
	counts.disconnected.Store(int64(*count))
	var clients []*client
	var wait sync.WaitGroup
	for index := 0; index < *count; index++ {
		uid := "bot:" + randomID()
		item := &client{host: net.JoinHostPort(*host, fmt.Sprint(*port)), uid: uid, counts: &counts}
		clients = append(clients, item)
		wait.Add(1)
		go func() { defer wait.Done(); item.run(ctx, seeded(uid)) }()
	}
	go func() {
		<-ctx.Done()
		for _, item := range clients {
			item.close()
		}
	}()
	encoder := json.NewEncoder(os.Stdout)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			wait.Wait()
			return
		case <-ticker.C:
			_ = encoder.Encode(map[string]any{"total": *count, "states": map[string]int64{"ready": counts.ready.Load(), "gateway": counts.gateway.Load(), "disconnected": counts.disconnected.Load()}})
		}
	}
}
