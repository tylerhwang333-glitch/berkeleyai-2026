"""Real CS2 `.dem` parser backed by `demoparser2`.

This decodes the actual binary demo (LaihoE/demoparser via the `demoparser2`
wheel) and reconstructs the SAME `ParsedDemo` shape the rest of the pipeline
already consumes (rounds -> events + PlayerRoundSummary). Because the output
shape is identical to the sample fixture, the detectors and coach run
completely unchanged on real demos.

What is grounded in real demo data:
  * rounds, sides (T/CT), winners            -> round_start / round_end events
  * the analyzed player's deaths             -> player_death events
  * death location + distance to teammates   -> per-tick X/Y/Z + last_place_name
  * enemy/teammate utility (smoke/molly/he/flash) with real map callouts
  * bomb plants                              -> bomb_planted events
  * flash support before death               -> player_blind (teammate blinds enemy)
  * unused utility on death                  -> inventory at the death tick

A few PlayerRoundSummary fields are inherently judgement calls (rotations,
"alternate map control", how long the player waited behind utility). Those are
derived heuristically from the player's REAL per-second zone timeline and are
clearly marked below. They feed the existing rule-based detectors; the detectors
decide whether a mistake actually fired.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Tuple

from .models import GameEvent, ParsedDemo, PlayerRoundSummary, RoundFacts

# CS2 competitive demos record 64 ticks/second. demoparser2 does not surface a
# tickrate, and 64 is correct for MM/FACEIT/Valve official demos.
TICK_RATE = 64.0

# Team numbers used throughout CS:GO/CS2 demos.
TEAM_T = 2
TEAM_CT = 3

# Map a demo grenade/utility source to the detector's utility token vocabulary
# (see detectors.BLOCKING_UTILITY / USABLE_UTILITY).
DETONATE_EVENTS = {
    "smokegrenade_detonate": "smoke",
    "inferno_startburn": "molotov",  # covers molotov + incendiary fire
    "hegrenade_detonate": "he",
    "flashbang_detonate": "flash",
}

# Names as they appear in a player's `inventory` tick property.
INVENTORY_UTILITY = {
    "Smoke Grenade": "smoke",
    "Flashbang": "flash",
    "High Explosive Grenade": "he",
    "Molotov": "molotov",
    "Incendiary Grenade": "incendiary",
    "Decoy Grenade": "decoy",
}

# Sample the per-second player timeline rather than every tick (keeps a 60MB
# demo fast). One sample/second is plenty for zone/rotation reasoning.
TIMELINE_STEP_TICKS = int(TICK_RATE)

# How recently a friendly flash must have blinded an enemy to count as "support".
FLASH_SUPPORT_WINDOW_S = 3.0


class DemoParseError(Exception):
    """Raised when the .dem cannot be decoded into a usable ParsedDemo."""


def _to_region(place: Optional[str]) -> Optional[str]:
    """Collapse a CS2 callout (``last_place_name``) into a coarse site region.

    Returns 'A', 'B', 'MID' or None. Generic across maps: it keys off the
    universal ``BombsiteA``/``BombsiteB`` callouts first, then a handful of
    common A-side / B-side / mid callout substrings.
    """
    if not place:
        return None
    p = place.lower()
    if "bombsitea" in p:
        return "A"
    if "bombsiteb" in p:
        return "B"
    a_side = ("palace", "ramp", "stairs", "tetris", "firingrange", "ticket")
    b_side = ("apartment", "apps", "market", "shortb", "van", "bench", "construction")
    mid = ("middle", "connector", "catwalk", "topofmid", "underpass", "sidealley", "tunnel")
    if any(s in p for s in a_side):
        return "A"
    if any(s in p for s in b_side):
        return "B"
    if any(s in p for s in mid):
        return "MID"
    return None


def _f(val, default: float = 0.0) -> float:
    """Coerce a possibly-NaN/None demo value to float."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def _i(val, default: int = 0) -> int:
    """Coerce a possibly-NaN/None demo value to int."""
    return int(_f(val, default))


