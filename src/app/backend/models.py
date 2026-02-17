from typing import List

from pydantic import BaseModel


class MatchRequest(BaseModel):
    player_id: str


class MatchResponse(BaseModel):
    session_id: str
    players: List[str]
    connect_host: str = ""
    connect_port: int = 0
