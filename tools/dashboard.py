#!/usr/bin/env python3
"""Local, read-only dashboard aggregating every HAProxy pod in tcp-lab."""

import argparse
import csv
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


NAMESPACE = "tcp-lab"
LABEL = "app=haproxy"
KEY_VALUE = re.compile(r"(?:^|\s)([a-zA-Z_]+)=([^\s]+)")
LOCATIONS = ("los-angeles", "new-york", "london", "singapore", "frankfurt")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>TCP lab connections</title>
  <style>
    :root { color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    body { max-width: 1200px; margin: 0 auto; padding: 24px; background: #0d1117; color: #e6edf3; }
    h1 { margin-bottom: 4px; }
    .muted { color: #8b949e; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap: 12px; margin: 24px 0; }
    .card, .panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
    .value { font-size: 2rem; margin-top: 6px; color: #58a6ff; }
    .panel { margin-top: 16px; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid #30363d; white-space: nowrap; }
    th { color: #8b949e; }
    .ok { color: #3fb950; } .bad { color: #f85149; }
    #error { color: #f85149; min-height: 1.2em; }
  </style>
</head>
<body>
  <h1>TCP lab connections</h1>
  <div class="muted">All HAProxy replicas · refreshes every 2 seconds · read-only</div>
  <div id="error"></div>
  <div class="cards">
    <div class="card">Proxy replicas<div class="value" id="proxies">–</div></div>
    <div class="card">Live clients<div class="value" id="clients">–</div></div>
    <div class="card">Backend sessions<div class="value" id="backends">–</div></div>
    <div class="card">Server pool<div class="value" id="pool">–</div></div>
    <div class="card">Hot spares<div class="value" id="spares">–</div></div>
  </div>
  <div class="panel"><h2>Per proxy</h2><table><thead><tr><th>Proxy</th><th>Pod IP</th><th>Node</th><th>Clients</th><th>Backend</th><th>Total accepted</th><th>Status</th></tr></thead><tbody id="proxyRows"></tbody></table></div>
  <div class="panel"><h2>Active client sessions</h2><table><thead><tr><th>Proxy</th><th>Client address</th><th>Location</th><th>Server</th><th>Instance</th><th>Age</th><th>Protocol</th></tr></thead><tbody id="sessionRows"></tbody></table></div>
  <div class="panel"><h2>Backend distribution</h2><table><thead><tr><th>Server</th><th>Location</th><th>Active instance</th><th>Endpoint</th><th>Clients</th><th>Seen by proxies</th><th>Status</th></tr></thead><tbody id="backendRows"></tbody></table></div>
  <p class="muted" id="updated"></p>
<script>
const cell = (row, value, cls='') => { const td=document.createElement('td'); td.textContent=value ?? '–'; if(cls)td.className=cls; row.appendChild(td); };
const fill = (id, rows, fields) => { const body=document.getElementById(id); body.replaceChildren(); for(const item of rows){ const tr=document.createElement('tr'); for(const f of fields) cell(tr, typeof f==='function'?f(item):item[f]); body.appendChild(tr); } if(!rows.length){ const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=fields.length; td.className='muted'; td.textContent='No active connections'; tr.appendChild(td); body.appendChild(tr); } };
async function refresh(){
  try {
    const response=await fetch('/api/connections', {cache:'no-store'}); const data=await response.json();
    if(!response.ok) throw new Error(data.error || response.statusText);
    document.getElementById('error').textContent=data.errors.join(' · ');
    document.getElementById('proxies').textContent=data.proxies.length;
    document.getElementById('clients').textContent=data.total_clients;
    document.getElementById('backends').textContent=data.total_backends;
    document.getElementById('pool').textContent=data.server_pods;
    document.getElementById('spares').textContent=data.hot_spares;
    fill('proxyRows', data.proxies, ['name','ip','node','clients','backend_sessions','total_accepted',p=>p.error?'ERROR':'OK']);
    fill('sessionRows', data.sessions, ['proxy','src','location','server','instance','age','proto']);
    fill('backendRows', data.backends, ['server','location','instance','address','current','proxies','status']);
    document.getElementById('updated').textContent='Updated '+new Date().toLocaleTimeString();
  } catch(error) { document.getElementById('error').textContent=error.message; }
}
refresh(); setInterval(refresh, 2000);
</script>
</body></html>"""


def run(*args: str) -> str:
    completed = subprocess.run(
        args, capture_output=True, text=True, timeout=8, check=False
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(message or f"command exited {completed.returncode}")
    return completed.stdout


def list_pods() -> list[dict]:
    raw = run(
        "kubectl", "get", "pods", "-n", NAMESPACE, "-l", LABEL,
        "-o", "json",
    )
    result = []
    for item in json.loads(raw).get("items", []):
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in status.get("conditions", [])
        )
        if metadata.get("deletionTimestamp") or status.get("phase") != "Running" or not ready:
            continue
        result.append({
            "name": metadata.get("name", "unknown"),
            "ip": status.get("podIP", ""),
            "node": item.get("spec", {}).get("nodeName", ""),
        })
    return result


def list_server_pods() -> dict[str, str]:
    raw = run(
        "kubectl", "get", "pods", "-n", NAMESPACE, "-l", "app=tcp-server",
        "-o", "json",
    )
    return {
        item.get("status", {}).get("podIP", ""): item.get("metadata", {}).get("name", "")
        for item in json.loads(raw).get("items", [])
        if item.get("status", {}).get("podIP")
    }


def backend_location(backend: str) -> str:
    if backend.startswith("location_"):
        return backend.removeprefix("location_").replace("_", "-")
    return "pending"


def logical_server(location: str) -> str:
    return f"tcp-server-{LOCATIONS.index(location)}" if location in LOCATIONS else "pending"


def session_location(values: dict) -> str:
    return backend_location(values.get("be", ""))


def session_server(values: dict, location: str) -> str:
    return logical_server(location)


def inspect_pod(pod: dict) -> dict:
    pod = dict(pod)
    pod.update(clients=0, backend_sessions=0, total_accepted=0, backends=[], sessions=[], error="")
    targets = {}
    identities = {}
    try:
        stats = run(
            "kubectl", "exec", "-n", NAMESPACE, pod["name"], "--",
            "wget", "-qO-", "http://127.0.0.1:8404/stats;csv",
        )
        for row in csv.reader(stats.splitlines()):
            if len(row) < 18:
                continue
            proxy, server = row[0].lstrip("# "), row[1]
            address = row[73] if len(row) > 73 else ""
            if (proxy == "tcp_servers" or proxy.startswith("location_")) and server.startswith("server"):
                targets[f"{proxy}/{server}"] = address
                location = backend_location(proxy)
                if location in LOCATIONS and row[17].startswith("UP") and address:
                    identities[address] = {
                        "server": logical_server(location),
                        "location": location,
                    }
            if proxy.startswith("client_") and server == "FRONTEND":
                pod["clients"] += int(row[4] or 0)
                pod["total_accepted"] += int(row[7] or 0)
            elif (proxy == "tcp_servers" or proxy.startswith("location_")) and server == "BACKEND":
                pod["backend_sessions"] += int(row[4] or 0)
            elif backend_location(proxy) in LOCATIONS and server.startswith("server"):
                location = backend_location(proxy)
                pod["backends"].append({
                    "proxy": pod["name"], "server": logical_server(location),
                    "location": location,
                    "current": int(row[4] or 0), "total": int(row[7] or 0),
                    "status": row[17], "address": address,
                })

        sessions = run(
            "kubectl", "exec", "-n", NAMESPACE, pod["name"], "--", "sh", "-c",
            "printf 'show sess\\n' | nc 127.0.0.1 9999",
        )
        for line in sessions.splitlines():
            values = dict(KEY_VALUE.findall(line))
            if not values.get("fe", "").startswith("client_"):
                continue
            values["proxy"] = pod["name"]
            address = targets.get(f'{values.get("be", "")}/{values.get("srv", "")}', "")
            values["address"] = address
            identity = identities.get(address)
            if identity:
                values.update(identity)
            else:
                values["location"] = session_location(values)
                values["server"] = session_server(values, values["location"])
            pod["sessions"].append(values)
    except Exception as error:
        pod["error"] = str(error)
    return pod


def snapshot() -> dict:
    pods = list_pods()
    server_pods = list_server_pods()
    with ThreadPoolExecutor(max_workers=max(1, len(pods))) as pool:
        proxies = list(pool.map(inspect_pod, pods))
    unique_backends = {
        logical_server(location): {
            "server": logical_server(location), "location": location,
            "address": "", "instance": "", "current": 0,
            "proxies": set(),
        }
        for location in LOCATIONS
    }
    for backend in (item for proxy in proxies for item in proxy["backends"]):
        current = unique_backends[backend["server"]]
        if backend["status"].startswith("UP"):
            current["proxies"].add(backend["proxy"])
            current["address"] = backend["address"]
            current["instance"] = server_pods.get(backend["address"].split(":")[0], "")
    for session in (item for proxy in proxies for item in proxy["sessions"]):
        session["instance"] = server_pods.get(session.get("address", "").split(":")[0], "")
        if session["server"] in unique_backends:
            unique_backends[session["server"]]["current"] += 1
    backends = sorted(unique_backends.values(), key=lambda item: item["server"])
    for backend in backends:
        backend["proxies"] = ", ".join(sorted(backend["proxies"]))
        up = len(backend["proxies"].split(", ")) if backend["proxies"] else 0
        backend["status"] = "UP" if proxies and up == len(proxies) else ("DEGRADED" if up else "DOWN")

    return {
        "proxies": proxies,
        "total_clients": sum(p["clients"] for p in proxies),
        "total_backends": sum(p["backend_sessions"] for p in proxies),
        "server_pods": len(server_pods),
        "hot_spares": max(0, len(server_pods) - sum(b["status"] != "DOWN" for b in backends)),
        "sessions": [session for p in proxies for session in p["sessions"]],
        "backends": backends,
        "errors": [f'{p["name"]}: {p["error"]}' for p in proxies if p["error"]],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self.send(200, "text/html; charset=utf-8", PAGE.encode())
            return
        if self.path == "/api/connections":
            try:
                body = json.dumps(snapshot()).encode()
                self.send(200, "application/json", body)
            except Exception as error:
                body = json.dumps({"error": str(error)}).encode()
                self.send(500, "application/json", body)
            return
        self.send(404, "text/plain", b"not found\n")

    def send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate live HAProxy connections")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Dashboard: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
