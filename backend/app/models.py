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
class GameEvent(BaseModel):
    timestamp_seconds: float
    event_type: str  # e.g. "smoke", "molotov", "death", "rotation", "bomb_plant"
    actor: str  # "enemy", "player", "teammate", etc.
    target: Optional[str] = None
    location: Optional[str] = None  # raw CS2 callout (last_place_name)
    # Canonical map zone resolved deterministically from coordinates/callout
    # (see app/map_zones.py). The coach may only refer to these labels.
    zone: Optional[str] = None
    description: str = ""


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


class RoundFacts(BaseModel):
    round_id: str
    round_number: int
    map: str
    side: str  # "T" or "CT"
    player_team: str
    round_winner: str  # "T" or "CT"
    bombsite: Optional[str] = None
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