def _isnum(val) -> bool:
    """True if val is a real (non-NaN, non-None) number."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return False
    return not math.isnan(f)


def _dist(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


class RealDemParser:
    """Parse a real CS2 .dem file into a ParsedDemo for the analyzed player."""

    def __init__(
        self,
        demo_path: str,
        player_id: str = "local_user",
        player_name: Optional[str] = None,
        demo_id: Optional[str] = None,
    ):
        self.demo_path = demo_path
        self.player_id = player_id
        self.player_name = player_name
        self.demo_id = demo_id
        self.analyzed_player_name: Optional[str] = None

    # -- public ----------------------------------------------------------------
    def parse(self) -> ParsedDemo:
        try:
            from demoparser2 import DemoParser as _DP
        except Exception as exc:  # noqa: BLE001
            raise DemoParseError(
                "demoparser2 is not installed; cannot parse real .dem files."
            ) from exc

        try:
            parser = _DP(self.demo_path)
            header = parser.parse_header()
        except DemoParseError:
            raise
        except BaseException as exc:  # noqa: BLE001
            # demoparser2 is a Rust/pyo3 extension: invalid input raises a
            # PanicException, which subclasses BaseException (not Exception).
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise DemoParseError(
                f"Could not open demo (not a valid CS2 demo file?): {exc}"
            ) from exc

        map_name = header.get("map_name", "unknown")

        players = self._load_players(parser)
        if not players:
            raise DemoParseError("No players found in demo.")
        target_steamid = self._resolve_target(players)
        self.analyzed_player_name = players[target_steamid]

        rounds_meta = self._load_rounds(parser)
        if not rounds_meta:
            diag = self._round_diagnostics(parser)
            raise DemoParseError(
                f"No rounds found in demo (incomplete recording?). {diag}"
            )

        # Bulk-load the events once; slice per round below.
        deaths = self._event_df(parser, "player_death")
        blinds = self._event_df(parser, "player_blind")
        bombs = self._event_df(parser, "bomb_planted")
        detonations = self._load_detonations(parser)

        # Per-second timeline of every player's position + zone + alive state.
        timeline = self._load_timeline(parser, rounds_meta)
        # Exact-death-tick positions for precise distance-to-teammate.
        death_ticks = (
            [_i(t) for t in deaths["tick"].dropna().tolist()] if deaths is not None else []
        )
        death_positions = self._positions_at(parser, death_ticks)

        team_by_round = self._teams_per_round(timeline, target_steamid, rounds_meta)

        demo_id = self.demo_id or self._demo_id_from_path()
        rounds: List[RoundFacts] = []
        for idx, rm in enumerate(rounds_meta):
            rf = self._build_round(
                idx=idx,
                rm=rm,
                map_name=map_name,
                target=target_steamid,
                target_team=team_by_round.get(idx),
                deaths=deaths,
                blinds=blinds,
                bombs=bombs,
                detonations=detonations,
                timeline=timeline,
                death_positions=death_positions,
            )
            if rf is not None:
                rounds.append(rf)

        if not rounds:
            raise DemoParseError(
                "Parsed the demo but could not reconstruct any rounds for the "
                "selected player."
            )

        return ParsedDemo(
            demo_id=demo_id,
            parser_mode="real_dem_parser",
            map=map_name,
            player_id=self.player_id,
            analyzed_player=self.analyzed_player_name,
            rounds=rounds,
        )

    # -- loading helpers -------------------------------------------------------
    def _demo_id_from_path(self) -> str:
        base = self.demo_path.rsplit("/", 1)[-1].replace(".dem", "")
        return f"dem_{base}" if base else "real_dem_demo"

    def _load_players(self, parser) -> Dict[str, str]:
        """Map steamid -> display name for every player in the demo."""
        try:
            df = parser.parse_player_info()
        except Exception as exc:  # noqa: BLE001
            raise DemoParseError(f"Could not read player list: {exc}") from exc
        out: Dict[str, str] = {}
        for _, row in df.iterrows():
            sid = str(row.get("steamid"))
            name = str(row.get("name"))
            if sid and sid != "None":
                out[sid] = name
        return out

    def _resolve_target(self, players: Dict[str, str]) -> str:
        """Pick which player to coach.

        Priority: exact/loose name match on the requested player_name, else the
        player whose id was passed (if it is a steamid), else the first player.
        """
        if self.player_name:
            want = self.player_name.strip().lower()
            for sid, name in players.items():
                if name.strip().lower() == want:
                    return sid
            for sid, name in players.items():
                if want and want in name.strip().lower():
                    return sid
        if self.player_id in players:  # player_id might already be a steamid
            return self.player_id
        return next(iter(players))

    def _event_df(self, parser, name: str):
        try:
            df = parser.parse_event(name)
        except Exception:  # noqa: BLE001
            return None
        # demoparser2 returns an empty list (not a DataFrame) when 0 events.
        if df is None or not hasattr(df, "columns") or len(df) == 0:
            return None
        return df

    def _load_detonations(self, parser) -> List[dict]:
        """All utility detonations as {tick, steamid, x, y, z, util}."""
        out: List[dict] = []
        for ev, util in DETONATE_EVENTS.items():
            df = self._event_df(parser, ev)
            if df is None:
                continue
            for _, row in df.iterrows():
                out.append(
                    {
                        "tick": _i(row["tick"]),
                        "steamid": str(row.get("user_steamid")),
                        "x": _f(row.get("x")),
                        "y": _f(row.get("y")),
                        "z": _f(row.get("z")),
                        "util": util,
                    }
                )
        out.sort(key=lambda d: d["tick"])
        return out

    def _load_rounds(self, parser) -> List[dict]:
        """Build [{number, start_tick, t0_tick, end_tick, winner_team}] per round.

        t0_tick is the freeze-end (live round start) used as the zero point for
        round-relative timestamps, matching how the sample fixture reads.
        """
        starts = self._event_df(parser, "round_start")
        ends = self._event_df(parser, "round_end")
        official = self._event_df(parser, "round_officially_ended")
        freeze = self._event_df(parser, "round_freeze_end")
        if starts is None:
            return []

        start_ticks = sorted(_i(t) for t in starts["tick"].dropna().tolist())
        # Keep every round_end with a valid TICK. The winner is OPTIONAL: some
        # demos record NaN winners (warmup / incomplete), and dropping those
        # rows would throw away real rounds. Unknown winner -> -1.
        end_rows = sorted(
            (
                {"tick": _i(r["tick"]), "winner": _i(r["winner"], -1) if _isnum(r.get("winner")) else -1}
                for _, r in ends.iterrows()
                if _isnum(r.get("tick"))
            ),
            key=lambda r: r["tick"],
        ) if ends is not None else []
        # Fallback end boundaries when there are no usable round_end ticks.
        official_ticks = (
            sorted(_i(t) for t in official["tick"].dropna().tolist()) if official is not None else []
        )
        freeze_ticks = (
            sorted(_i(t) for t in freeze["tick"].dropna().tolist()) if freeze is not None else []
        )

        rounds: List[dict] = []
        for i, st in enumerate(start_ticks):
            nxt = start_ticks[i + 1] if i + 1 < len(start_ticks) else None
            # round_end whose tick falls after this start and before the next.
            end = next(
                (e for e in end_rows if e["tick"] > st and (nxt is None or e["tick"] < nxt)),
                None,
            )
            if end is None:
                # Fall back to round_officially_ended, then to the next round's
                # start (minus a tick), so a missing/garbled round_end never
                # drops an otherwise-real round.
                off = next(
                    (t for t in official_ticks if t > st and (nxt is None or t < nxt)), None
                )
                end_tick = off if off is not None else (nxt - 1 if nxt is not None else None)
                if end_tick is None or end_tick <= st:
                    continue
                end = {"tick": end_tick, "winner": -1}
            t0 = next((f for f in freeze_ticks if st <= f <= end["tick"]), st)
            rounds.append(
                {
                    "number": len(rounds) + 1,
                    "start_tick": st,
                    "t0_tick": t0,
                    "end_tick": end["tick"],
                    "winner_team": end["winner"],
                }
            )
        return rounds

    def _round_diagnostics(self, parser) -> str:
        """Human-readable counts to explain why no rounds were reconstructed."""
        def count(name: str) -> int:
            df = self._event_df(parser, name)
            return 0 if df is None else len(df)

        return (
            f"Events seen — round_start={count('round_start')}, "
            f"round_end={count('round_end')}, "
            f"round_officially_ended={count('round_officially_ended')}, "
            f"round_freeze_end={count('round_freeze_end')}. "
            "If round_start is 0 this looks like a POV/incomplete demo (GOTV "
            "demos work best)."
        )

    def _load_timeline(self, parser, rounds_meta: List[dict]) -> Dict[int, List[dict]]:
        """Per-second snapshots, grouped by tick.

        Returns {tick -> [ {steamid, x, y, z, team, place, alive}, ... ]}.
        """
        if not rounds_meta:
            return {}
        ticks: List[int] = []
        for rm in rounds_meta:
            t = rm["start_tick"]
            while t <= rm["end_tick"]:
                ticks.append(t)
                t += TIMELINE_STEP_TICKS
        ticks = sorted(set(ticks))
        try:
            df = parser.parse_ticks(
                ["X", "Y", "Z", "team_num", "last_place_name", "is_alive"],
                ticks=ticks,
            )
        except Exception as exc:  # noqa: BLE001
            raise DemoParseError(f"Could not read tick timeline: {exc}") from exc
        out: Dict[int, List[dict]] = {}
        for _, row in df.iterrows():
            out.setdefault(_i(row["tick"]), []).append(
                {
                    "steamid": str(row.get("steamid")),
                    "x": _f(row.get("X")),
                    "y": _f(row.get("Y")),
                    "z": _f(row.get("Z")),
                    "team": _i(row.get("team_num")),
                    "place": row.get("last_place_name"),
                    "alive": bool(row.get("is_alive", False)),
                }
            )
        return out

    def _positions_at(self, parser, ticks: List[int]) -> Dict[int, List[dict]]:
        """Exact positions/inventory of all players at the given ticks."""
        ticks = sorted({_i(t) for t in ticks if _isnum(t)})
        if not ticks:
            return {}
        try:
            df = parser.parse_ticks(
                ["X", "Y", "Z", "team_num", "last_place_name", "is_alive", "inventory"],
                ticks=ticks,
            )
        except Exception:  # noqa: BLE001
            return {}
        out: Dict[int, List[dict]] = {}
        for _, row in df.iterrows():
            out.setdefault(_i(row["tick"]), []).append(
                {
                    "steamid": str(row.get("steamid")),
                    "x": _f(row.get("X")),
                    "y": _f(row.get("Y")),
                    "z": _f(row.get("Z")),
                    "team": _i(row.get("team_num")),
                    "place": row.get("last_place_name"),
                    "alive": bool(row.get("is_alive", False)),
                    "inventory": list(row.get("inventory") or []),
                }
            )
        return out

    def _teams_per_round(
        self, timeline: Dict[int, List[dict]], target: str, rounds_meta: List[dict]
    ) -> Dict[int, int]:
        """Resolve the analyzed player's team (2=T/3=CT) for each round."""
        out: Dict[int, int] = {}
        for idx, rm in enumerate(rounds_meta):
            team = None
            for tick in sorted(timeline):
                if rm["start_tick"] <= tick <= rm["end_tick"]:
                    for snap in timeline[tick]:
                        if snap["steamid"] == target and snap["team"] in (TEAM_T, TEAM_CT):
                            team = snap["team"]
                            break
                if team is not None:
                    break
            if team is not None:
                out[idx] = team
        return out

    # -- per-round reconstruction ---------------------------------------------
    def _build_round(
        self,
        idx: int,
        rm: dict,
        map_name: str,
        target: str,
        target_team: Optional[int],
        deaths,
        blinds,
        bombs,
        detonations: List[dict],
        timeline: Dict[int, List[dict]],
        death_positions: Dict[int, List[dict]],
    ) -> Optional[RoundFacts]:
        if target_team is None:
            return None
        t0 = rm["t0_tick"]
        start, end = rm["start_tick"], rm["end_tick"]
        side = "T" if target_team == TEAM_T else "CT"
        player_team = side
        # winner_team is -1 when the demo didn't record a usable winner.
        round_winner = {TEAM_T: "T", TEAM_CT: "CT"}.get(rm["winner_team"], "unknown")

        def secs(tick: int) -> float:
            return max(0.0, round((tick - t0) / TICK_RATE, 1))

        def in_round(tick: int) -> bool:
            return start <= tick <= end

        # ---- the analyzed player's per-second path this round ----------------
        path: List[dict] = []  # [{tick, secs, place, region, x, y, z, alive}]
        for tick in sorted(timeline):
            if not in_round(tick):
                continue
            for snap in timeline[tick]:
                if snap["steamid"] == target:
                    path.append(
                        {
                            "tick": tick,
                            "secs": secs(tick),
                            "place": snap["place"],
                            "region": _to_region(snap["place"]),
                            "x": snap["x"],
                            "y": snap["y"],
                            "z": snap["z"],
                            "alive": snap["alive"],
                        }
                    )
        alive_path = [p for p in path if p["alive"]]
        primary_area = ""
        if alive_path:
            places = [p["place"] for p in alive_path if p["place"]]
            if places:
                primary_area = Counter(places).most_common(1)[0][0]

        # ---- events list (utility, bomb, the player's death) -----------------
        events: List[GameEvent] = []
        for det in detonations:
            if not in_round(det["tick"]):
                continue
            actor = self._actor_of(det["steamid"], target, target_team, det["tick"], death_positions, timeline)
            location = self._place_near(det["x"], det["y"], det["z"], det["tick"], timeline) or ""
            events.append(
                GameEvent(
                    timestamp_seconds=secs(det["tick"]),
                    event_type=det["util"],
                    actor=actor,
                    location=location,
                    description=f"{actor.title()} {det['util']} at {location or 'unknown'}",
                )
            )

        bomb_plant_secs: Optional[float] = None
        bombsite: Optional[str] = None
        if bombs is not None:
            for _, b in bombs.iterrows():
                if not _isnum(b.get("tick")):
                    continue
                btick = _i(b["tick"])
                if not in_round(btick):
                    continue
                planter = str(b.get("user_steamid"))
                actor = self._actor_of(planter, target, target_team, btick, death_positions, timeline)
                place = self._place_of(planter, btick, death_positions, timeline) or ""
                region = _to_region(place)
                bombsite = region or place or None
                bomb_plant_secs = secs(btick)
                events.append(
                    GameEvent(
                        timestamp_seconds=bomb_plant_secs,
                        event_type="bomb_plant",
                        actor=actor,
                        location=place,
                        description=f"Bomb planted at {place or 'site'}",
                    )
                )

        # ---- the analyzed player's death this round --------------------------
        death = self._player_death(deaths, target, in_round)
        survived = death is None
        death_tick = int(death["tick"]) if death is not None else None
        death_time = secs(death_tick) if death_tick is not None else None
        death_place = (
            self._place_of(target, death_tick, death_positions, timeline)
            if death_tick is not None
            else None
        )
        if death is not None:
            events.append(
                GameEvent(
                    timestamp_seconds=death_time or 0.0,
                    event_type="death",
                    actor="player",
                    location=death_place or "",
                    description=f"Player died to {death.get('attacker_name', 'enemy')} ({death.get('weapon', '?')})",
                )
            )
        events.sort(key=lambda e: e.timestamp_seconds)

        # ---- PlayerRoundSummary ---------------------------------------------
        nearest = None
        flash_support = False
        unused: List[str] = []
        if death_tick is not None:
            nearest = self._nearest_teammate_distance(target, target_team, death_tick, death_positions)
            flash_support = self._had_flash_support(
                blinds, target, target_team, death_tick, death_positions, timeline
            )
            unused = self._unused_utility(target, death_tick, death_positions)

        rotated_from, rotated_to, rotation_secs = self._detect_rotation(
            path, target_team, bomb_plant_secs
        )
        waited, alt_control = self._waited_behind_utility(
            path, events, target_team, death_time, secs(end)
        )

        summary = PlayerRoundSummary(
            survived=survived,
            death_time_seconds=death_time,
            death_location=death_place,
            nearest_teammate_distance_on_death=nearest,
            had_flash_support_before_death=flash_support,
            utility_unused_on_death=unused,
            primary_area=primary_area,
            rotated_from=rotated_from,
            rotated_to=rotated_to,
            rotation_time_seconds=rotation_secs,
            waited_after_enemy_utility_seconds=waited,
            alternate_map_control_taken=alt_control,
        )

        return RoundFacts(
            round_id=f"r{rm['number']}",
            round_number=rm["number"],
            map=map_name,
            side=side,
            player_team=player_team,
            round_winner=round_winner,
            bombsite=bombsite,
            events=events,
            player_summary=summary,
        )

    # -- fine-grained reconstruction helpers ----------------------------------
    def _player_death(self, deaths, target: str, in_round) -> Optional[dict]:
        if deaths is None:
            return None
        for _, row in deaths.iterrows():
            if not _isnum(row.get("tick")):
                continue
            if str(row.get("user_steamid")) == target and in_round(_i(row["tick"])):
                return {
                    "tick": _i(row["tick"]),
                    "attacker_name": row.get("attacker_name"),
                    "weapon": row.get("weapon"),
                }
        return None

    def _team_of(
        self, steamid: str, tick: int, *sources: Dict[int, List[dict]]
    ) -> Optional[int]:
        best = None
        for src in sources:
            if tick in src:
                for snap in src[tick]:
                    if snap["steamid"] == steamid and snap["team"] in (TEAM_T, TEAM_CT):
                        return snap["team"]
            # nearest sampled tick fallback
            for t in sorted(src, key=lambda x: abs(x - tick))[:3]:
                for snap in src[t]:
                    if snap["steamid"] == steamid and snap["team"] in (TEAM_T, TEAM_CT):
                        best = best or snap["team"]
        return best

    def _actor_of(
        self,
        steamid: str,
        target: str,
        target_team: int,
        tick: int,
        deaths_pos: Dict[int, List[dict]],
        timeline: Dict[int, List[dict]],
    ) -> str:
        if steamid == target:
            return "player"
        team = self._team_of(steamid, tick, deaths_pos, timeline)
        if team is None:
            return "enemy"
        return "teammate" if team == target_team else "enemy"

    def _place_of(
        self,
        steamid: str,
        tick: int,
        deaths_pos: Dict[int, List[dict]],
        timeline: Dict[int, List[dict]],
    ) -> Optional[str]:
        for src in (deaths_pos, timeline):
            if tick in src:
                for snap in src[tick]:
                    if snap["steamid"] == steamid and snap.get("place"):
                        return snap["place"]
        # nearest sampled tick
        for t in sorted(timeline, key=lambda x: abs(x - tick))[:3]:
            for snap in timeline[t]:
                if snap["steamid"] == steamid and snap.get("place"):
                    return snap["place"]
        return None

    def _place_near(
        self, x: float, y: float, z: float, tick: int, timeline: Dict[int, List[dict]]
    ) -> Optional[str]:
        """Callout for a world position: the place of the closest sampled player."""
        best_place, best_d = None, float("inf")
        for t in sorted(timeline, key=lambda v: abs(v - tick))[:3]:
            for snap in timeline[t]:
                if not snap.get("place"):
                    continue
                d = _dist((x, y, z), (snap["x"], snap["y"], snap["z"]))
                if d < best_d:
                    best_d, best_place = d, snap["place"]
        # Only trust it if reasonably close (~6m), else unknown.
        return best_place if best_d < 600 else best_place

    def _nearest_teammate_distance(
        self, target: str, target_team: int, death_tick: int, deaths_pos: Dict[int, List[dict]]
    ) -> Optional[float]:
        snaps = deaths_pos.get(death_tick, [])
        me = next((s for s in snaps if s["steamid"] == target), None)
        if me is None:
            return None
        best = None
        for s in snaps:
            if s["steamid"] == target or s["team"] != target_team or not s["alive"]:
                continue
            d = _dist((me["x"], me["y"], me["z"]), (s["x"], s["y"], s["z"]))
            best = d if best is None else min(best, d)
        return round(best, 1) if best is not None else None

    def _had_flash_support(
        self,
        blinds,
        target: str,
        target_team: int,
        death_tick: int,
        deaths_pos: Dict[int, List[dict]],
        timeline: Dict[int, List[dict]],
    ) -> bool:
        """True if a TEAMMATE blinded an ENEMY shortly before the player's death.

        player_blind carries the flasher (attacker_steamid) and the blinded
        player (user_steamid); we resolve both to teams from the real timeline.
        A meaningful enemy blind right before the death means the player had a
        tradeable/supported peek available.
        """
        if blinds is None:
            return False
        lo = death_tick - int(FLASH_SUPPORT_WINDOW_S * TICK_RATE)
        for _, row in blinds.iterrows():
            tick = _i(row["tick"])
            if not (lo <= tick <= death_tick):
                continue
            if _f(row.get("blind_duration")) < 0.7:
                continue
            flasher = str(row.get("attacker_steamid"))
            blinded = str(row.get("user_steamid"))
            flasher_team = self._team_of(flasher, tick, deaths_pos, timeline)
            blinded_team = self._team_of(blinded, tick, deaths_pos, timeline)
            if flasher_team == target_team and blinded_team not in (None, target_team):
                return True
        return False

    def _unused_utility(
        self, target: str, death_tick: int, deaths_pos: Dict[int, List[dict]]
    ) -> List[str]:
        snaps = deaths_pos.get(death_tick, [])
        me = next((s for s in snaps if s["steamid"] == target), None)
        if me is None:
            return []
        out: List[str] = []
        for item in me.get("inventory", []):
            tok = INVENTORY_UTILITY.get(item)
            if tok and tok not in out:
                out.append(tok)
        return out

    def _detect_rotation(
        self, path: List[dict], target_team: int, bomb_plant_secs: Optional[float]
    ) -> Tuple[Optional[str], Optional[str], Optional[float]]:
        """Heuristic CT rotation: first A<->B site-region change while alive,
        before any bomb plant. Derived from the player's REAL zone timeline."""
        if target_team != TEAM_CT:
            return None, None, None
        prev_site = None
        for p in path:
            if not p["alive"]:
                continue
            region = p["region"]
            if region not in ("A", "B"):
                continue
            if prev_site is None:
                prev_site = region
                continue
            if region != prev_site:
                if bomb_plant_secs is not None and p["secs"] >= bomb_plant_secs:
                    return None, None, None
                return prev_site, region, p["secs"]
        return None, None, None

    def _waited_behind_utility(
        self,
        path: List[dict],
        events: List[GameEvent],
        target_team: int,
        death_time: Optional[float],
        round_end_secs: float,
    ) -> Tuple[Optional[float], bool]:
        """Heuristic: how long the player held in the same region after an enemy
        blocking-utility (smoke/molotov) landed in/near that region, and whether
        they later took control elsewhere (changed region). Real timeline-based."""
        block = next(
            (
                e
                for e in events
                if e.actor == "enemy" and e.event_type in ("smoke", "molotov")
            ),
            None,
        )
        if block is None or not path:
            return None, False
        block_region = _to_region(block.location)
        # Player's region at the moment of the utility.
        at_util = min(
            (p for p in path if p["alive"]),
            key=lambda p: abs(p["secs"] - block.timestamp_seconds),
            default=None,
        )
        if at_util is None:
            return None, False
        # If the util didn't land where the player was, it's not blocking them.
        if block_region is not None and at_util["region"] is not None and block_region != at_util["region"]:
            return None, False
        start_region = at_util["region"]
        moved_secs = None
        alt_control = False
        for p in path:
            if p["secs"] <= block.timestamp_seconds or not p["alive"]:
                continue
            if start_region is not None and p["region"] not in (None, start_region):
                moved_secs = p["secs"]
                alt_control = True
                break
        end_point = moved_secs if moved_secs is not None else (death_time or round_end_secs)
        waited = round(max(0.0, end_point - block.timestamp_seconds), 1)
        return waited, alt_control
