#!/usr/bin/env python3
"""Local, read-only dashboard aggregating every gateway pod in tcp-lab."""

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


NAMESPACE = "tcp-lab"
LABEL = "app=gateway"
DATA_SERVICES = {
    "postgres": ("PostgreSQL", "postgres:5432"),
    "redis": ("Redis", "redis:6379"),
}


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
  <div class="muted">All gateway replicas · refreshes every 2 seconds · read-only</div>
  <div id="error"></div>
  <div class="cards">
    <div class="card">Gateway replicas<div class="value" id="gateways">–</div></div>
    <div class="card">Live clients<div class="value" id="clients">–</div></div>
    <div class="card">Backend sessions<div class="value" id="backends">–</div></div>
    <div class="card">Server pool<div class="value" id="pool">–</div></div>
    <div class="card">Hot spares<div class="value" id="spares">–</div></div>
  </div>
  <div class="panel"><h2>Per gateway</h2><table><thead><tr><th>Gateway</th><th>Pod IP</th><th>Node</th><th>Clients</th><th>Backend</th><th>Total accepted</th><th>Status</th></tr></thead><tbody id="gatewayRows"></tbody></table></div>
  <div class="panel"><h2>Active client sessions</h2><table><thead><tr><th>Gateway</th><th>Client address</th><th>Location ID</th><th>Logical server</th><th>Instance</th><th>Generation</th><th>Age</th><th>Protocol</th></tr></thead><tbody id="sessionRows"></tbody></table></div>
  <div class="panel"><h2>Backend distribution</h2><table><thead><tr><th>Logical server</th><th>Location ID</th><th>Active instance</th><th>Node</th><th>Endpoint</th><th>Generation</th><th>Clients</th><th>Seen by gateways</th><th>Status</th></tr></thead><tbody id="backendRows"></tbody></table></div>
  <div class="panel"><h2>Data services</h2><table><thead><tr><th>Service</th><th>Instance</th><th>Node</th><th>Pod IP</th><th>Service endpoint</th><th>Restarts</th><th>Status</th></tr></thead><tbody id="dataRows"></tbody></table></div>
  <p class="muted" id="updated"></p>
