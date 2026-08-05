#!/usr/bin/env python3
"""Local TCP dashboard, map visualizer, and scoped lab controls."""

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    from bots import BotManager
except ModuleNotFoundError:
    from tools.bots import BotManager


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
    .tabs { display:flex; gap:8px; margin:18px 0; }.tabs a { color:#e6edf3; text-decoration:none; padding:8px 12px; border:1px solid #30363d; border-radius:6px; }.tabs .active { background:#1f6feb; border-color:#1f6feb; }
  </style>
</head>
<body>
  <h1>TCP lab connections</h1>
  <nav class="tabs"><a class="active" href="/">TCP dashboard</a><a href="/map">Interactive map</a></nav>
  <div class="muted">All gateway replicas · refreshes every 2 seconds · read-only</div>
  <div id="error"></div>
  <div class="cards">
    <div class="card">Not ready pods<div class="value bad" id="notReady">-</div></div>
    <div class="card">Unknown pods<div class="value muted" id="unknown">-</div></div>
    <div class="card">Gateway replicas<div class="value" id="gateways">–</div></div>
    <div class="card">Total clients<div class="value" id="clients">–</div></div>
    <div class="card">Connected clients<div class="value" id="connectedClients">–</div></div>
    <div class="card">Dummy threads<div class="value" id="dummyThreads">–</div></div>
    <div class="card">Backend sessions<div class="value" id="backends">–</div></div>
    <div class="card">Server pool<div class="value" id="pool">–</div></div>
    <div class="card">Hot spares<div class="value" id="spares">–</div></div>
  </div>
  <div class="panel"><h2>Per gateway</h2><table><thead><tr><th>Gateway</th><th>Pod IP</th><th>Node</th><th>Clients</th><th>Backend</th><th>Total accepted</th><th>Status</th></tr></thead><tbody id="gatewayRows"></tbody></table></div>
  <div class="panel"><h2>Client sessions</h2><table><thead><tr><th>Gateway</th><th>Client address</th><th>Status</th><th>Location ID</th><th>Logical server</th><th>Instance</th><th>Generation</th><th>Age</th><th>Protocol</th></tr></thead><tbody id="sessionRows"></tbody></table></div>
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
    document.getElementById('connectedClients').textContent=data.connected_clients;
    document.getElementById('dummyThreads').textContent=data.dummy_threads;
    document.getElementById('backends').textContent=data.total_backends;
    document.getElementById('pool').textContent=data.server_pods;
    document.getElementById('spares').textContent=data.hot_spares;
    document.getElementById('notReady').textContent=data.not_ready_pods;
    document.getElementById('unknown').textContent=data.unknown_pods;
    fill('gatewayRows', data.gateways, ['name','ip','node','clients','backend_sessions','total_accepted',p=>p.error?'ERROR':'OK']);
    fill('sessionRows', data.sessions, ['gateway','src','status','location_id','server','instance','generation','age','proto']);
    fill('backendRows', data.backends, ['server','location_id','instance','node','address','generation','current','gateways','status']);
    fill('dataRows', data.data_services, ['service','instance','node','ip','endpoint','restarts','status'], 'No in-cluster data services');
    document.getElementById('updated').textContent='Updated '+new Date().toLocaleTimeString();
  } catch(error) { document.getElementById('error').textContent=error.message; }
}
refresh(); setInterval(refresh, 2000);
</script>
</body></html>"""

MAP_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TCP lab map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{color-scheme:dark;font:14px system-ui,sans-serif;background:#0d1117;color:#e6edf3}*{box-sizing:border-box}body{margin:0;height:100vh;background:#0d1117;color:#e6edf3;display:grid;grid-template-rows:auto 1fr}header{display:flex;align-items:center;gap:18px;padding:12px 16px;background:#161b22;border-bottom:1px solid #30363d;z-index:1000}.tabs{display:flex;gap:8px}.tabs a{color:#e6edf3;text-decoration:none;padding:7px 10px;border:1px solid #30363d;border-radius:6px}.tabs .active{background:#1f6feb;border-color:#1f6feb}.summary{margin-left:auto;color:#8b949e}main{display:grid;grid-template-columns:minmax(0,1fr) 300px;min-height:0}.map-wrap{position:relative;min-width:0}#map{height:100%;background:#0d1117}.node-row{position:absolute;z-index:500;left:54px;right:12px;display:flex;justify-content:center;gap:7px;flex-wrap:wrap;pointer-events:none}.node-row.top{top:12px}.node-row.bottom{bottom:24px}.node{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:6px 9px;border:1px solid #8b949e;border-radius:6px;background:#161b22e8;color:#e6edf3;font:11px ui-monospace,monospace;box-shadow:0 2px 8px #0008}.gateway{border-color:#a371f7}.spare{border-color:#d29922;color:#d29922}.node.not_ready{border-color:#f85149;color:#f85149}.node.unknown{border-color:#8b949e;color:#8b949e}aside{padding:16px;background:#161b22;border-left:1px solid #30363d;overflow:auto}.card{padding:13px;margin-bottom:12px;border:1px solid #30363d;border-radius:7px;background:#0d1117}.label{color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}.value{font-family:ui-monospace,monospace;overflow-wrap:anywhere}.route{font-size:18px;color:#58a6ff}input{width:100%;margin-bottom:8px;border:1px solid #30363d;border-radius:6px;background:#161b22;color:#e6edf3;padding:9px}.toggle{display:flex;align-items:center;gap:8px;color:#8b949e;cursor:pointer}.toggle+.toggle{margin-top:9px}.toggle input{width:auto;margin:0}button{width:100%;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#e6edf3;padding:9px;cursor:pointer;margin-top:8px}button:hover{border-color:#58a6ff}button:disabled{cursor:not-allowed;opacity:.5}button.danger{border-color:#f85149;color:#ff7b72}.muted{font-size:11px;color:#8b949e;line-height:1.45}.error{color:#f85149;min-height:20px;margin-top:10px}.batch-row{display:grid;grid-template-columns:1fr auto;align-items:center;gap:8px;margin-top:8px;font:12px ui-monospace,monospace}.batch-row button{width:auto;padding:5px 8px;margin:0}.bot-summary{margin:8px 0;color:#8b949e}.client-pin{width:18px;height:18px;border-radius:50% 50% 50% 0;background:#39c5cf;border:2px solid #d7ffff;transform:rotate(-45deg);box-shadow:0 2px 5px #0009}.client-pin-wrap{background:transparent;border:0}.server-label{background:#161b22;color:#e6edf3;border:1px solid #58a6ff;border-radius:4px;box-shadow:none;padding:2px 5px}.leaflet-control-attribution{background:#161b22cc!important;color:#8b949e}.leaflet-control-attribution a{color:#58a6ff}@media(max-width:720px){header{flex-wrap:wrap}.summary{width:100%;margin-left:0}main{grid-template-columns:1fr;grid-template-rows:minmax(360px,1fr) auto}aside{border-left:0;border-top:1px solid #30363d}}
</style></head><body>
<header><strong>TCP lab</strong><nav class="tabs"><a href="/">TCP dashboard</a><a class="active" href="/map">Interactive map</a></nav><div id="summary" class="summary">Loading...</div></header>
<main><div class="map-wrap"><div id="map"></div><div id="spares" class="node-row top"></div><div id="gateways" class="node-row bottom"></div></div>
<aside>
<div class="card"><div class="label">Selected coordinate</div><div id="selection" class="value">Click the map</div></div>
<div class="card"><div class="label">Map layers</div><label class="toggle"><input id="showClients" type="checkbox" checked>Show clients</label><label class="toggle"><input id="showServers" type="checkbox" checked>Show active locations</label><label class="toggle"><input id="showGateways" type="checkbox" checked>Show gateways</label><label class="toggle"><input id="showSpares" type="checkbox" checked>Show hot swaps</label></div>
<div class="card"><div class="label">Map update rate</div><div id="refreshRateValue" class="value route">4 Hz</div><input id="refreshRate" type="range" min="1" max="20" step="1" value="4"><div class="muted">Target rate; slow Kubernetes snapshots never overlap.</div></div>
<div class="card"><div class="label">Dummy clients</div><input id="botCount" type="number" min="1" max="500" value="10"><button id="spawn">Spawn batch</button><div id="botState" class="bot-summary">0 active</div><div id="botBatches"></div><button id="despawn">Despawn all</button></div>
<div class="card"><div class="label">Location controls</div><button id="createSelected" disabled>Create at selected coordinate</button><button id="createRandom">Create random location</button><button id="deleteLocation" class="danger" disabled>Disable selected location</button><div class="muted" style="margin-top:9px">Click a server marker to select its location. Changes are stored by PostgreSQL and discovered by the running pool.</div></div>
<div id="error" class="error"></div>
</aside></main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const map=L.map('map',{worldCopyJump:true,minZoom:2}).setView([20,0],2);L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(map);
const serverLayer=L.layerGroup().addTo(map),clientLayer=L.layerGroup().addTo(map),serverMarkers=new Map(),clientMarkers=new Map();let selectedPoint=null,selectedLocation=null,lastData=null,refreshHz=4;
const request=async(path,options={})=>{const response=await fetch(path,{cache:'no-store',...options});const body=await response.json();if(!response.ok)throw new Error(body.error||response.statusText);return body};
const post=(path,body={})=>request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function render(data){lastData=data;document.getElementById('summary').textContent=`${data.gateways.length} gateways · ${data.connected_clients}/${data.total_clients} clients connected · ${data.dummy_threads} dummy threads · ${data.backends.length} locations · ${data.hot_spares} spares · ${data.not_ready_pods} not ready · ${data.unknown_pods} unknown`;const visibleServers=new Set(),visibleClients=new Set();
 if(document.getElementById('showServers').checked)for(const server of data.backends){if(!server.latitude&&server.latitude!==0)continue;visibleServers.add(server.server);let marker=serverMarkers.get(server.server);if(!marker){marker=L.circleMarker([server.latitude,server.longitude],{radius:10,color:'#3fb950',weight:3,fillColor:'#0d1117',fillOpacity:1}).addTo(serverLayer);marker.bindTooltip('',{permanent:true,direction:'top',className:'server-label'});marker.bindPopup('');marker.on('click',()=>selectLocation(marker.serverData));serverMarkers.set(server.server,marker)}marker.serverData=server;marker.setLatLng([server.latitude,server.longitude]);marker.getTooltip().setContent(`Location ${server.location_id}`);marker.getPopup().setContent(`<b>Location ${server.location_id}</b><br>${server.server}<br>${server.instance}<br>${server.node}<br>${server.current} clients`)}for(const [key,marker] of serverMarkers)if(!visibleServers.has(key)){marker.remove();serverMarkers.delete(key)}
 if(document.getElementById('showClients').checked)for(const client of data.sessions){if(client.latitude==null||client.longitude==null)continue;const key=`${client.gateway}:${client.id||client.src}`;visibleClients.add(key);let marker=clientMarkers.get(key);if(!marker){const icon=L.divIcon({className:'client-pin-wrap',html:'<div class="client-pin"></div>',iconSize:[18,25],iconAnchor:[9,25]});marker=L.marker([client.latitude,client.longitude],{icon}).addTo(clientLayer);marker.bindTooltip('');marker.bindPopup('');clientMarkers.set(key,marker)}marker.setLatLng([client.latitude,client.longitude]);marker.getTooltip().setContent(client.client_uid||client.src);marker.getPopup().setContent(`<b>${client.client_uid||'Client'}</b><br>Status: ${client.status}<br>Gateway: ${client.gateway}<br>Location: ${client.location_id}<br>Server: ${client.server}`)}for(const [key,marker] of clientMarkers)if(!visibleClients.has(key)){marker.remove();clientMarkers.delete(key)}
 const gateways=document.getElementById('gateways');gateways.replaceChildren();if(document.getElementById('showGateways').checked)for(const item of data.gateway_instances){const node=document.createElement('div');node.className=`node gateway ${item.status}`;node.textContent=item.name;node.title=`${item.status.replace('_',' ')} · ${item.node}`;gateways.appendChild(node)}
 const spares=document.getElementById('spares');spares.replaceChildren();if(document.getElementById('showSpares').checked)for(const item of data.server_instances.filter(x=>x.role==='spare'||x.status!=='ready')){const node=document.createElement('div');node.className=`node spare ${item.status}`;node.textContent=item.name;node.title=`${item.role} · ${item.status.replace('_',' ')} · ${item.node}`;spares.appendChild(node)}}
function selectLocation(server){selectedLocation=server;selectedPoint=[server.latitude,server.longitude];document.getElementById('selection').textContent=`Location ${server.location_id} · ${server.latitude.toFixed(5)}, ${server.longitude.toFixed(5)}`;document.getElementById('createSelected').disabled=false;document.getElementById('deleteLocation').disabled=false}
map.on('click',event=>{const point=event.latlng.wrap();selectedPoint=[point.lat,point.lng];selectedLocation=null;document.getElementById('selection').textContent=`${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;document.getElementById('createSelected').disabled=false;document.getElementById('deleteLocation').disabled=true});
async function refresh(){try{render(await request('/api/connections'));document.getElementById('error').textContent=''}catch(error){document.getElementById('error').textContent=error.message}}
async function refreshLoop(){const started=performance.now();await refresh();setTimeout(refreshLoop,Math.max(0,1000/refreshHz-(performance.now()-started)))}
function renderBots(bots){document.getElementById('botState').textContent=`${bots.states.ready} ready · ${bots.states.gateway} gateway · ${bots.states.disconnected} disconnected`;const list=document.getElementById('botBatches');list.replaceChildren();for(const batch of bots.batches){const row=document.createElement('div');row.className='batch-row';const label=document.createElement('span');label.textContent=`Batch ${batch.id}: ${batch.count}`;const remove=document.createElement('button');remove.textContent='Despawn';remove.onclick=async()=>{try{renderBots(await post('/api/bots/despawn',{batch_id:batch.id}))}catch(error){document.getElementById('error').textContent=error.message}};row.append(label,remove);list.appendChild(row)}}
async function refreshBots(){try{renderBots(await request('/api/bots'))}catch(error){document.getElementById('error').textContent=error.message}}
document.getElementById('spawn').onclick=async()=>{try{await post('/api/bots/spawn',{count:Number(document.getElementById('botCount').value)});refreshBots()}catch(error){document.getElementById('error').textContent=error.message}};document.getElementById('despawn').onclick=async()=>{try{await post('/api/bots/despawn-all');refreshBots()}catch(error){document.getElementById('error').textContent=error.message}};
document.getElementById('createSelected').onclick=async()=>{if(!selectedPoint)return;try{await post('/api/locations/create',{latitude:selectedPoint[0],longitude:selectedPoint[1]});selectedLocation=null;await refresh()}catch(error){document.getElementById('error').textContent=error.message}};document.getElementById('createRandom').onclick=async()=>{try{await post('/api/locations/create');await refresh()}catch(error){document.getElementById('error').textContent=error.message}};document.getElementById('deleteLocation').onclick=async()=>{if(!selectedLocation||!confirm(`Disable location ${selectedLocation.location_id}?`))return;try{await post('/api/locations/delete',{location_id:selectedLocation.location_id});selectedLocation=null;document.getElementById('deleteLocation').disabled=true;await refresh()}catch(error){document.getElementById('error').textContent=error.message}};
for(const id of ['showClients','showServers','showGateways','showSpares'])document.getElementById(id).onchange=()=>lastData&&render(lastData);const refreshRate=document.getElementById('refreshRate');refreshHz=Math.max(1,Math.min(20,Number(localStorage.getItem('tcp-lab-dashboard-refresh-hz')||4)));refreshRate.value=refreshHz;document.getElementById('refreshRateValue').textContent=`${refreshHz} Hz`;refreshRate.oninput=event=>{refreshHz=Number(event.target.value);localStorage.setItem('tcp-lab-dashboard-refresh-hz',refreshHz);document.getElementById('refreshRateValue').textContent=`${refreshHz} Hz`};refreshLoop();refreshBots();setInterval(refreshBots,2000);
</script></body></html>"""


