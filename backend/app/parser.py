"""Demo parsing interface + fixture/mock implementations.

The whole MVP is designed to run WITHOUT a real CS2 demo parser installed.
We define a small `DemoParser` protocol and provide three concrete paths:

  1. SampleFixtureParser   -> loads the bundled Mirage fixture.
  2. JsonUploadParser      -> treats an uploaded .json as already-parsed data.
  3. MockDemParser         -> for uploaded .dem files, returns the sample
                              fixture but clearly marks parser_mode and seeds
                              the demo_id from the filename.

TODO(real-parser): Replace MockDemParser with a real parser using either
  `demoparser2` (https://github.com/LaihoE/demoparser) or `awpy`
  (https://github.com/pnxenopoulos/awpy). Implement a `RealDemParser` that:
    - parses ticks/events from the .dem file,
    - reconstructs per-round GameEvent lists,
    - computes the PlayerRoundSummary fields (death info, distances,
      utility usage, rotations, wait times).
  Keep the ParsedDemo output shape identical so detectors/coach are unchanged.
"""
from __future__ import annotations

import json
from typing import Protocol

from .models import ParsedDemo
from .sample_data import load_sample_parsed_demo


class DemoParser(Protocol):
    def parse(self) -> ParsedDemo:  # pragma: no cover - interface
        ...


class SampleFixtureParser:
    """Loads the bundled sample parsed demo."""

    def __init__(self, player_id: str = "local_user"):
        self.player_id = player_id

    def parse(self) -> ParsedDemo:
        demo = load_sample_parsed_demo(self.player_id)
        demo.parser_mode = "sample_fixture"
        return demo


class JsonUploadParser:
    """Parses an uploaded .json file as already-parsed fixture data."""

    def __init__(self, raw_bytes: bytes, player_id: str = "local_user", demo_id: str | None = None):
        self.raw_bytes = raw_bytes
        self.player_id = player_id
        self.demo_id = demo_id

    def parse(self) -> ParsedDemo:
        raw = json.loads(self.raw_bytes.decode("utf-8"))
        raw["player_id"] = self.player_id
        raw["parser_mode"] = "json_upload"
        if self.demo_id:
            raw["demo_id"] = self.demo_id
        return ParsedDemo(**raw)


class MockDemParser:
    """Mock parser for uploaded .dem files.

    We don't actually decode the binary demo here. We return the sample
    fixture data so the full pipeline still runs, but we clearly mark the
    parser_mode as "mock_dem_parser" and seed the demo_id from the filename.
    """

    def __init__(self, filename: str, player_id: str = "local_user"):
        self.filename = filename
        self.player_id = player_id

    def parse(self) -> ParsedDemo:
        demo = load_sample_parsed_demo(self.player_id)
        demo.parser_mode = "mock_dem_parser"
        # Seed a stable-ish demo id from the uploaded filename.
        safe_name = self.filename.rsplit("/", 1)[-1].replace(".dem", "")
        demo.demo_id = f"mock_{safe_name}" if safe_name else "mock_dem_demo"
        return demo
