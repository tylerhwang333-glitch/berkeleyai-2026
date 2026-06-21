import { useState } from "react";
import { analyzeSample, analyzeUpload, getPlayerMemory } from "./api";
import type { CoachReport, DecisionMoment } from "./types";
import { MapLegend, RoundMap, mappableRounds } from "./MapView";

const PARSER_MODE_LABELS: Record<string, string> = {
  sample_fixture: "Sample fixture",
  json_upload: "Parsed JSON upload",
  real_dem_parser: "Real .dem (demoparser2)",
  mock_dem_parser: "Mock .dem",
};

const MISTAKE_LABELS: Record<string, string> = {
  passive_response_to_utility: "Passive Utility Response",
  isolated_death: "Isolated Death",
  early_overrotation: "Early Overrotation",
  utility_inefficiency: "Utility Inefficiency",
};

function titleCase(s: string) {
  return s
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function mistakeLabel(type: string) {
  return MISTAKE_LABELS[type] ?? (type ? titleCase(type) : "Decision Mistake");
}

function mapName(map: string) {
  if (!map) return "Unknown map";
  const cleaned = map.replace(/^(de|cs)_/i, "");
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

function sideLabel(side: string) {
  const u = (side ?? "").toUpperCase();
  if (u === "T") return "T Side";
  if (u === "CT") return "CT Side";
  return side || "—";
}

function roundLabel(roundId: string) {
  const digits = (roundId ?? "").match(/\d+/);
  return digits ? `Round ${digits[0]}` : roundId || "Round";
}

function Confidence({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, Math.round((value ?? 0) * 100)));
  return (
    <span className="confidence" title={`${pct}% confidence`} aria-label={`${pct}% confidence`}>
      <span className="confidence-track">
        <span className="confidence-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="confidence-val">{pct}% confidence</span>
    </span>
  );
}

function MomentCard({ m }: { m: DecisionMoment }) {
  const hasZone = !!m.zone && m.zone !== "Unknown";
  return (
    <article className="card moment">
      <div className="moment-head">
        <span className="mistake-pill">{mistakeLabel(m.mistake_type)}</span>
        <Confidence value={m.confidence} />
      </div>

      <div className="meta-pills">
        <span className="meta-pill">{mapName(m.map)}</span>
        <span className="meta-pill">{sideLabel(m.side)}</span>
        <span className="meta-pill">{roundLabel(m.round_id)}</span>
        <span className="meta-pill">{Math.round(m.timestamp_seconds)}s</span>
        {hasZone && <span className="meta-pill subtle">{m.zone}</span>}
      </div>

      <div className="moment-grid">
        <div className="moment-field">
          <span className="moment-field-label">Enemy action</span>
          <p>{m.enemy_action}</p>
        </div>
        <div className="moment-field">
          <span className="moment-field-label">Your response</span>
          <p>{m.user_response}</p>
        </div>
        <div className="moment-field">
          <span className="moment-field-label">Outcome</span>
          <p>{m.outcome}</p>
        </div>
      </div>

      <div className="better-play">
        <span className="better-play-label">Better decision</span>
        <p>{m.recommended_response}</p>
      </div>

      {m.evidence.length > 0 && (
        <details className="evidence">
          <summary>Evidence ({m.evidence.length})</summary>
          <ul>
            {m.evidence.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}

// Criticized rounds, interleaved with each round's event map: the round's map
// is rendered immediately before that round's decision-moment card(s).
function CriticizedRounds({ report }: { report: CoachReport }) {
  const mapped = mappableRounds(report);
  const radar = report.map_radar ?? null;

  // Group moments by round, preserving the order they appear in the report.
  const order: string[] = [];
  const byRound: Record<string, DecisionMoment[]> = {};
  for (const m of report.moments) {
    if (!byRound[m.round_id]) {
      byRound[m.round_id] = [];
      order.push(m.round_id);
    }
    byRound[m.round_id].push(m);
  }

  const anyMap = !!radar && order.some((rid) => mapped.has(rid));

  return (
    <>
      {anyMap && <MapLegend />}
      {order.map((rid) => (
        <div className="round-block" key={rid}>
          {radar && mapped.has(rid) && <RoundMap round={mapped.get(rid)!} radar={radar} />}
          {byRound[rid].map((m) => (
            <MomentCard key={m.moment_id} m={m} />
          ))}
        </div>
      ))}
    </>
  );
}

export default function AnalyzeApp() {
  const [playerId, setPlayerId] = useState("local_user");
  const [file, setFile] = useState<File | null>(null);
  const [playerName, setPlayerName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<CoachReport | null>(null);

  async function run(fn: () => Promise<CoachReport>) {
    setLoading(true);
    setError(null);
    try {
      const r = await fn();
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function loadMemory() {
    setError(null);
    try {
      const mem = await getPlayerMemory(playerId);
      alert(`${mem.count} saved decision moments for "${mem.player_id}".`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="page">
      <nav className="app-nav">
        <a className="app-logo" href="#/">
          rank<span className="accent">up</span>.ai
        </a>
        <a className="app-back" href="#/">
          ← Home
        </a>
      </nav>

      <header className="app-header">
        <span className="badge">CS2 demo analysis</span>
        <h1>Decision Coach</h1>
        <p className="lead">
          See exactly which decisions cost you the round — and the better play you should have made.
        </p>
      </header>

      <section className="card controls">
        <div className="field">
          <label htmlFor="playerId">Player ID</label>
          <input
            id="playerId"
            value={playerId}
            onChange={(e) => setPlayerId(e.target.value)}
            placeholder="local_user"
          />
        </div>

        <div className="row">
          <button className="btn primary" disabled={loading} onClick={() => run(() => analyzeSample(playerId))}>
            Analyze Sample Demo
          </button>
          <button className="btn ghost" disabled={loading} onClick={loadMemory}>
            View Saved Patterns
          </button>
        </div>

        <div className="divider" />

        <div className="field">
          <label htmlFor="demoFile">Upload a CS2 demo (.dem) or parsed .json</label>
          <input
            id="demoFile"
            type="file"
            accept=".dem,.json"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <div className="field">
          <label htmlFor="playerName">
            In-demo player name <span className="optional">(optional)</span>
          </label>
          <input
            id="playerName"
            value={playerName}
            onChange={(e) => setPlayerName(e.target.value)}
            placeholder="your Steam name in the demo"
          />
        </div>
        <p className="hint">.dem files are parsed with demoparser2. No name given? We analyze the first player.</p>
        <button
          className="btn primary"
          disabled={loading || !file}
          onClick={() => file && run(() => analyzeUpload(file, playerId, playerName))}
        >
          Upload &amp; Analyze
        </button>
      </section>

      {loading && (
        <div className="card status-card">
          <span className="spinner" aria-hidden="true" />
          <span>Analyzing your round…</span>
        </div>
      )}
      {error && (
        <div className="card status-card error" role="alert">
          <strong>Something went wrong.</strong>
          <span>{error}</span>
        </div>
      )}

      {report && (
        <section className="results">
          <div className="report-meta">
            <span className="meta-pill strong">{mapName(report.map)}</span>
            {report.analyzed_player && (
              <span className="meta-pill">Player: {report.analyzed_player}</span>
            )}
            <span className="meta-pill subtle">
              {PARSER_MODE_LABELS[report.parser_mode] ?? report.parser_mode}
            </span>
            <span className="meta-pill subtle">{report.demo_id}</span>
          </div>

          <div className="card summary">
            <h2>Coaching summary</h2>
            <p>{report.final_coaching_summary}</p>
          </div>

          <div className="section-head">
            <h2>Criticized rounds</h2>
            <span className="count-pill">{report.moments.length}</span>
          </div>
          {report.moments.length === 0 ? (
            <div className="card empty">No decision mistakes detected in this demo. Clean round.</div>
          ) : (
            <CriticizedRounds report={report} />
          )}

          <div className="section-head">
            <h2>Practice focus</h2>
          </div>
          <ul className="card drills">
            {report.drills.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>

          <div className="section-head">
            <h2>Recurring patterns</h2>
            <span className="count-pill">{report.similar_memory.length}</span>
          </div>
          {report.similar_memory.length === 0 ? (
            <div className="card empty">
              No recurring patterns yet. Analyze more rounds to build your improvement history.
            </div>
          ) : (
            <div className="pattern-grid">
              {report.similar_memory.map((s) => (
                <div className="card pattern" key={s.moment_id}>
                  <div className="pattern-head">
                    <span className="mistake-pill small">{mistakeLabel(s.mistake_type)}</span>
                    <span className="meta-pill subtle">{Math.round(s.similarity * 100)}% match</span>
                  </div>
                  <p>{s.summary_text}</p>
                  <span className="meta-pill subtle">{mapName(s.map)}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