def run(*args: str) -> str:
    completed = subprocess.run(
        args, capture_output=True, text=True, timeout=8, check=False
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(message or f"command exited {completed.returncode}")
    return completed.stdout


def list_node_statuses() -> dict[str, str]:
    raw = run("kubectl", "get", "nodes", "-o", "json")
    result = {}
    for item in json.loads(raw).get("items", []):
        ready = next(
            (condition.get("status") for condition in item.get("status", {}).get("conditions", []) if condition.get("type") == "Ready"),
            "Unknown",
        )
        result[item.get("metadata", {}).get("name", "")] = (
            "ready" if ready == "True" else "not_ready" if ready == "False" else "unknown"
        )
    return result


def application_pod(item: dict, node_statuses: dict[str, str]) -> dict:
    metadata = item.get("metadata", {})
    status = item.get("status", {})
    node = item.get("spec", {}).get("nodeName", "")
    node_status = node_statuses.get(node, "unknown")
    pod_ready = next(
        (condition.get("status") for condition in status.get("conditions", []) if condition.get("type") == "Ready"),
        "Unknown",
    )
    if node_status == "unknown" or pod_ready == "Unknown" or not node:
        health = "unknown"
    elif metadata.get("deletionTimestamp") or node_status == "not_ready" or status.get("phase") != "Running" or pod_ready != "True":
        health = "not_ready"
    else:
        health = "ready"
    return {
        "name": metadata.get("name", "unknown"),
        "ip": status.get("podIP", ""),
        "node": node,
        "status": health,
    }


def list_pods(node_statuses: dict[str, str]) -> list[dict]:
    raw = run(
        "kubectl", "get", "pods", "-n", NAMESPACE, "-l", LABEL,
        "-o", "json",
    )
    return [application_pod(item, node_statuses) for item in json.loads(raw).get("items", [])]


def list_server_pods(node_statuses: dict[str, str]) -> list[dict[str, str]]:
    raw = run(
        "kubectl", "get", "pods", "-n", NAMESPACE, "-l", "app=tcp-server",
        "-o", "json",
    )
    return [application_pod(item, node_statuses) for item in json.loads(raw).get("items", [])]


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


def postgres_query(sql: str) -> str:
    raw = run(
        "kubectl", "get", "pods", "-n", NAMESPACE, "-l", "app=postgres",
        "-o", "json",
    )
    pods = [
        item.get("metadata", {}).get("name", "")
        for item in json.loads(raw).get("items", [])
        if item.get("status", {}).get("phase") == "Running"
    ]
    if not pods:
        raise RuntimeError("PostgreSQL pod is not running")
    return run(
        "kubectl", "exec", "-n", NAMESPACE, pods[0], "--",
        "psql", "-U", "tcp_lab", "-d", "tcp_lab", "-At", "-c", sql,
    ).strip()


def ensure_server_replicas() -> None:
    enabled = int(postgres_query("SELECT COUNT(*) FROM tcp_server_state WHERE enabled"))
    deployment = json.loads(run(
        "kubectl", "get", "deployment", "tcp-server", "-n", NAMESPACE, "-o", "json",
    ))
    replicas = int(deployment.get("spec", {}).get("replicas", 0))
    if enabled > replicas:
        run(
            "kubectl", "scale", "deployment", "tcp-server", "-n", NAMESPACE,
            f"--replicas={enabled}",
        )


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
                "id": item.get("id", ""), "gateway": pod["name"], "src": item.get("client", ""),
                "client_uid": item.get("client_uid", ""),
                "location_id": item.get("location_id"), "server": item.get("server", ""),
                "latitude": item.get("latitude"), "longitude": item.get("longitude"),
                "instance": item.get("instance", ""), "address": item.get("address", ""),
                "generation": item.get("generation", 0), "age": f"{age}s",
                "proto": item.get("protocol", "TCP"), "status": item.get("status", "gateway"),
            })
        pod["clients"] = len(pod["sessions"])
        pod["backend_sessions"] = sum(item["status"] == "ready" for item in pod["sessions"])
        for item in stats.get("routes", []):
            pod["backends"].append({
                "gateway": pod["name"], "server": item.get("server", ""),
                "location_id": item.get("location_id"), "instance": item.get("instance", ""),
                "latitude": item.get("latitude"), "longitude": item.get("longitude"),
                "address": item.get("address", ""), "generation": item.get("generation", 0),
                "status": "UP",
            })
    except Exception as error:
        pod["error"] = str(error)
    return pod


