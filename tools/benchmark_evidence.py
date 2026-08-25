#!/usr/bin/env python3
"""Record synchronized visual and metrics evidence for a scale benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".generated" / "scale-benchmarks"
MILESTONES = {
    "baseline", "load_applied", "scale_requested", "autoscaler_enabled",
    "node_observed", "node_ready", "scale_target_ready", "autoscale_target_ready",
    "worker_stop_requested", "worker_loss_recovered", "load_removed",
    "application_minimum_requested", "autoscale_minimum_ready", "node_removed",
    "node_scale_down_complete", "run_failed",
}
PROMETHEUS_QUERIES = {
    "gateway_network_rx_bytes_per_second": 'sum(rate(container_network_receive_bytes_total{namespace="tcp-lab",pod=~"gateway-.*",pod!~"gateway-autoscaler-.*"}[10s]))',
    "gateway_network_tx_bytes_per_second": 'sum(rate(container_network_transmit_bytes_total{namespace="tcp-lab",pod=~"gateway-.*",pod!~"gateway-autoscaler-.*"}[10s]))',
    "server_network_rx_bytes_per_second": 'sum(rate(container_network_receive_bytes_total{namespace="tcp-lab",pod=~"tcp-server-.*",pod!~"tcp-server-autoscaler-.*"}[10s]))',
    "server_network_tx_bytes_per_second": 'sum(rate(container_network_transmit_bytes_total{namespace="tcp-lab",pod=~"tcp-server-.*",pod!~"tcp-server-autoscaler-.*"}[10s]))',
    "valkey_network_rx_bytes_per_second": 'sum(rate(container_network_receive_bytes_total{namespace="tcp-lab",pod=~"valkey-tcp-lab-.*"}[10s]))',
    "valkey_network_tx_bytes_per_second": 'sum(rate(container_network_transmit_bytes_total{namespace="tcp-lab",pod=~"valkey-tcp-lab-.*"}[10s]))',
    "gateway_network_rx_bytes_per_second_by_pod": 'sum by (pod) (rate(container_network_receive_bytes_total{namespace="tcp-lab",pod=~"gateway-.*",pod!~"gateway-autoscaler-.*"}[10s]))',
    "gateway_network_tx_bytes_per_second_by_pod": 'sum by (pod) (rate(container_network_transmit_bytes_total{namespace="tcp-lab",pod=~"gateway-.*",pod!~"gateway-autoscaler-.*"}[10s]))',
    "server_network_rx_bytes_per_second_by_pod": 'sum by (pod) (rate(container_network_receive_bytes_total{namespace="tcp-lab",pod=~"tcp-server-.*",pod!~"tcp-server-autoscaler-.*"}[10s]))',
    "server_network_tx_bytes_per_second_by_pod": 'sum by (pod) (rate(container_network_transmit_bytes_total{namespace="tcp-lab",pod=~"tcp-server-.*",pod!~"tcp-server-autoscaler-.*"}[10s]))',
    "valkey_network_rx_bytes_per_second_by_pod": 'sum by (pod) (rate(container_network_receive_bytes_total{namespace="tcp-lab",pod=~"valkey-tcp-lab-.*"}[10s]))',
    "valkey_network_tx_bytes_per_second_by_pod": 'sum by (pod) (rate(container_network_transmit_bytes_total{namespace="tcp-lab",pod=~"valkey-tcp-lab-.*"}[10s]))',
    "gateway_cpu_cores_by_pod": 'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="tcp-lab",container="gateway"}[10s]))',
    "server_cpu_cores_by_pod": 'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="tcp-lab",container="server",pod=~"tcp-server-.*"}[10s]))',
    "valkey_cpu_cores_by_pod": 'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="tcp-lab",container="server",pod=~"valkey-tcp-lab-.*"}[10s]))',
    "gateway_memory_bytes_by_pod": 'sum by (pod) (container_memory_working_set_bytes{namespace="tcp-lab",container="gateway"})',
    "server_memory_bytes_by_pod": 'sum by (pod) (container_memory_working_set_bytes{namespace="tcp-lab",container="server",pod=~"tcp-server-.*"})',
    "valkey_memory_bytes_by_pod": 'sum by (pod) (container_memory_working_set_bytes{namespace="tcp-lab",container="server",pod=~"valkey-tcp-lab-.*"})',
    "network_receive_drops_per_second": 'sum(rate(container_network_receive_packets_dropped_total{namespace="tcp-lab"}[10s]))',
    "network_transmit_drops_per_second": 'sum(rate(container_network_transmit_packets_dropped_total{namespace="tcp-lab"}[10s]))',
    "gateway_sessions": "sum(tcp_gateway_sessions)",
    "gateway_ready_sessions": "sum(tcp_gateway_ready_sessions)",
    "gateway_backend_failures_per_second": "sum(rate(tcp_gateway_backend_failures_total[10s]))",
    "server_connections": "sum(tcp_server_connections)",
    "server_entities": "sum(tcp_server_entities)",
    "server_input_queue_full_per_second": "sum(rate(tcp_server_input_queue_full_total[10s]))",
    "server_visibility_failures_per_second": "sum(rate(tcp_server_visibility_failures_total[10s]))",
    "gateway_to_server_latency_p95_seconds": "histogram_quantile(0.95, sum by (le) (rate(tcp_gateway_backend_duration_seconds_bucket[1m])))",
    "server_to_valkey_latency_p95_seconds": "histogram_quantile(0.95, sum by (le) (rate(tcp_server_valkey_duration_seconds_bucket[1m])))",
    "server_visibility_exchange_seconds_by_pod": "tcp_server_visibility_exchange_duration_seconds",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run(command: list[str], *, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                            check=False, env=env)
    if check and result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or
                           f"{' '.join(command)} failed")
    return result


def kubectl_json(*arguments: str) -> dict[str, Any]:
    return json.loads(run(["kubectl", *arguments, "-o", "json"]).stdout)


def wait_http(url: str, process: subprocess.Popen[str] | None = None,
              timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"process exited while waiting for {url}")
        try:
            with urlopen(url, timeout=2) as response:  # nosec: local development endpoint
                if response.status < 500:
                    return
        except OSError:
            time.sleep(.25)
    raise TimeoutError(f"timed out waiting for {url}")


def start_process(command: list[str], log: Path) -> tuple[subprocess.Popen[str], Any]:
    handle = log.open("w", encoding="utf-8")
    process = subprocess.Popen(command, cwd=ROOT, stdout=handle,
                               stderr=subprocess.STDOUT, text=True)
    return process, handle


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def gateway_address(environment: str) -> tuple[str, int]:
    if environment == "kind":
        return "127.0.0.1", 9000
    service = kubectl_json("get", "service", "gateway", "-n", "tcp-lab")
    ingress = service.get("status", {}).get("loadBalancer", {}).get("ingress", [])
    if not ingress:
        raise RuntimeError("gateway LoadBalancer has no address")
    return ingress[0].get("hostname") or ingress[0]["ip"], 9000


def detect_environment(arguments: list[str]) -> str:
    if "--environment" in arguments:
        requested = arguments[arguments.index("--environment") + 1]
        if requested != "auto":
            return requested
    context = run(["kubectl", "config", "current-context"]).stdout.strip()
    return "kind" if context.startswith("kind-") else "eks"


def mirror_output(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")[:100] or "event"


def phase_banner(page: Any, title: str, detail: str) -> None:
    script = """([title, detail]) => {
      let box = document.getElementById('tcp-lab-evidence-phase');
      if (!box) {
        box = document.createElement('div'); box.id = 'tcp-lab-evidence-phase';
        Object.assign(box.style, {position:'fixed',top:'12px',left:'50%',transform:'translateX(-50%)',
          zIndex:'2147483647',background:'#0d1117ee',color:'#e6edf3',border:'1px solid #58a6ff',
          borderRadius:'7px',padding:'8px 14px',font:'13px ui-monospace,monospace',boxShadow:'0 4px 18px #000a'});
        document.documentElement.appendChild(box);
      }
      box.textContent = title + (detail ? ' · ' + detail : '');
    }"""
    try:
        page.evaluate(script, [title, detail])
    except Exception:
        return


def capture(pages: dict[str, Any], directory: Path, sequence: int,
            event: dict[str, Any], seen: set[tuple[str, str]]) -> int:
    event_type = str(event.get("type", "event"))
    run_id = str(event.get("run_id", "run"))
    identity = (run_id, event_type)
    if event_type not in MILESTONES or identity in seen:
        return sequence
    seen.add(identity)
    sequence += 1
    destination = directory / f"{sequence:03d}-{safe_name(run_id)}-{safe_name(event_type)}"
    destination.mkdir(parents=True, exist_ok=True)
    detail = ", ".join(f"{key}={value}" for key, value in event.items()
                       if key not in {"at", "type", "run_id", "mode", "environment"})[:180]
    for name, page in pages.items():
        phase_banner(page, event_type.replace("_", " "), detail)
        try:
            page.screenshot(path=str(destination / f"{name}.png"), full_page=False)
        except Exception as error:
            (destination / f"{name}-error.txt").write_text(str(error), encoding="utf-8")
    (destination / "event.json").write_text(json.dumps(event, indent=2), encoding="utf-8")
    return sequence


def scroll_tour(page: Any, pause: float = .6) -> None:
    try:
        height = int(page.evaluate(
            "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"))
        viewport = int(page.evaluate("window.innerHeight"))
        for top in range(0, max(1, height), max(300, int(viewport * .72))):
            page.evaluate("top => window.scrollTo({top, behavior: 'smooth'})", top)
            page.wait_for_timeout(int(pause * 1000))
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(700)
    except Exception:
        return


def record_tour(browser: Any, name: str, url: str, raw_video: Path,
                videos: Path, *, scroll: bool = False) -> None:
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        record_video_dir=str(raw_video),
        record_video_size={"width": 1920, "height": 1080})
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2500)
    phase_banner(page, f"{name.replace('-', ' ')} tour", "recorded after the benchmark")
    if scroll:
        scroll_tour(page, .8)
    else:
        page.wait_for_timeout(4000)
    video = page.video
    context.close()
    if video:
        shutil.move(str(video.path()), videos / f"{name}.webm")


def prometheus_query_range(query: str, start: float, end: float,
                           step: int) -> list[dict[str, Any]]:
    path = "/api/v1/namespaces/tcp-lab/services/http:prometheus:9090/proxy/api/v1/query_range?" + urlencode({
        "query": query, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": str(step),
    })
    result = run(["kubectl", "get", "--raw", path], check=False)
    if result.returncode:
        return [{"error": (result.stderr or result.stdout).strip()}]
    payload = json.loads(result.stdout)
    return payload.get("data", {}).get("result", [])


def export_metrics(directory: Path, start: float, end: float, step: int) -> None:
    metrics = directory / "metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    collected: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for name, expression in PROMETHEUS_QUERIES.items():
        series = prometheus_query_range(expression, start, end, step)
        collected[name] = {"query": expression, "series": series}
        for item in series:
            if "error" in item:
                rows.append({"metric": name, "timestamp": "", "value": "",
                             "labels": "", "error": item["error"]})
                continue
            labels = json.dumps(item.get("metric", {}), sort_keys=True,
                                separators=(",", ":"))
            for timestamp, value in item.get("values", []):
                rows.append({"metric": name, "timestamp": timestamp, "value": value,
                             "labels": labels, "error": ""})
    (metrics / "prometheus.json").write_text(json.dumps(collected, indent=2), encoding="utf-8")
    with (metrics / "prometheus.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=["metric", "timestamp", "value", "labels", "error"])
        writer.writeheader()
        writer.writerows(rows)


def sample_client(url: str) -> dict[str, Any]:
    observed = utc_now()
    try:
        with urlopen(url + "api/state", timeout=2) as response:  # nosec: local client endpoint
            state = json.load(response)
        route = state.get("route") or {}
        return {
            "at": observed, "connection": state.get("connection"),
            "latency_ms": state.get("latency_ms"), "gateway": state.get("gateway"),
            "server": route.get("server"), "server_pod": route.get("server_pod"),
            "error": "",
        }
    except Exception as error:
        return {"at": observed, "connection": "unreachable", "latency_ms": "",
                "gateway": "", "server": "", "server_pod": "", "error": str(error)}


def export_client_samples(directory: Path, samples: list[dict[str, Any]]) -> None:
    metrics = directory / "metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    with (metrics / "client-path.csv").open("w", newline="", encoding="utf-8") as output:
        fields = ["at", "connection", "latency_ms", "gateway", "server", "server_pod", "error"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(samples)


def snapshot_environment(directory: Path, name: str) -> None:
    destination = directory / "environment"
    destination.mkdir(parents=True, exist_ok=True)
    resources: dict[str, Any] = {"captured_at": utc_now()}
    for key, arguments in {
        "nodes": ["get", "nodes", "-o", "json"],
        "pods": ["get", "pods", "-A", "-o", "json"],
        "deployments": ["get", "deployments", "-A", "-o", "json"],
        "autoscalers": ["get", "hpa", "-A", "-o", "json"],
        "services": ["get", "services", "-n", "tcp-lab", "-o", "json"],
    }.items():
        result = run(["kubectl", *arguments], check=False)
        if result.returncode:
            resources[key] = {"error": (result.stderr or result.stdout).strip()}
        else:
            resources[key] = json.loads(result.stdout)
    (destination / f"{name}.json").write_text(json.dumps(resources, indent=2),
                                               encoding="utf-8")


def write_index(directory: Path, metadata: dict[str, Any]) -> None:
    screenshots = sorted((directory / "screenshots").glob("*/*.png"))
    screenshots += sorted((directory / "screenshots").glob("*.png"))
    videos = sorted((directory / "video").glob("*.webm"))
    cards = "".join(
        f'<a href="{html.escape(path.relative_to(directory).as_posix())}">'
        f'<img src="{html.escape(path.relative_to(directory).as_posix())}">'
        f'<span>{html.escape(path.parent.name)} · {html.escape(path.stem)}</span></a>'
        for path in screenshots)
    clips = "".join(
        f'<section><h2>{html.escape(path.stem)}</h2><video controls preload="metadata" '
        f'src="{html.escape(path.relative_to(directory).as_posix())}"></video></section>'
        for path in videos)
    (directory / "index.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>TCP lab benchmark evidence</title><style>:root{{color-scheme:dark;font:14px system-ui}}body{{margin:2rem;background:#0d1117;color:#e6edf3}}.meta{{white-space:pre-wrap;background:#161b22;padding:1rem;border:1px solid #30363d}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem}}a,section{{color:inherit;text-decoration:none;background:#161b22;border:1px solid #30363d;padding:.7rem}}img,video{{display:block;width:100%;background:#000}}span{{display:block;margin-top:.5rem}}</style></head><body><h1>TCP lab benchmark evidence</h1><div class="meta">{html.escape(json.dumps(metadata, indent=2))}</div><h1>Recordings</h1><div class="grid">{clips}</div><h1>Milestone screenshots</h1><div class="grid">{cards}</div></body></html>""", encoding="utf-8")


