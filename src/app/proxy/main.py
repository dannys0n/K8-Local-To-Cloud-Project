"""Game proxy: single entry point for clients."""
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from backend_client import forward_json, health_backend, shutdown_client, startup_client


app = FastAPI(title="Game Proxy", version="0.1.0")


@app.on_event("startup")
async def startup() -> None:
    await startup_client()


@app.on_event("shutdown")
async def shutdown() -> None:
    await shutdown_client()


@app.get("/health")
async def health() -> dict:
    return await health_backend()


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
    return await forward_json(
        "POST",
        "/match/join",
        content=body,
        headers={"content-type": content_type},
        timeout=10.0,
    )


@app.post("/api/match/{session_id}/end")
async def proxy_end(session_id: str, request: Request):
    """Forward /match/{session_id}/end to backend."""
    return await forward_json(
        "POST",
        f"/match/{session_id}/end",
        timeout=15.0,
    )


@app.get("/api/match/status")
async def proxy_match_status(player_id: str):
    """Forward status so queued clients can poll until matched and get game server address."""
    return await forward_json(
        "GET",
        "/match/status",
        params={"player_id": player_id},
        timeout=5.0,
    )


@app.get("/api/sessions/active")
async def proxy_active_sessions():
    """Forward /sessions/active to backend."""
    return await forward_json("GET", "/sessions/active", timeout=5.0)