def snapshot() -> dict:
    node_statuses = list_node_statuses()
    gateway_instances = list_pods(node_statuses)
    pods = [pod for pod in gateway_instances if pod["status"] == "ready"]
    server_pods = list_server_pods(node_statuses)
    server_pods_by_ip = {pod["ip"]: pod for pod in server_pods if pod["ip"]}
    data_services = list_data_services()
    with ThreadPoolExecutor(max_workers=max(1, len(pods))) as pool:
        gateways = list(pool.map(inspect_pod, pods))
    unique_backends = {}
    for backend in (item for gateway in gateways for item in gateway["backends"]):
        current = unique_backends.setdefault(backend["server"], {
            "server": backend["server"], "location_id": backend["location_id"],
            "latitude": backend["latitude"], "longitude": backend["longitude"],
            "address": "", "instance": "", "node": "", "generation": 0, "current": 0,
            "gateways": set(),
        })
        if backend["status"] == "UP" and backend["generation"] >= current["generation"]:
            if backend["generation"] > current["generation"]:
                current["gateways"].clear()
            current["generation"] = backend["generation"]
            current["gateways"].add(backend["gateway"])
            current["address"] = backend["address"]
            server_pod = server_pods_by_ip.get(backend["address"].split(":")[0], {})
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
            for pod in server_pods
        ),
        key=lambda pod: pod["name"],
    )

    application_instances = gateway_instances + server_instances
    bot_state = BOT_MANAGER.snapshot() if BOT_MANAGER is not None else {"total": 0}

    return {
        "gateways": gateways,
        "gateway_instances": gateway_instances,
        "total_clients": sum(p["clients"] for p in gateways),
        "connected_clients": sum(p["backend_sessions"] for p in gateways),
        "dummy_threads": bot_state["total"],
        "total_backends": sum(p["backend_sessions"] for p in gateways),
        "server_pods": sum(instance["status"] == "ready" for instance in server_instances),
        "hot_spares": sum(instance["role"] == "spare" and instance["status"] == "ready" for instance in server_instances),
        "not_ready_pods": sum(instance["status"] == "not_ready" for instance in application_instances),
        "unknown_pods": sum(instance["status"] == "unknown" for instance in application_instances),
        "server_instances": server_instances,
        "data_services": data_services,
        "sessions": [session for p in gateways for session in p["sessions"]],
        "backends": backends,
        "errors": [f'{p["name"]}: {p["error"]}' for p in gateways if p["error"]],
    }