def write_checksums(directory: Path) -> None:
    lines = []
    files = (item for item in directory.rglob("*")
             if item.is_file() and item.name != "checksums.sha256")
    for path in sorted(files):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(directory).as_posix()}")
    (directory / "checksums.sha256").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")


def parse_arguments() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grafana-port", type=int, default=3000)
    parser.add_argument("--dashboard-port", type=int, default=8080)
    parser.add_argument("--client-port", type=int, default=8081)
    parser.add_argument("--metrics-step", type=int, default=2)
    parser.add_argument("--headed", action="store_true",
                        help="show the automated Chromium window")
    parser.add_argument("--self-test", action="store_true",
                        help="verify screenshots and WebM recording without a cluster")
    parser.add_argument("--skip-local-services", action="store_true",
                        help="use views already listening on ports 3000, 8080, and 8081")
    parser.add_argument("benchmark_arguments", nargs=argparse.REMAINDER,
                        help="arguments after -- are passed to scale_benchmark.py run")
    args = parser.parse_args()
    forwarded = args.benchmark_arguments
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    if not forwarded:
        forwarded = ["--environment", "auto", "--mode", "full", "--application"]
    return args, forwarded


def self_test(sync_playwright: Any, headed: bool) -> Path:
    session_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-evidence-self-test"
    directory = OUTPUT / session_id
    screenshots, raw_video, videos = (directory / "screenshots",
                                      directory / "video-raw", directory / "video")
    for path in (screenshots, raw_video, videos):
        path.mkdir(parents=True, exist_ok=True)
    markup = """<!doctype html><html><style>:root{color-scheme:dark;font:18px system-ui}body{margin:4rem;background:#0d1117;color:#e6edf3}section{height:700px;padding:2rem;border:1px solid #30363d;background:#161b22;margin:1rem}b{color:#58a6ff}</style><body><section><h1>TCP lab evidence self-test</h1><p>Screenshot and WebM capture are working.</p></section><section><h1>Automated tour</h1><p>This second panel verifies scrolling.</p></section></body></html>"""
    url = "data:text/html;charset=utf-8," + quote(markup)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        page.goto(url, wait_until="load")
        page.screenshot(path=str(screenshots / "self-test.png"), full_page=True)
        context.close()
        record_tour(browser, "self-test", url, raw_video, videos, scroll=True)
        browser.close()
    metadata = {"session_id": session_id, "type": "self-test", "created_at": utc_now()}
    (directory / "manifest.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    write_index(directory, metadata)
    write_checksums(directory)
    return directory


def main() -> int:
    args, benchmark_arguments = parse_arguments()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "Playwright is optional. Install it with: python -m pip install "
            "-r tools/requirements-evidence.txt; python -m playwright install chromium") from error

    if args.self_test:
        directory = self_test(sync_playwright, args.headed)
        print(f"Evidence self-test passed: {directory}\nOpen: {directory / 'index.html'}")
        return 0

    environment = detect_environment(benchmark_arguments)
    session_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-evidence-{environment}"
    directory = OUTPUT / session_id
    screenshots = directory / "screenshots"
    raw_video = directory / "video-raw"
    videos = directory / "video"
    logs = directory / "logs"
    for path in (screenshots, raw_video, videos, logs):
        path.mkdir(parents=True, exist_ok=True)
    event_stream = directory / "events.jsonl"
    event_stream.touch()
    started_epoch = time.time()
    started_at = utc_now()
    gateway_host, gateway_port = gateway_address(environment)
    processes: list[tuple[subprocess.Popen[str], Any]] = []
    grafana_process: tuple[subprocess.Popen[str], Any] | None = None
    benchmark: subprocess.Popen[str] | None = None
    result = 1
    metadata: dict[str, Any] = {
        "session_id": session_id, "environment": environment,
        "started_at": started_at, "benchmark_arguments": benchmark_arguments,
        "gateway": f"{gateway_host}:{gateway_port}",
    }
    commit = run(["git", "rev-parse", "HEAD"], check=False).stdout.strip()
    status = run(["git", "status", "--porcelain"], check=False).stdout.strip()
    metadata.update({"repository_commit": commit, "repository_dirty": bool(status)})
    try:
        snapshot_environment(directory, "before")
        if not args.skip_local_services:
            grafana_process = start_process(
                ["kubectl", "port-forward", "-n", "tcp-lab", "service/grafana",
                 f"{args.grafana_port}:3000"], logs / "grafana-port-forward.log")
            processes.append(grafana_process)
            wait_http(f"http://127.0.0.1:{args.grafana_port}/api/health", grafana_process[0])
            processes.append(start_process(
                [sys.executable, "tools/dashboard.py", "--port", str(args.dashboard_port),
                 "--gateway-host", gateway_host, "--gateway-port", str(gateway_port)],
                logs / "dashboard.log"))
            wait_http(f"http://127.0.0.1:{args.dashboard_port}/", processes[-1][0])
            processes.append(start_process(
                [sys.executable, "tools/client.py", "--listen-port", str(args.client_port),
                 "--gateway-host", gateway_host, "--gateway-port", str(gateway_port),
                 "--no-browser"], logs / "client.log"))
            wait_http(f"http://127.0.0.1:{args.client_port}/", processes[-1][0])

        urls = {
            "grafana": f"http://127.0.0.1:{args.grafana_port}/d/tcp-lab-live/"
                       "tcp-lab-live-pressure?orgId=1&refresh=5s&kiosk",
            "tcp-map": f"http://127.0.0.1:{args.dashboard_port}/",
            "client": f"http://127.0.0.1:{args.client_port}/",
        }
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            contexts: dict[str, Any] = {}
            pages: dict[str, Any] = {}
            for name, url in urls.items():
                context = browser.new_context(viewport={"width": 1920, "height": 1080})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(2500)
                contexts[name], pages[name] = context, page

            sequence = capture(pages, screenshots, 0,
                               {"type": "baseline", "run_id": session_id}, set())
            environment_vars = os.environ.copy()
            environment_vars["TCP_LAB_BENCHMARK_EVENT_STREAM"] = str(event_stream)
            environment_vars["TCP_LAB_BENCHMARK_EVIDENCE_SESSION"] = session_id
            benchmark = subprocess.Popen(
                [sys.executable, "tools/scale_benchmark.py", "run", *benchmark_arguments],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=environment_vars, bufsize=1)
            threading.Thread(target=mirror_output, args=(benchmark,), daemon=True).start()
            position = 0
            seen: set[tuple[str, str]] = set()
            client_samples: list[dict[str, Any]] = []
            next_client_sample = 0.0
            while benchmark.poll() is None:
                with event_stream.open("r", encoding="utf-8") as events:
                    events.seek(position)
                    for line in events:
                        if line.strip():
                            sequence = capture(pages, screenshots, sequence,
                                               json.loads(line), seen)
                    position = events.tell()
                if time.monotonic() >= next_client_sample:
                    client_samples.append(sample_client(urls["client"]))
                    next_client_sample = time.monotonic() + 1.0
                pages["grafana"].wait_for_timeout(250)
            with event_stream.open("r", encoding="utf-8") as events:
                events.seek(position)
                for line in events:
                    if line.strip():
                        sequence = capture(pages, screenshots, sequence,
                                           json.loads(line), seen)
            result = int(benchmark.returncode or 0)
            finished_epoch = time.time()

            snapshot_environment(directory, "after")
            export_metrics(directory, started_epoch, finished_epoch,
                           max(1, args.metrics_step))
            export_client_samples(directory, client_samples)
            report_directory = directory / "report"
            run([sys.executable, "tools/scale_benchmark.py", "report",
                 "--session", session_id, "--output-dir", str(report_directory)], check=False)
            report = report_directory / "report.html"
            if report.exists():
                context = browser.new_context(viewport={"width": 1920, "height": 1080})
                page = context.new_page()
                page.goto(report.resolve().as_uri(), wait_until="load")
                pages["report"], contexts["report"] = page, context
                page.screenshot(path=str(screenshots / "report-full.png"), full_page=True)

            from_ms, to_ms = int(started_epoch * 1000), int(finished_epoch * 1000)
            if not args.skip_local_services:
                health_url = f"http://127.0.0.1:{args.grafana_port}/api/health"
                try:
                    wait_http(health_url, timeout=15)
                except (OSError, RuntimeError, TimeoutError):
                    if grafana_process is not None:
                        stop_process(grafana_process[0])
                        grafana_process[1].close()
                    grafana_process = start_process(
                        ["kubectl", "port-forward", "-n", "tcp-lab", "service/grafana",
                         f"{args.grafana_port}:3000"], logs / "grafana-port-forward-restarted.log")
                    processes.append(grafana_process)
                    wait_http(health_url, grafana_process[0], timeout=45)
            pages["grafana"].goto(urls["grafana"] + f"&from={from_ms}&to={to_ms}",
                                  wait_until="domcontentloaded", timeout=60_000)
            pages["grafana"].wait_for_timeout(2500)
            pages["grafana"].screenshot(path=str(screenshots / "grafana-full.png"),
                                         full_page=True)
            pages["tcp-map"].screenshot(path=str(screenshots / "tcp-map-final.png"))
            pages["client"].screenshot(path=str(screenshots / "client-final.png"))
            for context in contexts.values():
                context.close()
            record_tour(browser, "grafana", urls["grafana"] +
                        f"&from={from_ms}&to={to_ms}", raw_video, videos, scroll=True)
            record_tour(browser, "tcp-map", urls["tcp-map"], raw_video, videos)
            record_tour(browser, "client", urls["client"], raw_video, videos)
            if report.exists():
                record_tour(browser, "report", report.resolve().as_uri(),
                            raw_video, videos, scroll=True)
            browser.close()

        metadata.update({
            "finished_at": utc_now(), "benchmark_exit_code": result,
            "duration_seconds": round(time.time() - started_epoch, 3), "urls": urls,
        })
        (directory / "manifest.json").write_text(json.dumps(metadata, indent=2),
                                                 encoding="utf-8")
        write_index(directory, metadata)
        write_checksums(directory)
        print(f"Evidence: {directory}\nOpen: {directory / 'index.html'}")
        return result
    finally:
        if benchmark is not None and benchmark.poll() is None:
            stop_process(benchmark)
        for process, handle in reversed(processes):
            stop_process(process)
            handle.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Recording interrupted; benchmark resources may require cleanup.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"Evidence recording failed: {error}", file=sys.stderr)
        raise SystemExit(1)
