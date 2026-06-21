"""Loads the bundled sample parsed demo fixture."""
from __future__ import annotations

import json
import os

from .models import ParsedDemo

SAMPLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "sample",
    "mirage_sample_parsed_demo.json",
)


def load_sample_parsed_demo(player_id: str = "local_user") -> ParsedDemo:
    """Load the Mirage sample fixture and override the player id."""
    with open(SAMPLE_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    raw["player_id"] = player_id
    return ParsedDemo(**raw)
