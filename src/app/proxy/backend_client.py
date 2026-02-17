import os

import httpx
from fastapi import HTTPException


BACKEND_URL = os.getenv("BACKEND_URL", "http://game-backend:8080")
_client: httpx.AsyncClient | None = None


async def startup_client() -> None:
    global _client
    limits = httpx.Limits(max_keepalive_connections=100, max_connections=200)
    _client = httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=30.0,
        limits=limits,
    )


async def shutdown_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def health_backend() -> dict:
    if _client is None:
        return {"status": "degraded", "reason": "client_not_initialized"}
    try:
        resp = await _client.get("/health", timeout=2.0)
        if resp.status_code == 200:
            return {"status": "ok"}
        return {"status": "degraded", "reason": f"backend_status_{resp.status_code}"}
    except Exception:  # noqa: BLE001
        return {"status": "degraded", "reason": "backend_unreachable"}


async def forward_json(
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
