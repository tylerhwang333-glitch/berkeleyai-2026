"""Pydantic data models for the CS2 Decision Coach.

These models describe the full pipeline:
    parsed demo -> round facts -> detected decision moments -> coach report.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class AnalyzeSampleRequest(BaseModel):
    player_id: str = "local_user"


# ---------------------------------------------------------------------------
# Parsed demo models
# ---------------------------------------------------------------------------
class EntityPosition(BaseModel):
    """One player or piece of utility on the map at a single tick.

    ``x/y/z`` are raw world coordinates; ``nx/ny`` are the same point scaled to
    a 0..1 fraction of the map's radar image (see app/map_zones.world_to_normalized).
    The frontend multiplies nx/ny by the displayed image size, so it never needs
    to know any per-map calibration -- that lives entirely on the backend.
    """
    kind: str  # "player" | "util"
    label: str  # player display name, or utility type ("smoke", "molotov", ...)
    team: Optional[str] = None  # "T" | "CT" | None
    alive: Optional[bool] = None  # players only
    is_analyzed_player: bool = False  # the coached player
    util_type: Optional[str] = None  # utils only ("smoke", "molotov", "he", "flash")
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    nx: Optional[float] = None  # 0..1 fraction of radar width (None if uncalibrated)
    ny: Optional[float] = None  # 0..1 fraction of radar height


class EventSnapshot(BaseModel):
    """Positions of every player and every active util at an event's tick."""
    players: List[EntityPosition] = Field(default_factory=list)
    utils: List[EntityPosition] = Field(default_factory=list)


class GameEvent(BaseModel):
    timestamp_seconds: float
    tick: Optional[int] = None  # demo tick this event fired on (real .dem only)
    event_type: str  # e.g. "smoke", "molotov", "death", "rotation", "bomb_plant"
    actor: str  # "enemy", "player", "teammate", etc.
    target: Optional[str] = None
    location: Optional[str] = None  # raw CS2 callout (last_place_name)
    # Canonical map zone resolved deterministically from coordinates/callout
    # (see app/map_zones.py). The coach may only refer to these labels.
    zone: Optional[str] = None
    description: str = ""
    # Map state at this event's tick: every player + every active util, with
    # both world and radar-normalized coordinates. Populated by the real .dem
    # parser; None for parser modes without positional data (fixture/json).
    snapshot: Optional[EventSnapshot] = None


class PlayerRoundSummary(BaseModel):
    survived: bool
    death_time_seconds: Optional[float] = None
    death_location: Optional[str] = None  # raw CS2 callout
    death_zone: Optional[str] = None  # canonical zone (app/map_zones.py)
    primary_zone: Optional[str] = None  # canonical zone the player spent the round in
    nearest_teammate_distance_on_death: Optional[float] = None
    had_flash_support_before_death: bool = False
    utility_unused_on_death: List[str] = Field(default_factory=list)
    primary_area: str = ""
    rotated_from: Optional[str] = None
    rotated_to: Optional[str] = None
    rotation_time_seconds: Optional[float] = None
    waited_after_enemy_utility_seconds: Optional[float] = None
    alternate_map_control_taken: bool = False


class BombState(BaseModel):
    """What happened to the bomb this round, for map display."""
    status: str  # "planted" | "dropped" | "not_planted"
    site: Optional[str] = None  # canonical zone / "A" / "B" where it was planted
    tick: Optional[int] = None  # plant tick (planted only)
    nx: Optional[float] = None  # plant location on the radar (0..1), planted only
    ny: Optional[float] = None


class RoundFacts(BaseModel):
    round_id: str
    round_number: int
    map: str
    side: str  # "T" or "CT"
    player_team: str
    round_winner: str  # "T" or "CT"
    bombsite: Optional[str] = None
    bomb: Optional[BombState] = None
    events: List[GameEvent] = Field(default_factory=list)
    player_summary: PlayerRoundSummary


class ParsedDemo(BaseModel):
    demo_id: str
    parser_mode: str  # "sample_fixture" | "json_upload" | "mock_dem_parser" | "real_dem_parser"
    map: str
    player_id: str
    analyzed_player: Optional[str] = None  # in-demo name of the coached player (real .dem)
    rounds: List[RoundFacts] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Analysis output models
# ---------------------------------------------------------------------------
class DecisionMoment(BaseModel):
    moment_id: str
    player_id: str
    demo_id: str
    round_id: str
    map: str
    side: str
    zone: Optional[str] = None  # canonical map zone for this moment (or "Unknown")
    timestamp_seconds: float
    enemy_action: str
    user_response: str
    outcome: str
    mistake_type: str
    evidence: List[str] = Field(default_factory=list)
    recommended_response: str
    confidence: float
    summary_text: str


class SimilarMemoryItem(BaseModel):
    moment_id: str
    mistake_type: str
    summary_text: str
    similarity: float
    map: str


class MapRadar(BaseModel):
    """Per-map radar image + the world->image calibration it was authored with.

    The backend already scales positions into ``nx/ny`` (0..1), so the frontend
    only needs ``image_url``. The calibration fields are included so a client
    could re-derive pixels itself if it ever wanted to. Extendable: register a
    new map's image + calibration in app/map_data/<map>.py.
    """
    map: str
    image_url: str  # served by the backend (see /assets mount)
    pos_x: float
    pos_y: float
    scale: float
    size: float  # radar resolution (px) the calibration was authored against


class RoundView(BaseModel):
    """Lightweight per-round view sent to the client for map visualization."""
    round_id: str
    round_number: int
    side: str
    round_winner: str
    bombsite: Optional[str] = None
    bomb: Optional[BombState] = None
    events: List[GameEvent] = Field(default_factory=list)


class CoachReport(BaseModel):
    report_id: str
    player_id: str
    demo_id: str
    parser_mode: str
    map: str
    analyzed_player: Optional[str] = None
    moments: List[DecisionMoment] = Field(default_factory=list)
    similar_memory: List[SimilarMemoryItem] = Field(default_factory=list)
    final_coaching_summary: str = ""
    drills: List[str] = Field(default_factory=list)
    # Visualization payload (not persisted to Redis): the radar image descriptor
    # plus every round's events, each carrying a position snapshot at its tick.
    map_radar: Optional[MapRadar] = None
    rounds: List[RoundView] = Field(default_factory=list)