BOT_MANAGER = None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send(200, "text/html; charset=utf-8", PAGE.encode())
            return
        if path == "/map":
            self.send(200, "text/html; charset=utf-8", MAP_PAGE.encode())
            return
        if path == "/api/connections":
            try:
                body = json.dumps(snapshot()).encode()
                self.send(200, "application/json", body)
            except Exception as error:
                body = json.dumps({"error": str(error)}).encode()
                self.send(500, "application/json", body)
            return
        if path == "/api/bots":
            self.send_json(BOT_MANAGER.snapshot())
            return
        self.send(404, "text/plain", b"not found\n")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/bots/spawn":
                self.send_json(BOT_MANAGER.spawn(int(payload["count"])))
            elif path == "/api/bots/despawn":
                self.send_json(BOT_MANAGER.despawn(int(payload["batch_id"])))
            elif path == "/api/bots/despawn-all":
                self.send_json(BOT_MANAGER.despawn_all())
            elif path == "/api/locations/create":
                if "latitude" in payload or "longitude" in payload:
                    latitude = float(payload["latitude"])
                    longitude = float(payload["longitude"])
                    if not (-85.05112878 <= latitude <= 85.05112878 and -180 <= longitude <= 180):
                        raise ValueError("coordinate is outside the Leaflet world bounds")
                    arguments = f"{latitude:.8f}, {longitude:.8f}"
                else:
                    arguments = ""
                result = postgres_query(
                    f"SELECT row_to_json(location) FROM tcp_create_location({arguments}) AS location"
                )
                ensure_server_replicas()
                self.send_json(json.loads(result))
            elif path == "/api/locations/delete":
                location_id = int(payload["location_id"])
                if location_id < 1:
                    raise ValueError("location ID must be positive")
                deleted = postgres_query(f"SELECT tcp_delete_location({location_id})") == "t"
                self.send_json({"location_id": location_id, "deleted": deleted})
            else:
                self.send_json({"error": "not found"}, 404)
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, 400)

    def send_json(self, body, status: int = 200) -> None:
        self.send(status, "application/json", json.dumps(body).encode())

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
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=9000)
    args = parser.parse_args()
    global BOT_MANAGER
    BOT_MANAGER = BotManager(args.gateway_host, args.gateway_port)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Dashboard: http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        BOT_MANAGER.despawn_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
