import os
import time
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="Game Server", version="0.1.0")

# In-memory match state (authoritative for this match instance)
_match_state = {
    "session_id": os.getenv("SESSION_ID", "unknown"),
    "players": [],
    "started_at": None,
    "ended_at": None,
    "status": "pending",
}


class MatchStatus(BaseModel):
    session_id: str
    players: list[str]
    status: str
    started_at: str | None
    ended_at: str | None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "session_id": _match_state["session_id"]}


@app.get("/status", response_model=MatchStatus)
def get_status() -> MatchStatus:
    """Get current match status."""
    return MatchStatus(
        session_id=_match_state["session_id"],
        players=_match_state["players"],
        status=_match_state["status"],
        started_at=_match_state["started_at"].isoformat() if _match_state["started_at"] else None,
        ended_at=_match_state["ended_at"].isoformat() if _match_state["ended_at"] else None,
    )


@app.post("/start")
def start_match(players: list[str]) -> dict:
    """Initialize match with players (called by backend when pod starts)."""
    _match_state["players"] = players
    _match_state["started_at"] = datetime.utcnow()
    _match_state["status"] = "active"
    return {"status": "started", "session_id": _match_state["session_id"]}


@app.post("/end")
def end_match() -> dict:
    """End the match (called by clients or backend)."""
    _match_state["ended_at"] = datetime.utcnow()
    _match_state["status"] = "ended"
    return {"status": "ended", "session_id": _match_state["session_id"]}
