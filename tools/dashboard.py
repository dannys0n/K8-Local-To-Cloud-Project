#!/usr/bin/env python3
"""Local topology map and scoped lab controls."""

import argparse
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

try:
    from bots import BotManager
except ModuleNotFoundError:
    from tools.bots import BotManager


NAMESPACE = "tcp-lab"
LABEL = "app=gateway"
INFRASTRUCTURE_CACHE = {"data": None, "error": ""}
INFRASTRUCTURE_REFRESH = {"seconds": 2.0}
INFRASTRUCTURE_LOCK = threading.Lock()
INFRASTRUCTURE_WAKE = threading.Event()


MAP_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TCP lab map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{color-scheme:dark;font:14px system-ui,sans-serif;background:#0d1117;color:#e6edf3}*{box-sizing:border-box}body{margin:0;height:100vh;background:#0d1117;color:#e6edf3;display:grid;grid-template-rows:auto 1fr}header{position:relative;display:flex;align-items:center;gap:12px;padding:12px 16px;background:#161b22;border-bottom:1px solid #30363d;z-index:1000}.summary{margin-left:auto;color:#8b949e}.grafana-toggle{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:auto;margin:0;padding:6px 12px}main{display:grid;grid-template-columns:minmax(0,1fr) 300px;min-height:0}.map-wrap{position:relative;min-width:0}#map{height:100%;background:#0d1117}.node-row{position:absolute;z-index:500;left:54px;right:12px;display:flex;justify-content:center;gap:7px;flex-wrap:wrap;pointer-events:none}.node-row.bottom{bottom:24px}.node{max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:6px 9px;border:1px solid #8b949e;border-radius:6px;background:#161b22e8;color:#e6edf3;font:11px ui-monospace,monospace;box-shadow:0 2px 8px #0008}.gateway{border-color:#a371f7}.node.not_ready{border-color:#f85149;color:#f85149}.node.unknown{border-color:#8b949e;color:#8b949e}aside{padding:16px;background:#161b22;border-left:1px solid #30363d;overflow:auto}.card{padding:13px;margin-bottom:12px;border:1px solid #30363d;border-radius:7px;background:#0d1117}.label{color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}.value{font-family:ui-monospace,monospace;overflow-wrap:anywhere}.route{font-size:18px;color:#58a6ff}input{width:100%;margin-bottom:8px;border:1px solid #30363d;border-radius:6px;background:#161b22;color:#e6edf3;padding:9px}.toggle{display:flex;align-items:center;gap:8px;color:#8b949e;cursor:pointer}.toggle+.toggle{margin-top:9px}.toggle input{width:auto;margin:0}button{width:100%;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#e6edf3;padding:9px;cursor:pointer;margin-top:8px}button:hover{border-color:#58a6ff}.muted{font-size:11px;color:#8b949e;line-height:1.45}.error{color:#f85149;min-height:20px;margin-top:10px}.batch-row{display:grid;grid-template-columns:1fr auto;align-items:center;gap:8px;margin-top:8px;font:12px ui-monospace,monospace}.batch-row button{width:auto;padding:5px 8px;margin:0}.bot-summary{margin:8px 0;color:#8b949e}.client-pin{width:18px;height:18px;border-radius:50% 50% 50% 0;background:#39c5cf;border:2px solid #d7ffff;transform:rotate(-45deg);box-shadow:0 2px 5px #0009}.client-pin-wrap{background:transparent;border:0}.server-label{background:#161b22;color:#e6edf3;border:1px solid #58a6ff;border-radius:4px;box-shadow:none;padding:2px 5px}.leaflet-control-attribution{background:#161b22cc!important;color:#8b949e}.leaflet-control-attribution a{color:#58a6ff}@media(max-width:720px){header{flex-wrap:wrap}.summary{width:100%;margin-left:0}main{grid-template-columns:1fr;grid-template-rows:minmax(360px,1fr) auto}aside{border-left:0;border-top:1px solid #30363d}}
.node-row.top{top:24px;z-index:900}.node.valkey{max-width:260px;border-color:#39c5cf}.node.valkey.warn{border-color:#d29922;color:#d29922}.node.valkey.bad{border-color:#f85149;color:#f85149}
.toggle.disabled{opacity:.45;cursor:not-allowed}
</style></head><body>
<header><strong>TCP lab map & controls</strong><button id="grafanaToggle" class="grafana-toggle" type="button" hidden></button><div id="summary" class="summary">Loading...</div></header>
<main><div class="map-wrap"><div id="map"></div><div id="valkeyRow" class="node-row top"></div><div id="gateways" class="node-row bottom"></div></div>
<aside>
<div class="card"><div class="label">Map layers</div><label class="toggle"><input id="showClients" type="checkbox">Show clients</label><label class="toggle"><input id="showServers" type="checkbox" checked>Show active locations</label><label class="toggle"><input id="showGateways" type="checkbox" checked>Show gateways</label><label class="toggle"><input id="showValkey" type="checkbox" checked>Show Valkey</label></div>
<div class="card"><div class="label">Map update rate</div><div id="refreshRateValue" class="value route">4 Hz</div><input id="refreshRate" type="range" min="1" max="20" step="1" value="4"><div class="muted">Target rate; slow Kubernetes snapshots never overlap.</div></div>
<div class="card"><div class="label">Gateway/server polling</div><div id="infrastructureRateValue" class="value route">2 seconds</div><input id="infrastructureRate" type="range" min="0.5" max="5" step="0.5" value="2"><div class="muted">Refreshes Kubernetes pod and node state independently of map updates.</div></div>
<div class="card"><div class="label">Dummy clients</div><input id="botCount" type="number" min="1" max="500" value="10"><label class="toggle"><input id="botServerRelevance" type="checkbox" checked>Server relevance</label><label id="botCrossServerRelevanceLabel" class="toggle"><input id="botCrossServerRelevance" type="checkbox" checked>Cross-server relevance</label><label id="botSpatialRelevanceLabel" class="toggle"><input id="botSpatialRelevance" type="checkbox" checked>Spatial relevance</label><button id="spawn">Spawn batch</button><div id="botState" class="bot-summary">0 active</div><div id="botBatches"></div><button id="despawn">Despawn all</button></div>
<div id="error" class="error"></div>
</aside></main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const storedView=JSON.parse(sessionStorage.getItem('tcp-lab-dashboard-map-view')||'null');const map=L.map('map',{worldCopyJump:true,minZoom:2}).setView(storedView?.center||[20,0],storedView?.zoom||2);L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(map);map.on('moveend zoomend',()=>sessionStorage.setItem('tcp-lab-dashboard-map-view',JSON.stringify({center:[map.getCenter().lat,map.getCenter().lng],zoom:map.getZoom()})));
const serverLayer=L.layerGroup().addTo(map),clientLayer=L.layerGroup().addTo(map),serverMarkers=new Map(),clientMarkers=new Map();let lastData=null,refreshHz=4;
const request=async(path,options={})=>{const response=await fetch(path,{cache:'no-store',...options});const body=await response.json();if(!response.ok)throw new Error(body.error||response.statusText);return body};
const post=(path,body={})=>request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const pageParams=new URLSearchParams(location.search),grafanaMode=pageParams.get('grafana'),grafanaOrigin=pageParams.get('grafana_origin')||'http://127.0.0.1:3000',grafanaToggle=document.getElementById('grafanaToggle');function navigateGrafana(){window.top.location=grafanaMode==='solo'?`${grafanaOrigin}/d/tcp-lab-live/tcp-lab-live-pressure`:`${grafanaOrigin}/d-solo/tcp-lab-map/tcp-lab-map?orgId=1&panelId=1`}if(grafanaMode){grafanaToggle.hidden=false;grafanaToggle.textContent=grafanaMode==='solo'?'Exit map view':'Open map view';grafanaToggle.onclick=navigateGrafana}
function render(data){lastData=data;document.getElementById('summary').textContent=`${data.gateway_count} gateways · ${data.connected_clients}/${data.total_clients} clients connected · ${data.dummy_processes} dummy processes · ${data.backends.length} locations · ${data.not_ready_pods} not ready · ${data.unknown_pods} unknown`;const visibleServers=new Set(),visibleClients=new Set();
 if(document.getElementById('showServers').checked)for(const server of data.backends){if(!server.latitude&&server.latitude!==0)continue;visibleServers.add(server.server);let marker=serverMarkers.get(server.server);if(!marker){marker=L.circleMarker([server.latitude,server.longitude],{radius:10,color:'#3fb950',weight:3,fillColor:'#0d1117',fillOpacity:1}).addTo(serverLayer);marker.bindTooltip('',{permanent:true,direction:'top',className:'server-label'});marker.bindPopup('');serverMarkers.set(server.server,marker)}marker.serverData=server;marker.setLatLng([server.latitude,server.longitude]);marker.getTooltip().setContent(`Location ${server.location_id}`);marker.getPopup().setContent(`<b>Location ${server.location_id}</b><br>${server.server}<br>${server.instance}<br>${server.node}<br>${server.current} clients`)}for(const [key,marker] of serverMarkers)if(!visibleServers.has(key)){marker.remove();serverMarkers.delete(key)}
 if(document.getElementById('showClients').checked)for(const client of data.sessions){if(client.latitude==null||client.longitude==null)continue;const key=`${client.gateway}:${client.id||client.src}`;visibleClients.add(key);let marker=clientMarkers.get(key);if(!marker){const icon=L.divIcon({className:'client-pin-wrap',html:'<div class="client-pin"></div>',iconSize:[18,25],iconAnchor:[9,25]});marker=L.marker([client.latitude,client.longitude],{icon}).addTo(clientLayer);marker.bindTooltip('');marker.bindPopup('');clientMarkers.set(key,marker)}marker.setLatLng([client.latitude,client.longitude]);marker.getTooltip().setContent(client.client_uid||client.src);marker.getPopup().setContent(`<b>${client.client_uid||'Client'}</b><br>Status: ${client.status}<br>Gateway: ${client.gateway}<br>Location: ${client.location_id}<br>Server: ${client.server}`)}for(const [key,marker] of clientMarkers)if(!visibleClients.has(key)){marker.remove();clientMarkers.delete(key)}
 const gateways=document.getElementById('gateways');gateways.replaceChildren();if(document.getElementById('showGateways').checked)for(const item of data.gateway_instances){const node=document.createElement('div');node.className=`node gateway ${item.status}`;node.textContent=item.name;node.title=`${item.status.replace('_',' ')} · ${item.node}`;gateways.appendChild(node)}
 renderValkey(data);
}
function renderValkey(data){const row=document.getElementById('valkeyRow');row.replaceChildren();if(!document.getElementById('showValkey').checked)return;const valkey=data.valkey||{},workers=valkey.workers||[];const problem=valkey.error||valkey.unjoined>0;const cluster=document.createElement('div');cluster.className=`node valkey${valkey.error?' bad':problem?' warn':''}`;cluster.textContent=valkey.error?'Valkey unavailable':`Valkey · ${valkey.primaries??'—'}P / ${valkey.replicas??'—'}R${valkey.unjoined?` · ${valkey.unjoined} unjoined`:''}`;cluster.title=valkey.error||`${workers.filter(worker=>worker.node!=='unassigned').length} database workers`;row.appendChild(cluster);for(const worker of workers){const node=document.createElement('div');if(worker.node==='unassigned'){node.className='node valkey warn';node.textContent=`Scheduling · ${worker.pods} Valkey pod${worker.pods===1?'':'s'}`;node.title='Created pods waiting for Kubernetes node assignment'}else{node.className=`node valkey ${worker.status}`;node.textContent=`${worker.node} · ${worker.primaries}P / ${worker.replicas}R`;node.title=`${worker.pods} pods · ${worker.unjoined} unjoined`}row.appendChild(node)}}
async function refresh(){try{const includeClients=document.getElementById('showClients').checked,data=await request(`/api/connections?include_clients=${includeClients}`);render(data);document.getElementById('error').textContent=(data.errors||[]).join(' · ')}catch(error){document.getElementById('error').textContent=error.message}}
async function refreshLoop(){const started=performance.now();await refresh();setTimeout(refreshLoop,Math.max(0,1000/refreshHz-(performance.now()-started)))}
function renderBots(bots){document.getElementById('botState').textContent=`${bots.states.ready} ready · ${bots.states.gateway} gateway · ${bots.states.disconnected} disconnected`;const list=document.getElementById('botBatches');list.replaceChildren();for(const batch of bots.batches){const row=document.createElement('div');row.className='batch-row';const label=document.createElement('span'),parts=[];if(batch.server_relevance)parts.push('server');if(batch.cross_server_relevance)parts.push('cross-server');if(batch.spatial_relevance)parts.push('spatial');label.textContent=`Batch ${batch.id}: ${batch.count} · ${parts.join(' + ')||'none'}`;const remove=document.createElement('button');remove.textContent='Despawn';remove.onclick=async()=>{try{renderBots(await post('/api/bots/despawn',{batch_id:batch.id}))}catch(error){document.getElementById('error').textContent=error.message}};row.append(label,remove);list.appendChild(row)}}
async function refreshBots(){try{renderBots(await request('/api/bots'))}catch(error){document.getElementById('error').textContent=error.message}}
const botCount=document.getElementById('botCount'),botServerRelevance=document.getElementById('botServerRelevance'),botCrossServerRelevance=document.getElementById('botCrossServerRelevance'),botSpatialRelevance=document.getElementById('botSpatialRelevance');botCount.value=sessionStorage.getItem('tcp-lab-dashboard-bot-count')||botCount.value;botCount.oninput=()=>sessionStorage.setItem('tcp-lab-dashboard-bot-count',botCount.value);function updateBotRelevance(){const none=!botServerRelevance.checked&&!botCrossServerRelevance.checked;botSpatialRelevance.disabled=none;document.getElementById('botSpatialRelevanceLabel').classList.toggle('disabled',none);sessionStorage.setItem('tcp-lab-dashboard-bot-server-relevance',String(botServerRelevance.checked));sessionStorage.setItem('tcp-lab-dashboard-bot-cross-server-relevance',String(botCrossServerRelevance.checked));sessionStorage.setItem('tcp-lab-dashboard-bot-spatial-relevance',String(botSpatialRelevance.checked))}for(const [input,key] of [[botServerRelevance,'server'],[botCrossServerRelevance,'cross-server'],[botSpatialRelevance,'spatial']]){const stored=sessionStorage.getItem(`tcp-lab-dashboard-bot-${key}-relevance`);input.checked=stored===null||stored==='true';input.onchange=updateBotRelevance}updateBotRelevance();document.getElementById('spawn').onclick=async()=>{try{await post('/api/bots/spawn',{count:Number(botCount.value),server_relevance:botServerRelevance.checked,cross_server_relevance:botCrossServerRelevance.checked,spatial_relevance:botSpatialRelevance.checked});refreshBots()}catch(error){document.getElementById('error').textContent=error.message}};document.getElementById('despawn').onclick=async()=>{try{await post('/api/bots/despawn-all');refreshBots()}catch(error){document.getElementById('error').textContent=error.message}};
const layerDefaults={showClients:false,showServers:true,showGateways:true,showValkey:true};for(const id of Object.keys(layerDefaults)){const input=document.getElementById(id),stored=sessionStorage.getItem(`tcp-lab-dashboard-${id}`);input.checked=stored===null?layerDefaults[id]:stored==='true';input.onchange=()=>{sessionStorage.setItem(`tcp-lab-dashboard-${id}`,String(input.checked));lastData&&render(lastData)}}const refreshRate=document.getElementById('refreshRate');refreshHz=Math.max(1,Math.min(20,Number(sessionStorage.getItem('tcp-lab-dashboard-refresh-hz')||4)));refreshRate.value=refreshHz;document.getElementById('refreshRateValue').textContent=`${refreshHz} Hz`;refreshRate.oninput=event=>{refreshHz=Number(event.target.value);sessionStorage.setItem('tcp-lab-dashboard-refresh-hz',refreshHz);document.getElementById('refreshRateValue').textContent=`${refreshHz} Hz`};const infrastructureRate=document.getElementById('infrastructureRate');let infrastructureSeconds=Math.max(.5,Math.min(5,Number(sessionStorage.getItem('tcp-lab-infrastructure-refresh-seconds')||2)));function showInfrastructureRate(){document.getElementById('infrastructureRateValue').textContent=`${infrastructureSeconds} second${infrastructureSeconds===1?'':'s'}`}async function setInfrastructureRate(){try{await post('/api/infrastructure-rate',{seconds:infrastructureSeconds})}catch(error){document.getElementById('error').textContent=error.message}}infrastructureRate.value=infrastructureSeconds;showInfrastructureRate();infrastructureRate.oninput=event=>{infrastructureSeconds=Number(event.target.value);sessionStorage.setItem('tcp-lab-infrastructure-refresh-seconds',infrastructureSeconds);showInfrastructureRate();setInfrastructureRate()};setInfrastructureRate();refreshLoop();refreshBots();setInterval(refreshBots,2000);
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


def list_server_assignments(server_pods: list[dict[str, str]]) -> list[dict]:
    ready_pods = {
        pod["name"]: pod for pod in server_pods if pod["status"] == "ready"
    }
    query = '{__name__=~"tcp_server_assignment(_generation|_latitude_degrees|_longitude_degrees)?|tcp_server_entities"}'
    raw = run(
        "kubectl", "get", "--raw",
        f"/api/v1/namespaces/{NAMESPACE}/services/http:prometheus:9090/proxy/"
        f"api/v1/query?query={quote(query)}",
    )
    metrics = {}
    for sample in json.loads(raw).get("data", {}).get("result", []):
        labels = sample.get("metric", {})
        pod_name = labels.get("pod", "")
        if pod_name not in ready_pods:
            continue
        metrics.setdefault(pod_name, {})[labels.get("__name__", "")] = (
            labels, float(sample["value"][1])
        )

    assignments = []
    for pod_name, values in metrics.items():
        assigned = values.get("tcp_server_assignment")
        latitude = values.get("tcp_server_assignment_latitude_degrees")
        longitude = values.get("tcp_server_assignment_longitude_degrees")
        if not assigned or assigned[1] != 1 or not latitude or not longitude:
            continue
        labels = assigned[0]
        assignments.append({
            "server": labels.get("server_id", ""),
            "location_id": int(labels.get("location_id", 0)),
            "latitude": latitude[1],
            "longitude": longitude[1],
            "instance": pod_name,
            "node": ready_pods[pod_name]["node"],
            "current": int(values.get("tcp_server_entities", ({}, 0))[1]),
        })
    return sorted(assignments, key=lambda item: item["location_id"])


def list_valkey_pods(node_statuses: dict[str, str]) -> list[dict]:
    raw = run(
        "kubectl", "get", "pods", "-n", NAMESPACE,
        "-l", "valkey.io/cluster=tcp-lab", "-o", "json",
    )
    result = []
    for item in json.loads(raw).get("items", []):
        metadata = item.get("metadata", {})
        spec = item.get("spec", {})
        status = item.get("status", {})
        result.append({
            "instance": metadata.get("name", ""),
            "node": spec.get("nodeName", ""),
            "ip": status.get("podIP", ""),
            "pod_status": application_pod(item, node_statuses)["status"],
        })
    return sorted(result, key=lambda item: item["instance"])


def inspect_valkey_cluster(valkey_pods: list[dict]) -> dict:
    summary = {"primaries": None, "replicas": None, "unjoined": None, "error": ""}
    if not valkey_pods:
        return {"primaries": 0, "replicas": 0, "unjoined": 0, "error": ""}
    ready = next((item for item in valkey_pods if item["pod_status"] == "ready"), None)
    if ready is None:
        summary["error"] = "no ready pod is available for cluster inspection"
        return summary
    try:
        output = run(
            "kubectl", "exec", "-n", NAMESPACE, ready["instance"], "--",
            "valkey-cli", "cluster", "nodes",
        )
        known_ips = set()
        role_by_ip = {}
        primaries = replicas = 0
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 3:
                continue
            known_ips.add(fields[1].split("@", 1)[0].rsplit(":", 1)[0])
            flags = set(fields[2].split(","))
            ip = fields[1].split("@", 1)[0].rsplit(":", 1)[0]
            if flags.intersection({"fail", "fail?", "handshake", "noaddr"}):
                role_by_ip[ip] = "unknown"
                continue
            if "master" in flags:
                role_by_ip[ip] = "primary"
                primaries += 1
            elif "slave" in flags:
                role_by_ip[ip] = "replica"
                replicas += 1
        pod_ips = {item["ip"] for item in valkey_pods if item["ip"]}
        workers = {}
        for item in valkey_pods:
            node = item["node"] or "unassigned"
            worker = workers.setdefault(node, {
                "node": node, "pods": 0, "primaries": 0, "replicas": 0,
                "unjoined": 0, "unknown": 0, "not_ready": 0, "status": "ready",
            })
            worker["pods"] += 1
            if item["pod_status"] == "unknown":
                worker["unknown"] += 1
            elif item["pod_status"] == "not_ready":
                worker["not_ready"] += 1
            role = role_by_ip.get(item["ip"])
            if not item["ip"] or item["ip"] not in known_ips:
                worker["unjoined"] += 1
            elif role == "primary":
                worker["primaries"] += 1
            elif role == "replica":
                worker["replicas"] += 1
            elif role == "unknown":
                worker["unknown"] += 1
        for worker in workers.values():
            if worker["unknown"]:
                worker["status"] = "unknown"
            elif worker["not_ready"]:
                worker["status"] = "not_ready"
        return {
            "primaries": primaries,
            "replicas": replicas,
            "unjoined": sum(
                1 for item in valkey_pods
                if not item["ip"] or item["ip"] not in known_ips
            ),
            "workers": sorted(workers.values(), key=lambda item: item["node"]),
            "error": "",
        }
    except Exception as error:
        summary["error"] = str(error)
        return summary


def inspect_pod(pod: dict, include_clients: bool = True) -> dict:
    pod = dict(pod)
    pod.update(clients=0, backend_sessions=0, sessions=[], error="")
    try:
        raw = run(
            "kubectl", "get", "--raw",
            f'/api/v1/namespaces/{NAMESPACE}/pods/{pod["name"]}:8404/proxy/stats'
            f'?include_sessions={str(include_clients).lower()}',
        )
        stats = json.loads(raw)
        for item in stats.get("sessions", []):
            pod["sessions"].append({
                "id": item.get("id", ""), "gateway": pod["name"], "src": item.get("client", ""),
                "client_uid": item.get("client_uid", ""),
                "location_id": item.get("location_id"), "server": item.get("server", ""),
                "latitude": item.get("latitude"), "longitude": item.get("longitude"),
                "status": item.get("status", "gateway"),
            })
        pod["clients"] = int(stats.get("session_count", len(pod["sessions"])))
        pod["backend_sessions"] = int(stats.get(
            "ready_session_count", sum(item["status"] == "ready" for item in pod["sessions"])
        ))
    except Exception as error:
        pod["error"] = str(error)
    return pod


def collect_infrastructure() -> dict:
    node_statuses = list_node_statuses()
    server_pods = list_server_pods(node_statuses)
    valkey_pods = list_valkey_pods(node_statuses)
    try:
        server_assignments = list_server_assignments(server_pods)
        assignment_error = ""
    except Exception as error:
        server_assignments = []
        assignment_error = str(error)
    return {
        "gateway_instances": list_pods(node_statuses),
        "server_pods": server_pods,
        "server_assignments": server_assignments,
        "assignment_error": assignment_error,
        "valkey_pods": valkey_pods,
        "valkey": inspect_valkey_cluster(valkey_pods),
    }


def refresh_infrastructure() -> None:
    while True:
        try:
            data = collect_infrastructure()
            with INFRASTRUCTURE_LOCK:
                INFRASTRUCTURE_CACHE.update(data=data, error="")
        except Exception as error:
            with INFRASTRUCTURE_LOCK:
                INFRASTRUCTURE_CACHE["error"] = str(error)
        with INFRASTRUCTURE_LOCK:
            interval = INFRASTRUCTURE_REFRESH["seconds"]
        INFRASTRUCTURE_WAKE.wait(interval)
        INFRASTRUCTURE_WAKE.clear()


def set_infrastructure_refresh(seconds: float) -> float:
    if not 0.5 <= seconds <= 5:
        raise ValueError("infrastructure polling must be between 0.5 and 5 seconds")
    with INFRASTRUCTURE_LOCK:
        INFRASTRUCTURE_REFRESH["seconds"] = seconds
    INFRASTRUCTURE_WAKE.set()
    return seconds


def cached_infrastructure() -> tuple[dict, str]:
    with INFRASTRUCTURE_LOCK:
        data = INFRASTRUCTURE_CACHE["data"]
        error = INFRASTRUCTURE_CACHE["error"]
    if data is None:
        data = collect_infrastructure()
        with INFRASTRUCTURE_LOCK:
            INFRASTRUCTURE_CACHE.update(data=data, error="")
        error = ""
    return data, error


def snapshot(include_clients: bool = True) -> dict:
    infrastructure, infrastructure_error = cached_infrastructure()
    gateway_instances = infrastructure["gateway_instances"]
    pods = [pod for pod in gateway_instances if pod["status"] == "ready"]
    server_pods = infrastructure["server_pods"]
    backends = [dict(item) for item in infrastructure["server_assignments"]]
    valkey_pods = infrastructure["valkey_pods"]
    valkey = infrastructure["valkey"]
    with ThreadPoolExecutor(max_workers=max(1, len(pods))) as pool:
        gateways = list(pool.map(lambda pod: inspect_pod(pod, include_clients), pods))
    server_instances = sorted(server_pods, key=lambda pod: pod["name"])

    application_instances = gateway_instances + server_instances + [
        {"status": pod["pod_status"]} for pod in valkey_pods
    ]
    bot_state = BOT_MANAGER.snapshot() if BOT_MANAGER is not None else {"total": 0}

    return {
        "gateway_count": len(gateways),
        "gateway_instances": gateway_instances,
        "total_clients": sum(p["clients"] for p in gateways),
        "connected_clients": sum(p["backend_sessions"] for p in gateways),
        "dummy_processes": bot_state["total"],
        "not_ready_pods": sum(instance["status"] == "not_ready" for instance in application_instances),
        "unknown_pods": sum(instance["status"] == "unknown" for instance in application_instances),
        "valkey": valkey,
        "sessions": [session for p in gateways for session in p["sessions"]],
        "backends": backends,
        "errors": (
            ([f"Kubernetes: {infrastructure_error}"] if infrastructure_error else [])
            + ([f'Assignments: {infrastructure["assignment_error"]}'] if infrastructure["assignment_error"] else [])
            + ([f'Valkey: {valkey["error"]}'] if valkey["error"] else [])
            + [f'{p["name"]}: {p["error"]}' for p in gateways if p["error"]]
        ),
    }


BOT_MANAGER = None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/map"):
            self.send(200, "text/html; charset=utf-8", MAP_PAGE.encode())
            return
        if path == "/api/connections":
            try:
                query = parse_qs(urlparse(self.path).query)
                include_clients = query.get("include_clients", ["true"])[0].lower() != "false"
                body = json.dumps(snapshot(include_clients)).encode()
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
                server_relevance = payload.get("server_relevance", True)
                spatial_relevance = payload.get("spatial_relevance", True)
                cross_server_relevance = payload.get("cross_server_relevance", True)
                if not all(isinstance(value, bool) for value in (
                    server_relevance, spatial_relevance, cross_server_relevance,
                )):
                    raise ValueError("relevance settings must be boolean")
                self.send_json(BOT_MANAGER.spawn(
                    int(payload["count"]), server_relevance, spatial_relevance,
                    cross_server_relevance,
                ))
            elif path == "/api/bots/despawn":
                self.send_json(BOT_MANAGER.despawn(int(payload["batch_id"])))
            elif path == "/api/bots/despawn-all":
                self.send_json(BOT_MANAGER.despawn_all())
            elif path == "/api/infrastructure-rate":
                self.send_json({"seconds": set_infrastructure_refresh(float(payload["seconds"]))})
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
    parser = argparse.ArgumentParser(description="Interactive TCP lab map and controls")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=9000)
    args = parser.parse_args()
    global BOT_MANAGER
    BOT_MANAGER = BotManager(args.gateway_host, args.gateway_port)
    threading.Thread(target=refresh_infrastructure, name="infrastructure-cache", daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Dashboard map: http://{args.host}:{args.port}")
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
