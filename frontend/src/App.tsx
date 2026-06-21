import { useState } from "react";
import { analyzeSample, analyzeUpload, getPlayerMemory } from "./api";
import type { CoachReport, DecisionMoment } from "./types";

function MomentCard({ m }: { m: DecisionMoment }) {
  return (
    <div className="card moment">
      <div className="moment-head">
        <span className="tag">{m.mistake_type}</span>
        <span className="muted">
          {m.map} · {m.side} · {m.round_id} · {Math.round(m.timestamp_seconds)}s · conf {m.confidence}
        </span>
      </div>
      <p><strong>Enemy action:</strong> {m.enemy_action}</p>
      <p><strong>Your response:</strong> {m.user_response}</p>
      <p><strong>Outcome:</strong> {m.outcome}</p>
      <p className="recommend"><strong>Coach:</strong> {m.recommended_response}</p>
      <details>
        <summary>Evidence</summary>
        <ul>
          {m.evidence.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

export default function App() {
  const [playerId, setPlayerId] = useState("local_user");
  const [file, setFile] = useState<File | null>(null);
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
      alert(`Redis memory for ${mem.player_id}: ${mem.count} stored decision moments.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="page">
      <header>
        <h1>CS2 Decision Coach</h1>
        <p className="muted">
          Post-game decision coaching for your own demos. Given what the enemy did, what should you
          have done differently?
        </p>
      </header>

      <section className="card controls">
        <label>
          Player ID
          <input value={playerId} onChange={(e) => setPlayerId(e.target.value)} placeholder="local_user" />
        </label>

        <div className="row">
          <button disabled={loading} onClick={() => run(() => analyzeSample(playerId))}>
            Analyze Sample Demo
          </button>
          <button className="secondary" disabled={loading} onClick={loadMemory}>
            View Redis Memory
          </button>
        </div>

        <hr />

        <label>
          Upload a demo (.dem or already-parsed .json)
          <input
            type="file"
            accept=".dem,.json"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <button
          disabled={loading || !file}
          onClick={() => file && run(() => analyzeUpload(file, playerId))}
        >
          Upload and Analyze
        </button>
      </section>

      {loading && <p className="status">Analyzing…</p>}
      {error && <p className="status error">Error: {error}</p>}

      {report && (
        <section className="results">
          <div className="card meta">
            <div><strong>Demo ID:</strong> {report.demo_id}</div>
            <div><strong>Map:</strong> {report.map}</div>
            <div><strong>Parser mode:</strong> <span className="tag">{report.parser_mode}</span></div>
          </div>

          <div className="card summary">
            <h2>Coaching summary</h2>
            <p>{report.final_coaching_summary}</p>
          </div>

          <h2>Detected decision moments ({report.moments.length})</h2>
          {report.moments.length === 0 && <p className="muted">No mistakes detected.</p>}
          {report.moments.map((m) => (
            <MomentCard key={m.moment_id} m={m} />
          ))}

          <h2>Practice drills</h2>
          <ul className="card">
            {report.drills.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>

          <h2>Similar memories from Redis ({report.similar_memory.length})</h2>
          {report.similar_memory.length === 0 ? (
            <p className="muted">
              No similar past mistakes yet. Run an analysis again to build up memory in Redis.
            </p>
          ) : (
            <ul className="card">
              {report.similar_memory.map((s) => (
                <li key={s.moment_id}>
                  <span className="tag">{s.mistake_type}</span> ({s.map}, similarity {s.similarity}):{" "}
                  {s.summary_text}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
