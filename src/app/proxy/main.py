"""
Game proxy: single entry point for clients.

Flow:
- Clients hit proxy to join matchmaking (queued at backend).
- Backend allocates a game server when 12 are ready and notifies via response
  (join returns connect_host/port for the 12th) or status (queued clients poll
  GET /api/match/status?player_id= until status=matched and get address).
- Proxy forwards all requests to backend; proxy tells grouped clients the
  game server address (in join response or via status poll).
"""
import os

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse


BACKEND_URL = os.getenv("BACKEND_URL", "http://game-backend:8080")

app = FastAPI(title="Game Proxy", version="0.1.0")

_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def startup() -> None:
    global _client
    # Increase connection pool and timeout for high load
    limits = httpx.Limits(max_keepalive_connections=100, max_connections=200)
    _client = httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=30.0,  # Longer timeout for pod creation
        limits=limits,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _forward_json(
    method: str,
    path: str,
    *,
    content: bytes | None = None,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 10.0,
):
    if _client is None:
        raise HTTPException(status_code=503, detail="Proxy not ready")
    try:
        if method == "GET":
            resp = await _client.get(path, params=params, timeout=timeout, headers=headers)
        elif method == "POST":
            resp = await _client.post(path, content=content, timeout=timeout, headers=headers)
        else:
            raise HTTPException(status_code=500, detail=f"Unsupported method: {method}")
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Backend timeout")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Backend connection error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")


@app.get("/health")
async def health() -> dict:
    """Health check - also checks backend connectivity."""
    if _client is None:
        return {"status": "degraded", "reason": "client_not_initialized"}
    
    try:
        # Quick check if backend is reachable
        resp = await _client.get("/health", timeout=2.0)
        if resp.status_code == 200:
            return {"status": "ok"}
        return {"status": "degraded", "reason": f"backend_status_{resp.status_code}"}
    except Exception:  # noqa: BLE001
        return {"status": "degraded", "reason": "backend_unreachable"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all exceptions to prevent proxy crashes."""
    import traceback
    print(f"Unhandled exception in proxy: {exc}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal proxy error", "detail": str(exc)},
    )


@app.post("/api/match/join")
async def proxy_join(request: Request):
    """
    Thin HTTP proxy in front of the backend.
    Forwards JSON body to /match/join on the backend service.
    """
    body = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    return await _forward_json(
        "POST",
        "/match/join",
        content=body,
        headers={"content-type": content_type},
        timeout=10.0,
    )


@app.post("/api/match/{session_id}/end")
async def proxy_end(session_id: str, request: Request):
    """Forward /match/{session_id}/end to backend."""
    return await _forward_json(
        "POST",
        f"/match/{session_id}/end",
        timeout=15.0,
    )


@app.get("/api/match/status")
async def proxy_match_status(player_id: str):
    """Forward status so queued clients can poll until matched and get game server address."""
    return await _forward_json(
        "GET",
        "/match/status",
        params={"player_id": player_id},
        timeout=5.0,
    )


@app.get("/api/sessions/active")
async def proxy_active_sessions():
    """Forward /sessions/active to backend."""
    return await _forward_json("GET", "/sessions/active", timeout=5.0)

