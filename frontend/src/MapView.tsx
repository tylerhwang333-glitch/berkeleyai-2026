import { useState } from "react";
import { API_BASE } from "./api";
import type { BombState, CoachReport, EntityPosition, GameEvent, MapRadar, RoundView } from "./types";

// Team marker colors.
const TEAM_COLOR: Record<string, string> = {
  T: "#e0a13a",
  CT: "#4a90d9",
};

// Utility marker colors + short glyphs, keyed by util type.
const UTIL_STYLE: Record<string, { color: string; glyph: string }> = {
  smoke: { color: "#c4c4c4", glyph: "S" },
  molotov: { color: "#ff6b35", glyph: "M" },
  incendiary: { color: "#ff6b35", glyph: "M" },
  he: { color: "#5bd75b", glyph: "H" },
  flash: { color: "#ffd93d", glyph: "F" },
  decoy: { color: "#9b9b9b", glyph: "D" },
};

function clampPct(v: number | null | undefined): number | null {
  if (v == null || Number.isNaN(v)) return null;
  return Math.max(0, Math.min(100, v * 100));
}

function eventLabel(e: GameEvent): string {
  const t = Math.round(e.timestamp_seconds);
  const type = e.event_type.replace(/_/g, " ");
  const who = e.actor && e.actor !== "player" ? `${e.actor} ` : "";
  return `${t}s · ${who}${type}`;
}

/** True if an event actually carries placeable positions. */
function hasPositions(e: GameEvent): boolean {
  const s = e.snapshot;
  if (!s) return false;
  const all = [...s.players, ...s.utils];
  return all.some((p) => p.nx != null && p.ny != null);
}

function PlayerMarker({ p }: { p: EntityPosition }) {
  const left = clampPct(p.nx);
  const top = clampPct(p.ny);
  if (left == null || top == null) return null;
  const color = (p.team && TEAM_COLOR[p.team]) || "#999";
  const dead = p.alive === false;
  const title = `${p.label}${p.team ? ` (${p.team})` : ""}${dead ? " — dead" : ""}`;
  return (
    <span
      className={`mv-marker mv-player${p.is_analyzed_player ? " mv-you" : ""}${dead ? " mv-dead" : ""}`}
      style={{ left: `${left}%`, top: `${top}%`, background: color }}
      title={title}
    >
      {dead ? "×" : ""}
    </span>
  );
}

function BombMarker({ bomb }: { bomb: BombState }) {
  const left = clampPct(bomb.nx);
  const top = clampPct(bomb.ny);
  if (left == null || top == null) return null;
  return (
    <span
      className="mv-marker mv-bomb"
      style={{ left: `${left}%`, top: `${top}%` }}
      title={`Bomb planted${bomb.site ? ` at ${bomb.site}` : ""}`}
    >
      C4
    </span>
  );
}

function bombBadge(bomb: BombState | null | undefined): string {
  if (!bomb || bomb.status === "not_planted") return "Bomb not planted";
  if (bomb.status === "dropped") return "Bomb dropped — no plant";
  return `Bomb planted${bomb.site ? ` · ${bomb.site}` : ""}`;
}

function UtilMarker({ u }: { u: EntityPosition }) {
  const left = clampPct(u.nx);
  const top = clampPct(u.ny);
  if (left == null || top == null) return null;
  const style = UTIL_STYLE[u.util_type ?? ""] ?? { color: "#bbb", glyph: "•" };
  return (
    <span
      className="mv-marker mv-util"
      style={{ left: `${left}%`, top: `${top}%`, borderColor: style.color, color: style.color }}
      title={`${u.util_type ?? "util"}${u.team ? ` (${u.team})` : ""}`}
    >
      {style.glyph}
    </span>
  );
}

export function MapLegend() {
  return (
    <div className="mv-legend">
      <span className="mv-leg">
        <i className="mv-dot" style={{ background: TEAM_COLOR.T }} /> T
      </span>
      <span className="mv-leg">
        <i className="mv-dot" style={{ background: TEAM_COLOR.CT }} /> CT
      </span>
      <span className="mv-leg">
        <i className="mv-dot mv-ring" /> You
      </span>
      <span className="mv-leg">
        <i className="mv-dot mv-dead-dot">×</i> Dead
      </span>
      <span className="mv-leg-sep" />
      {Object.entries({ smoke: "Smoke", molotov: "Fire", he: "HE", flash: "Flash" }).map(
        ([k, name]) => (
          <span className="mv-leg" key={k}>
            <i className="mv-dot mv-util-dot" style={{ borderColor: UTIL_STYLE[k].color, color: UTIL_STYLE[k].color }}>
              {UTIL_STYLE[k].glyph}
            </i>
            {name}
          </span>
        ),
      )}
    </div>
  );
}

export function RoundMap({ round, radar }: { round: RoundView; radar: MapRadar }) {
  const events = round.events.filter(hasPositions);
  const [eventIdx, setEventIdx] = useState(0);
  const event = events[Math.min(eventIdx, events.length - 1)];
  const snap = event?.snapshot;
  const imgUrl = radar.image_url.startsWith("http")
    ? radar.image_url
    : `${API_BASE}${radar.image_url}`;
  const mapLabel = radar.map;

  return (
    <div className="card mapview">
      <div className="mv-controls">
        <span className="mv-round-title">
          Round {round.round_number} <span className="mv-round-sub">({round.side}) · {round.round_winner} win</span>
        </span>
        <span className={`mv-bomb-badge mv-bomb-${round.bomb?.status ?? "not_planted"}`}>
          {bombBadge(round.bomb)}
        </span>
      </div>

      <div className="mv-body">
        <ol className="mv-events">
          {events.map((e, i) => (
            <li key={`${e.tick}-${i}`}>
              <button
                className={`mv-event${i === eventIdx ? " active" : ""}`}
                onClick={() => setEventIdx(i)}
              >
                <span className="mv-event-label">{eventLabel(e)}</span>
                {e.zone && e.zone !== "Unknown" && (
                  <span className="mv-event-zone">{e.zone}</span>
                )}
              </button>
            </li>
          ))}
        </ol>

        <div className="mv-stage">
          <div className="mv-canvas">
            <img src={imgUrl} alt={`${mapLabel} radar`} draggable={false} />
            {snap?.utils.map((u, i) => (
              <UtilMarker key={`u${i}`} u={u} />
            ))}
            {round.bomb?.status === "planted" &&
              (event?.tick == null ||
                round.bomb.tick == null ||
                event.tick >= round.bomb.tick) && <BombMarker bomb={round.bomb} />}
            {snap?.players.map((p, i) => (
              <PlayerMarker key={`p${i}`} p={p} />
            ))}
          </div>
          {event && (
            <p className="mv-caption">
              {event.description || eventLabel(event)}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/** Rounds that carry plottable positions, keyed by round_id. */
export function mappableRounds(report: CoachReport): Map<string, RoundView> {
  const out = new Map<string, RoundView>();
  for (const r of report.rounds ?? []) {
    if (r.events.some(hasPositions)) out.set(r.round_id, r);
  }
  return out;
}
