#!/usr/bin/env python3
"""Leaflet browser client backed by one persistent TCP gateway connection."""

import argparse
import json
import socket
import threading
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
    .server-label{background:#161b22;color:#e6edf3;border:1px solid #58a6ff;border-radius:4px;box-shadow:none;padding:2px 5px}
    .entity-label{background:#161b22;color:#39c5cf;border:1px solid #39c5cf;border-radius:4px;box-shadow:none;padding:2px 5px}
    .other-client-pin-wrap{background:transparent;border:0}.other-client-pin{display:block;width:18px;height:18px;background:#39c5cf;border:2px solid #d7ffff;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 2px 5px #0009}
    .other-client-pin.stale{background:#8b949e;border-color:#c9d1d9}
    @media(max-width:720px){main{grid-template-columns:1fr;grid-template-rows:minmax(360px,1fr) auto}aside{border-left:0;border-top:1px solid #30363d}}
  </style>
</head>
<body>
  <header><h1>Geographic client</h1><span class="hint">Click to teleport; use WASD to move</span><span class="status"><span id="dot" class="dot"></span><span id="connection">Connecting</span></span></header>
  <main><div id="map"></div><aside>
    <div class="card"><div class="label">Selected coordinate</div><div id="coordinate" class="value">Click the map</div></div>
    <div class="card"><div class="label">Client UID</div><div id="clientUid" class="value">—</div></div>
    <div class="card"><div class="label">Durable counter</div><div id="counter" class="value route">0</div><button id="increment">Increase counter</button></div>
    <div class="card"><div class="label">Nearest active location ID</div><div id="location" class="value route">—</div></div>
    <div class="card"><div class="label">Connected gateway pod</div><div id="gateway" class="value">—</div></div>
    <div class="card"><div class="label">Logical server</div><div id="server" class="value">—</div></div>
    <div class="card"><div class="label">Server pod</div><div id="instance" class="value">—</div></div>
    <div class="card"><div class="label">Ownership generation</div><div id="generation" class="value">—</div></div>
    <div class="card"><label class="toggle"><input id="showAllServers" type="checkbox">Show all active servers</label></div>
    <button id="reconnect">Reconnect gateway client</button><div id="error" class="error"></div>
  </aside></main>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    const map=L.map('map',{worldCopyJump:true,minZoom:2}).setView([25,0],2);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
    let selectedMarker=null,teleportTargetMarker=null,connectionLine=null,serverLayers=new Map(),otherMarkers=new Map(),locations=[],currentRoute=null,selectedPosition=null,connectionState='disconnected',lastInputAt=0,draining=false,inputInFlight=false,inputDirty=true,teleporting=false,reconnecting=false;
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
      renderServers();
    }
    function connection(state){connectionState=state;const dot=el('dot');dot.classList.toggle('ok',state==='ready');dot.classList.toggle('waiting',state==='gateway');el('connection').textContent=state==='ready'?'Connected':state==='gateway'?'Gateway connected; waiting for server':'Disconnected';}
    async function request(path,options){const response=await fetch(path,options);const body=await response.json();if(!response.ok)throw new Error(body.error||response.statusText);return body}
    function applyAuthoritativePosition(body){if(!Number.isFinite(body.client_latitude)||!Number.isFinite(body.client_longitude))return;selectedPosition=[body.client_latitude,body.client_longitude];el('coordinate').textContent=`${body.client_latitude.toFixed(5)}, ${body.client_longitude.toFixed(5)}`;if(selectedMarker)selectedMarker.setLatLng(selectedPosition);else selectedMarker=L.marker(selectedPosition).addTo(map)}
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
    async function reconnect(silent=false){if(reconnecting)return;reconnecting=true;try{const body=await request('/api/reconnect',{method:'POST'});showRoute(body||{});connection(body?'ready':'gateway');inputDirty=true;el('error').textContent=''}catch(error){if(!silent)el('error').textContent=error.message;refresh()}finally{reconnecting=false}}
    el('reconnect').addEventListener('click',()=>reconnect(false));
    async function refresh(){try{const state=await request('/api/state');connection(state.connection);if(state.latitude!==null&&!pendingCommands.length&&!teleporting)applyAuthoritativePosition({client_latitude:state.latitude,client_longitude:state.longitude});showRoute(state.route||{gateway:state.gateway})}catch(error){connection('disconnected')}}
    loadLocations().then(()=>{refresh();drainCommands()}).catch(error=>{el('error').textContent=error.message;refresh()});setInterval(sendInput,25);setInterval(expireEntityMarkers,250);setInterval(refresh,1000);setInterval(()=>{if(connectionState!=='ready')reconnect(true)},1000);setInterval(drainCommands,1000);setInterval(()=>loadLocations().catch(()=>{}),5000);
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


def make_handler(client: GatewayClient):
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
                else:
                    self.send_json({"error": "not found"}, 404)
            except (GatewayResponseError, OSError, ValueError, ConnectionError) as error:
                self.send_json({"error": str(error)}, 503)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if path == "/api/location":
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
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), make_handler(client))
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
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
