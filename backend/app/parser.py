"""Demo parsing interface + fixture/fallback implementations.

We define a small `DemoParser` protocol with these concrete paths:

  1. SampleFixtureParser   -> loads the bundled Mirage fixture.
  2. JsonUploadParser      -> treats an uploaded .json as already-parsed data.
  3. MockDemParser         -> fallback for uploaded .dem files when the real
                              parser is disabled (USE_MOCK_DEM_PARSER=1) or the
                              native wheel is unavailable.

Real `.dem` files are decoded for real by `RealDemParser` in
`app/real_parser.py` (backed by `demoparser2`), which produces the SAME
`ParsedDemo` shape so the detectors/coach run unchanged. `main.py` routes
.dem uploads to it by default.
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
    """Fallback parser for uploaded .dem files (real parser disabled).

    Used only when USE_MOCK_DEM_PARSER=1 or `demoparser2` can't be installed.
    It does not decode the binary demo: it returns the sample fixture so the
    pipeline still runs, clearly marked `parser_mode="mock_dem_parser"` with the
    demo_id seeded from the filename. The real path is `real_parser.RealDemParser`.
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