<script>
const cell = (row, value, cls='') => { const td=document.createElement('td'); td.textContent=value ?? '–'; if(cls)td.className=cls; row.appendChild(td); };
const fill = (id, rows, fields, empty='No active connections') => { const body=document.getElementById(id); body.replaceChildren(); for(const item of rows){ const tr=document.createElement('tr'); for(const f of fields) cell(tr, typeof f==='function'?f(item):item[f]); body.appendChild(tr); } if(!rows.length){ const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=fields.length; td.className='muted'; td.textContent=empty; tr.appendChild(td); body.appendChild(tr); } };
async function refresh(){
  try {
    const response=await fetch('/api/connections', {cache:'no-store'}); const data=await response.json();
    if(!response.ok) throw new Error(data.error || response.statusText);
    document.getElementById('error').textContent=data.errors.join(' · ');
    document.getElementById('gateways').textContent=data.gateways.length;
    document.getElementById('clients').textContent=data.total_clients;
    document.getElementById('backends').textContent=data.total_backends;
    document.getElementById('pool').textContent=data.server_pods;
    document.getElementById('spares').textContent=data.hot_spares;
    fill('gatewayRows', data.gateways, ['name','ip','node','clients','backend_sessions','total_accepted',p=>p.error?'ERROR':'OK']);
    fill('sessionRows', data.sessions, ['gateway','src','location_id','server','instance','generation','age','proto']);
    fill('backendRows', data.backends, ['server','location_id','instance','node','address','generation','current','gateways','status']);
    fill('dataRows', data.data_services, ['service','instance','node','ip','endpoint','restarts','status'], 'No in-cluster data services');
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


def list_server_pods() -> dict[str, dict[str, str]]:
    raw = run(
        "kubectl", "get", "pods", "-n", NAMESPACE, "-l", "app=tcp-server",
        "-o", "json",
    )
    return {
        item.get("status", {}).get("podIP", ""): {
            "name": item.get("metadata", {}).get("name", ""),
            "ip": item.get("status", {}).get("podIP", ""),
            "node": item.get("spec", {}).get("nodeName", ""),
        }
        for item in json.loads(raw).get("items", [])
        if item.get("status", {}).get("podIP")
    }


def list_data_services() -> list[dict]:
    raw = run(
        "kubectl", "get", "pods", "-n", NAMESPACE,
        "-l", "app in (postgres,redis)", "-o", "json",
    )
    result = []
    for item in json.loads(raw).get("items", []):
        metadata = item.get("metadata", {})
        spec = item.get("spec", {})
        status = item.get("status", {})
        labels = metadata.get("labels", {})
        app = labels.get("app", "")
        if app not in DATA_SERVICES:
            continue
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in status.get("conditions", [])
        )
        phase = status.get("phase", "Unknown")
        if metadata.get("deletionTimestamp"):
            health = "TERMINATING"
        elif phase == "Running" and ready:
            health = "UP"
        elif phase == "Running":
            health = "NOT READY"
        else:
            health = phase.upper()
        service, endpoint = DATA_SERVICES[app]
        result.append({
            "service": service,
            "instance": metadata.get("name", ""),
            "node": spec.get("nodeName", ""),
            "ip": status.get("podIP", ""),
            "endpoint": endpoint,
            "restarts": sum(
                container.get("restartCount", 0)
                for container in status.get("containerStatuses", [])
            ),
            "status": health,
        })
    return sorted(result, key=lambda item: (item["service"], item["instance"]))


def inspect_pod(pod: dict) -> dict:
    pod = dict(pod)
    pod.update(clients=0, backend_sessions=0, total_accepted=0, backends=[], sessions=[], error="")
    try:
        raw = run(
            "kubectl", "get", "--raw",
            f'/api/v1/namespaces/{NAMESPACE}/pods/{pod["name"]}:8404/proxy/stats',
        )
        stats = json.loads(raw)
        pod["total_accepted"] = int(stats.get("accepted", 0))
        now = datetime.now(timezone.utc)
        for item in stats.get("sessions", []):
            started = datetime.fromisoformat(item["connected_at"].replace("Z", "+00:00"))
            age = max(0, int((now - started).total_seconds()))
            pod["sessions"].append({
                "gateway": pod["name"], "src": item.get("client", ""),
                "location_id": item.get("location_id"), "server": item.get("server", ""),
                "instance": item.get("instance", ""), "address": item.get("address", ""),
                "generation": item.get("generation", 0), "age": f"{age}s",
                "proto": item.get("protocol", "TCP"),
            })
        pod["clients"] = len(pod["sessions"])
        pod["backend_sessions"] = len(pod["sessions"])
        for item in stats.get("routes", []):
            pod["backends"].append({
                "gateway": pod["name"], "server": item.get("server", ""),
                "location_id": item.get("location_id"), "instance": item.get("instance", ""),
                "address": item.get("address", ""), "generation": item.get("generation", 0),
                "status": "UP",
            })
    except Exception as error:
        pod["error"] = str(error)
    return pod


def snapshot() -> dict:
    pods = list_pods()
    server_pods = list_server_pods()
    data_services = list_data_services()
    with ThreadPoolExecutor(max_workers=max(1, len(pods))) as pool:
        gateways = list(pool.map(inspect_pod, pods))
    unique_backends = {}
    for backend in (item for gateway in gateways for item in gateway["backends"]):
        current = unique_backends.setdefault(backend["server"], {
            "server": backend["server"], "location_id": backend["location_id"],
            "address": "", "instance": "", "node": "", "generation": 0, "current": 0,
            "gateways": set(),
        })
        if backend["status"] == "UP" and backend["generation"] >= current["generation"]:
            if backend["generation"] > current["generation"]:
                current["gateways"].clear()
            current["generation"] = backend["generation"]
            current["gateways"].add(backend["gateway"])
            current["address"] = backend["address"]
            server_pod = server_pods.get(backend["address"].split(":")[0], {})
            current["instance"] = backend["instance"] or server_pod.get("name", "")
            current["node"] = server_pod.get("node", "")
    for session in (item for gateway in gateways for item in gateway["sessions"]):
        if session["server"] in unique_backends:
            unique_backends[session["server"]]["current"] += 1
    backends = sorted(unique_backends.values(), key=lambda item: item["server"])
    for backend in backends:
        backend["gateways"] = ", ".join(sorted(backend["gateways"]))
        up = len(backend["gateways"].split(", ")) if backend["gateways"] else 0
        backend["status"] = "UP" if gateways and up == len(gateways) else ("DEGRADED" if up else "DOWN")

    active_instances = {backend["instance"] for backend in backends if backend["instance"]}
    server_instances = sorted(
        (
            {**pod, "role": "active" if pod["name"] in active_instances else "spare"}
            for pod in server_pods.values()
        ),
        key=lambda pod: pod["name"],
    )

    return {
        "gateways": gateways,
        "total_clients": sum(p["clients"] for p in gateways),
        "total_backends": sum(p["backend_sessions"] for p in gateways),
        "server_pods": len(server_pods),
        "hot_spares": sum(instance["role"] == "spare" for instance in server_instances),
        "server_instances": server_instances,
        "data_services": data_services,
        "sessions": [session for p in gateways for session in p["sessions"]],
        "backends": backends,
        "errors": [f'{p["name"]}: {p["error"]}' for p in gateways if p["error"]],
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate live gateway connections")
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
