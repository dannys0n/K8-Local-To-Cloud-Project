#!/usr/bin/env python3
"""Leaflet browser client backed by one persistent TCP gateway connection."""

import argparse
import json
import math
import random
import socket
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>TCP lab map client</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
  <style>
    :root{color-scheme:dark;font:14px system-ui,sans-serif;background:#0d1117;color:#e6edf3}
    *{box-sizing:border-box}body{margin:0;display:grid;grid-template-rows:auto 1fr;height:100vh}
    header{display:flex;align-items:center;gap:18px;padding:12px 16px;background:#161b22;border-bottom:1px solid #30363d;z-index:1000}
    h1{font-size:16px;margin:0}.hint{color:#8b949e}.status{margin-left:auto;display:flex;align-items:center;gap:7px}
    .dot{width:9px;height:9px;border-radius:50%;background:#f85149}.dot.waiting{background:#d29922}.dot.ok{background:#3fb950}
    main{display:grid;grid-template-columns:minmax(0,1fr) 300px;min-height:0}#map{height:100%;background:#0d1117}
    aside{padding:16px;background:#161b22;border-left:1px solid #30363d;overflow:auto}
    .card{padding:13px;margin-bottom:12px;border:1px solid #30363d;border-radius:7px;background:#0d1117}
    .label{color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}
    .value{font-family:ui-monospace,monospace;overflow-wrap:anywhere}.route{font-size:18px;color:#58a6ff}
    input{width:100%;margin-bottom:8px;border:1px solid #30363d;border-radius:6px;background:#161b22;color:#e6edf3;padding:9px}
    .toggle{display:flex;align-items:center;gap:8px;color:#8b949e;cursor:pointer}.toggle+.toggle{margin-top:9px}.toggle input{width:auto;margin:0}
    button{width:100%;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#e6edf3;padding:9px;cursor:pointer}
    button:hover{border-color:#58a6ff}.error{color:#f85149;min-height:20px;margin-top:10px}
    .batch-row{display:grid;grid-template-columns:1fr auto;align-items:center;gap:8px;margin-top:8px;font:12px ui-monospace,monospace}.batch-row button{width:auto;padding:5px 8px}.bot-summary{margin:8px 0;color:#8b949e}
    .server-label{background:#161b22;color:#e6edf3;border:1px solid #58a6ff;border-radius:4px;box-shadow:none;padding:2px 5px}
    .entity-label{background:#161b22;color:#39c5cf;border:1px solid #39c5cf;border-radius:4px;box-shadow:none;padding:2px 5px}
    .other-client-pin-wrap{background:transparent;border:0}.other-client-pin{display:block;width:18px;height:18px;background:#39c5cf;border:2px solid #d7ffff;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 2px 5px #0009}
    .other-client-pin.stale{background:#8b949e;border-color:#c9d1d9}
    .infra-row{position:absolute;left:54px;right:12px;z-index:900;display:flex;justify-content:center;gap:7px;flex-wrap:wrap;pointer-events:none}.infra-row.top{top:12px}.infra-row.bottom{bottom:24px}
    .infra-node{max-width:180px;padding:6px 9px;border:1px solid #8b949e;border-radius:6px;background:#161b22e8;color:#e6edf3;font:11px ui-monospace,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;pointer-events:auto;box-shadow:0 2px 8px #0008}.infra-node.active{border-color:#3fb950;color:#3fb950}.infra-node.spare{border-color:#d29922;color:#d29922}.infra-node.not_ready{border-color:#f85149;color:#f85149}.infra-node.unknown{border-color:#8b949e;color:#8b949e}
    #infraEdges{position:absolute;inset:0;width:100%;height:100%;z-index:899;pointer-events:none}#infraStatus{position:absolute;left:12px;bottom:12px;z-index:901;color:#f85149;background:#161b22dd;padding:4px 7px;border-radius:4px;font-size:11px}
    @media(max-width:720px){main{grid-template-columns:1fr;grid-template-rows:minmax(360px,1fr) auto}aside{border-left:0;border-top:1px solid #30363d}}
  </style>
</head>
<body>
  <header><h1>Geographic client</h1><span class="hint">Click to teleport; use WASD to move</span><span class="status"><span id="dot" class="dot"></span><span id="connection">Connecting</span></span></header>
  <main><div id="map"><svg id="infraEdges"></svg><div id="spareRow" class="infra-row top"></div><div id="proxyRow" class="infra-row bottom"></div><div id="infraStatus" hidden></div></div><aside>
    <div class="card"><div class="label">Selected coordinate</div><div id="coordinate" class="value">Click the map</div></div>
    <div class="card"><div class="label">Client UID</div><div id="clientUid" class="value">—</div></div>
    <div class="card"><div class="label">Durable counter</div><div id="counter" class="value route">0</div><button id="increment">Increase counter</button></div>
    <div class="card"><div class="label">Nearest active location ID</div><div id="location" class="value route">—</div></div>
    <div class="card"><div class="label">Connected gateway pod</div><div id="gateway" class="value">—</div></div>
    <div class="card"><div class="label">Logical server</div><div id="server" class="value">—</div></div>
    <div class="card"><div class="label">Server pod</div><div id="instance" class="value">—</div></div>
    <div class="card"><div class="label">Ownership generation</div><div id="generation" class="value">—</div></div>
    <div class="card"><label class="toggle"><input id="showAllServers" type="checkbox">Show all active servers</label><label class="toggle"><input id="showProxies" type="checkbox">Show proxies</label><label class="toggle"><input id="showHotSwaps" type="checkbox">Show hot swaps</label></div>
    <div class="card"><div class="label">Dummy clients</div><input id="botBatchSize" type="number" min="1" max="500" value="10"><button id="spawnBots">Spawn batch</button><div id="botSummary" class="bot-summary">0 active</div><div id="botBatches"></div><button id="despawnAllBots">Despawn all</button></div>
    <button id="reconnect">Reconnect gateway client</button><div id="error" class="error"></div>
  </aside></main>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    const map=L.map('map',{worldCopyJump:true,minZoom:2}).setView([25,0],2);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
    let selectedMarker=null,teleportTargetMarker=null,connectionLine=null,serverLayers=new Map(),otherMarkers=new Map(),locations=[],infra=null,currentRoute=null,selectedPosition=null,connectionState='disconnected',lastInputAt=0,draining=false,inputInFlight=false,inputDirty=true,teleporting=false,reconnecting=false;
    const keys=new Set();
    const el=id=>document.getElementById(id);
    const clientUid=localStorage.getItem('tcp-lab-client-uid')||crypto.randomUUID();localStorage.setItem('tcp-lab-client-uid',clientUid);el('clientUid').textContent=clientUid;
    const inputSequenceKey=`tcp-lab-input-sequence-${clientUid}`;let inputSequence=Number(localStorage.getItem(inputSequenceKey)||0);
    const pendingKey=`tcp-lab-pending-${clientUid}`;let pendingCommands=JSON.parse(localStorage.getItem(pendingKey)||'[]').filter(command=>command.kind==='increment');localStorage.setItem(pendingKey,JSON.stringify(pendingCommands));
    function showRoute(body){
      currentRoute=body;
      el('location').textContent=body.location_id??'—'; el('server').textContent=body.server||'—';
      el('gateway').textContent=body.gateway||'—';el('instance').textContent=body.instance||'—';el('generation').textContent=body.generation??'—';
      if(body.client_uid===clientUid&&body.counter!==undefined)el('counter').textContent=body.counter;
      renderServers();renderInfra();
    }
    function connection(state){connectionState=state;const dot=el('dot');dot.classList.toggle('ok',state==='ready');dot.classList.toggle('waiting',state==='gateway');el('connection').textContent=state==='ready'?'Connected':state==='gateway'?'Gateway connected; waiting for server':'Disconnected';}
    async function request(path,options){const response=await fetch(path,options);const body=await response.json();if(!response.ok)throw new Error(body.error||response.statusText);return body}
    function applyAuthoritativePosition(body){if(!Number.isFinite(body.client_latitude)||!Number.isFinite(body.client_longitude))return;selectedPosition=[body.client_latitude,body.client_longitude];el('coordinate').textContent=`${body.client_latitude.toFixed(5)}, ${body.client_longitude.toFixed(5)}`;if(selectedMarker)selectedMarker.setLatLng(selectedPosition);else selectedMarker=L.marker(selectedPosition).addTo(map)}
    function renderBots(state){el('botSummary').textContent=`${state.states.ready} ready · ${state.states.gateway} gateway · ${state.states.disconnected} disconnected`;const list=el('botBatches');list.replaceChildren();for(const batch of state.batches){const row=document.createElement('div');row.className='batch-row';const label=document.createElement('span');label.textContent=`Batch ${batch.id}: ${batch.count}`;const remove=document.createElement('button');remove.textContent='Despawn';remove.addEventListener('click',async()=>{await request('/api/bots/despawn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({batch_id:batch.id})});refreshBots()});row.append(label,remove);list.appendChild(row)}}
    async function refreshBots(){try{renderBots(await request('/api/bots'))}catch(error){el('error').textContent=error.message}}
    function infraNode(item,kind){const node=document.createElement('div');node.className=`infra-node ${kind} ${item.status||''}`;node.dataset.name=item.name;node.textContent=item.name;node.title=[item.name,item.role,item.status?.replace('_',' '),item.ip,item.node].filter(Boolean).join('\n');return node}
    function drawProxyEdge(){const svg=el('infraEdges');svg.replaceChildren();if(!el('showProxies').checked||!selectedPosition||!currentRoute?.gateway)return;const node=[...el('proxyRow').children].find(item=>item.dataset.name===currentRoute.gateway);if(!node)return;const mapRect=el('map').getBoundingClientRect(),nodeRect=node.getBoundingClientRect(),start=map.latLngToContainerPoint(selectedPosition);svg.setAttribute('viewBox',`0 0 ${mapRect.width} ${mapRect.height}`);const line=document.createElementNS('http://www.w3.org/2000/svg','line');line.setAttribute('x1',start.x);line.setAttribute('y1',start.y);line.setAttribute('x2',nodeRect.left-mapRect.left+nodeRect.width/2);line.setAttribute('y2',nodeRect.top-mapRect.top+nodeRect.height/2);line.setAttribute('stroke','#3fb950');line.setAttribute('stroke-width','2');line.setAttribute('stroke-dasharray','7 6');svg.appendChild(line)}
    function renderInfra(){const proxies=el('proxyRow'),spares=el('spareRow');proxies.replaceChildren();spares.replaceChildren();if(infra&&el('showProxies').checked)for(const gateway of (infra.gateway_instances||infra.gateways)){const node=infraNode(gateway,'proxy');if(gateway.status==='ready'&&gateway.name===currentRoute?.gateway)node.classList.add('active');proxies.appendChild(node)}if(infra&&el('showHotSwaps').checked)for(const server of infra.server_instances.filter(item=>item.role==='spare'||item.status!=='ready'))spares.appendChild(infraNode(server,'spare'));requestAnimationFrame(drawProxyEdge)}
    async function loadInfra(){if(!el('showProxies').checked&&!el('showHotSwaps').checked){el('infraStatus').hidden=true;renderInfra();return}try{const response=await fetch('http://127.0.0.1:8080/api/connections',{cache:'no-store'});const body=await response.json();if(!response.ok)throw new Error(body.error||response.statusText);infra=body;el('infraStatus').hidden=true;renderInfra()}catch(error){infra=null;el('infraStatus').textContent='Infrastructure dashboard unavailable';el('infraStatus').hidden=false;renderInfra()}}
    function renderServers(){
      if(connectionLine){connectionLine.remove();connectionLine=null}
      const showAll=el('showAllServers').checked;
      const visible=new Set();
      locations.filter(server=>showAll||(currentRoute&&server.server===currentRoute.server)).forEach(server=>{
        visible.add(server.server);
        const active=currentRoute&&server.server===currentRoute.server;
        let layer=serverLayers.get(server.server);
        if(!layer){layer=L.circleMarker([server.latitude,server.longitude]).addTo(map);layer.bindTooltip('',{permanent:true,direction:'top',className:'server-label'});layer.bindPopup('');serverLayers.set(server.server,layer)}
        layer.setLatLng([server.latitude,server.longitude]);layer.setRadius(active?11:7);layer.setStyle({color:active?'#3fb950':'#58a6ff',weight:active?4:2,fillColor:'#0d1117',fillOpacity:1});
        layer.getTooltip().setContent(`Location ${server.location_id}`);layer.getPopup().setContent(`<b>Location ${server.location_id}</b><br>${server.server}<br>${server.latitude.toFixed(4)}, ${server.longitude.toFixed(4)}`);
        if(active&&selectedPosition)connectionLine=L.polyline([selectedPosition,[server.latitude,server.longitude]],{color:'#3fb950',weight:2,dashArray:'7 7',opacity:.8}).addTo(map);
      });
      for(const [server,layer] of serverLayers)if(!visible.has(server)){layer.remove();serverLayers.delete(server)}
    }
    function renderEntities(entities){const now=performance.now();for(const entity of entities){let item=otherMarkers.get(entity.uid);if(!item){const icon=L.divIcon({className:'other-client-pin-wrap',html:'<span class="other-client-pin"></span>',iconSize:[18,25],iconAnchor:[9,25]});const marker=L.marker([entity.latitude,entity.longitude],{icon}).addTo(map);marker.bindTooltip(entity.uid,{className:'entity-label'});item={marker,lastSeen:now};otherMarkers.set(entity.uid,item)}else{item.marker.setLatLng([entity.latitude,entity.longitude]);item.lastSeen=now}item.marker.getElement()?.querySelector('.other-client-pin')?.classList.remove('stale')}}
    function expireEntityMarkers(){const now=performance.now();for(const [uid,item] of otherMarkers){const age=now-item.lastSeen;if(age>=5000){item.marker.remove();otherMarkers.delete(uid)}else if(age>=1000)item.marker.getElement()?.querySelector('.other-client-pin')?.classList.add('stale')}}
    async function loadLocations(){
      const body=await request('/api/locations');locations=body.locations;renderServers();
    }
    async function drainCommands(){
      if(draining)return;draining=true;
      try{while(pendingCommands.length){const command=pendingCommands[0];const body=await request('/api/increment',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_uid:clientUid,operation_id:command.operation_id})});showRoute(body);connection('ready');pendingCommands.shift();localStorage.setItem(pendingKey,JSON.stringify(pendingCommands));el('error').textContent=''}}
      catch(error){el('error').textContent=error.message;refresh()}finally{draining=false}
    }
    map.on('click',async event=>{
      if(teleporting)return;
      const {lat,lng}=event.latlng.wrap();el('coordinate').textContent=`Target: ${lat.toFixed(5)}, ${lng.toFixed(5)}`;el('error').textContent='';
      if(teleportTargetMarker)teleportTargetMarker.setLatLng([lat,lng]);else teleportTargetMarker=L.circleMarker([lat,lng],{radius:9,color:'#d29922',weight:2,dashArray:'4 4',fillColor:'#d29922',fillOpacity:.15}).addTo(map);
      teleporting=true;inputSequence++;localStorage.setItem(inputSequenceKey,inputSequence);
      try{const body=await request('/api/location',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_uid:clientUid,sequence:inputSequence,latitude:lat,longitude:lng})});applyAuthoritativePosition(body);showRoute(body);renderEntities(body.entities||[]);connection('ready')}
      catch(error){el('error').textContent=error.message;refresh()}finally{if(teleportTargetMarker){teleportTargetMarker.remove();teleportTargetMarker=null}teleporting=false;inputDirty=true}
    });
    el('increment').addEventListener('click',()=>{pendingCommands.push({kind:'increment',operation_id:crypto.randomUUID()});localStorage.setItem(pendingKey,JSON.stringify(pendingCommands));drainCommands()});
    el('spawnBots').addEventListener('click',async()=>{try{await request('/api/bots/spawn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:Number(el('botBatchSize').value)})});refreshBots()}catch(error){el('error').textContent=error.message}});
    el('despawnAllBots').addEventListener('click',async()=>{try{await request('/api/bots/despawn-all',{method:'POST'});refreshBots()}catch(error){el('error').textContent=error.message}});
    function setKey(event,pressed){const key=event.key.toLowerCase();if(!'wasd'.includes(key))return;event.preventDefault();if(pressed)keys.add(key);else keys.delete(key);inputDirty=true}
    addEventListener('keydown',event=>setKey(event,true));addEventListener('keyup',event=>setKey(event,false));addEventListener('blur',()=>{keys.clear();inputDirty=true});
    async function sendInput(){
      if(inputInFlight||teleporting||pendingCommands.length||!currentRoute?.server)return;
      const x=(keys.has('d')?1:0)-(keys.has('a')?1:0),y=(keys.has('w')?1:0)-(keys.has('s')?1:0);
      const now=performance.now();if(x===0&&y===0&&!inputDirty&&now-lastInputAt<50)return;inputDirty=false;inputInFlight=true;lastInputAt=now;
      try{inputSequence++;localStorage.setItem(inputSequenceKey,inputSequence);const body=await request('/api/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_uid:clientUid,sequence:inputSequence,x,y})});applyAuthoritativePosition(body);showRoute(body);renderEntities(body.entities||[])}
      catch(error){el('error').textContent=error.message;inputDirty=true}finally{inputInFlight=false}
    }
    el('showAllServers').addEventListener('change',renderServers);
    el('showProxies').addEventListener('change',loadInfra);el('showHotSwaps').addEventListener('change',loadInfra);map.on('move zoom resize',drawProxyEdge);
    async function reconnect(silent=false){if(reconnecting)return;reconnecting=true;try{const body=await request('/api/reconnect',{method:'POST'});showRoute(body||{});connection(body?'ready':'gateway');inputDirty=true;el('error').textContent=''}catch(error){if(!silent)el('error').textContent=error.message;refresh()}finally{reconnecting=false}}
    el('reconnect').addEventListener('click',()=>reconnect(false));
    async function refresh(){try{const state=await request('/api/state');connection(state.connection);if(state.latitude!==null&&!pendingCommands.length&&!teleporting)applyAuthoritativePosition({client_latitude:state.latitude,client_longitude:state.longitude});showRoute(state.route||{gateway:state.gateway})}catch(error){connection('disconnected')}}
    loadLocations().then(()=>{refresh();refreshBots();drainCommands()}).catch(error=>{el('error').textContent=error.message;refresh()});setInterval(sendInput,25);setInterval(expireEntityMarkers,250);setInterval(refresh,1000);setInterval(refreshBots,1000);setInterval(()=>{if(connectionState!=='ready')reconnect(true)},1000);setInterval(drainCommands,1000);setInterval(loadInfra,2000);setInterval(()=>loadLocations().catch(()=>{}),5000);
  </script>
</body>
</html>"""


class GatewayResponseError(Exception):
    pass


class GatewayClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.sock = None
        self.stream = None
        self.connection = "disconnected"
        self.gateway = None
        self.route = None
        self.latitude = None
        self.longitude = None

    def _close(self):
        if self.stream:
            self.stream.close()
        if self.sock:
            self.sock.close()
        self.stream = self.sock = None
        with self.state_lock:
            self.connection = "disconnected"
            self.gateway = None
            self.route = None

    def _connect(self):
        self._close()
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.settimeout(10)
        self.stream = self.sock.makefile("rwb", buffering=0)
        with self.state_lock:
            self.connection = "gateway"
        try:
            route = self._exchange("@location any")
        except GatewayResponseError:
            route = None
        self.sock.settimeout(None)
        with self.state_lock:
            self.route = route
            if route:
                self.gateway = route.get("gateway")
                self.connection = "ready"

    def _exchange(self, command: str):
        self.stream.write(command.encode("utf-8") + b"\n")
        response = self.stream.readline()
        if not response:
            raise ConnectionError("gateway closed the connection")
        body = json.loads(response)
        if body.get("error"):
            raise GatewayResponseError(body["error"])
        return body

    def exchange(self, command: str):
        with self.lock:
            try:
                if self.stream is None:
                    self._connect()
                body = self._exchange(command)
            except GatewayResponseError:
                with self.state_lock:
                    self.connection = "gateway"
                    self.route = None
                raise
            except (OSError, ValueError, ConnectionError):
                self._close()
                try:
                    self._connect()
                    body = self._exchange(command)
                except (OSError, ValueError, ConnectionError):
                    self._close()
                    raise
            with self.state_lock:
                if body.get("gateway"):
                    self.gateway = body["gateway"]
                if body.get("server"):
                    self.route = body
                    self.connection = "ready"
            return body

    def move(self, client_uid: str, sequence: int, latitude: float, longitude: float):
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")
        if not client_uid or len(client_uid) > 128 or set(client_uid) - allowed or sequence < 0:
            raise ValueError("client identifier or input sequence is invalid")
        body = self.exchange(f"@teleport {client_uid} {sequence} {latitude:.8f} {longitude:.8f}")
        with self.state_lock:
            self.latitude, self.longitude, self.route = latitude, longitude, body
            self.gateway = body.get("gateway")
            self.connection = "ready"
        return body

    def send_input(self, client_uid: str, sequence: int, x: float, y: float):
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")
        if not client_uid or len(client_uid) > 128 or set(client_uid) - allowed:
            raise ValueError("client identifier is invalid")
        if sequence < 0 or not (-1 <= x <= 1 and -1 <= y <= 1):
            raise ValueError("input intent is invalid")
        body = self.exchange(f"@input {client_uid} {sequence} {x:.3f} {y:.3f}")
        with self.state_lock:
            self.latitude = body["client_latitude"]
            self.longitude = body["client_longitude"]
            self.route = body
        return body

    def increment(self, client_uid: str, operation_id: str):
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")
        if not client_uid or len(client_uid) > 128 or not operation_id or len(operation_id) > 128 or set(client_uid) - allowed or set(operation_id) - allowed:
            raise ValueError("client and operation identifiers are invalid")
        return self.exchange(f"@increment {client_uid} {operation_id}")

    def reconnect(self):
        with self.lock:
            self._connect()
            if self.latitude is not None:
                try:
                    route = self._exchange(f"@position {self.latitude:.8f} {self.longitude:.8f}")
                except GatewayResponseError:
                    route = None
                with self.state_lock:
                    self.route = route
                    if route:
                        self.gateway = route.get("gateway")
                        self.connection = "ready"
            with self.state_lock:
                return self.route

    def snapshot(self):
        with self.state_lock:
            return {"connection": self.connection, "connected": self.connection != "disconnected", "gateway": self.gateway, "latitude": self.latitude, "longitude": self.longitude, "route": self.route}

    def close(self):
        with self.lock:
            self._close()


class Bot:
    def __init__(self, host: str, port: int):
        self.uid = f"bot:{uuid.uuid4()}"
        self.client = GatewayClient(host, port)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name=self.uid, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        sock = self.client.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def run(self):
        sequence = 0
        axis_x = axis_y = 0.0
        rng = random.Random(self.uid)
        next_direction = time.monotonic()
        next_counter = next_direction + rng.random()
        next_reconnect = next_direction
        pending_operation = None
        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                if self.client.snapshot()["connection"] != "ready":
                    if started >= next_reconnect:
                        try:
                            self.client.reconnect()
                        except (GatewayResponseError, OSError, ValueError, ConnectionError):
                            pass
                        next_reconnect = time.monotonic() + 0.75 + rng.random() * 0.5
                    self.stopped.wait(0.05)
                    continue
                if started >= next_direction:
                    angle = rng.random() * math.tau
                    axis_x, axis_y = math.cos(angle), math.sin(angle)
                    next_direction = started + 3
                if started >= next_counter:
                    if pending_operation is None:
                        pending_operation = f"bot-op:{uuid.uuid4()}"
                    try:
                        self.client.increment(self.uid, pending_operation)
                        pending_operation = None
                    except (GatewayResponseError, OSError, ValueError, ConnectionError):
                        pass
                    next_counter = time.monotonic() + 1
                if self.client.snapshot()["connection"] != "ready":
                    continue
                sequence += 1
                try:
                    self.client.send_input(self.uid, sequence, axis_x, axis_y)
                except (GatewayResponseError, OSError, ValueError, ConnectionError):
                    pass
                self.stopped.wait(max(0, 0.05 - (time.monotonic() - started)))
        finally:
            self.client.close()


class BotManager:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.next_batch = 1
        self.batches: dict[int, list[Bot]] = {}

    def spawn(self, count: int) -> dict:
        if count < 1 or count > 500:
            raise ValueError("batch size must be between 1 and 500")
        bots = [Bot(self.host, self.port) for _ in range(count)]
        with self.lock:
            batch_id = self.next_batch
            self.next_batch += 1
            self.batches[batch_id] = bots
        for bot in bots:
            bot.start()
        return self.snapshot()

    def despawn(self, batch_id: int) -> dict:
        with self.lock:
            bots = self.batches.pop(batch_id, None)
        if bots is None:
            raise ValueError("batch not found")
        for bot in bots:
            bot.stop()
        return self.snapshot()

    def despawn_all(self) -> dict:
        with self.lock:
            batches = list(self.batches.values())
            self.batches.clear()
        for bots in batches:
            for bot in bots:
                bot.stop()
        return self.snapshot()

    def snapshot(self) -> dict:
        with self.lock:
            batches = [{"id": batch_id, "count": len(bots)} for batch_id, bots in self.batches.items()]
            bots = [bot for batch in self.batches.values() for bot in batch]
        states = {"ready": 0, "gateway": 0, "disconnected": 0}
        for bot in bots:
            connection = bot.client.snapshot()["connection"]
            states[connection if connection in states else "disconnected"] += 1
        return {"total": len(bots), "states": states, "batches": batches}


def make_handler(client: GatewayClient, bots: BotManager):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, body, status=200):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path == "/":
                    encoded = PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                elif path == "/api/state":
                    self.send_json(client.snapshot())
                elif path == "/api/locations":
                    self.send_json(client.exchange("@locations"))
                elif path == "/api/bots":
                    self.send_json(bots.snapshot())
                else:
                    self.send_json({"error": "not found"}, 404)
            except (GatewayResponseError, OSError, ValueError, ConnectionError) as error:
                self.send_json({"error": str(error)}, 503)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if path == "/api/bots/spawn":
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    self.send_json(bots.spawn(int(payload["count"])))
                elif path == "/api/bots/despawn":
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    self.send_json(bots.despawn(int(payload["batch_id"])))
                elif path == "/api/bots/despawn-all":
                    self.send_json(bots.despawn_all())
                elif path == "/api/location":
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    latitude, longitude = float(payload["latitude"]), float(payload["longitude"])
                    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                        raise ValueError("coordinate is outside the world bounds")
                    self.send_json(client.move(str(payload["client_uid"]), int(payload["sequence"]), latitude, longitude))
                elif path == "/api/reconnect":
                    self.send_json(client.reconnect())
                elif path == "/api/increment":
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    self.send_json(client.increment(str(payload["client_uid"]), str(payload["operation_id"])))
                elif path == "/api/input":
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    self.send_json(client.send_input(str(payload["client_uid"]), int(payload["sequence"]), float(payload["x"]), float(payload["y"])))
                else:
                    self.send_json({"error": "not found"}, 404)
            except (GatewayResponseError, KeyError, OSError, TypeError, ValueError, ConnectionError) as error:
                self.send_json({"error": str(error)}, 400)

        def log_message(self, _format, *_args):
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Leaflet browser client for the TCP lab")
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=9000)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=0, help="local HTTP port; 0 asks the operating system for a free port")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    client = GatewayClient(args.gateway_host, args.gateway_port)
    bots = BotManager(args.gateway_host, args.gateway_port)
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), make_handler(client, bots))
    actual_port = server.server_address[1]
    url = f"http://{args.listen_host}:{actual_port}"
    print(f"Map client: {url}")
    print(f"Gateway: {args.gateway_host}:{args.gateway_port}")
    if not args.no_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        bots.despawn_all()
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
