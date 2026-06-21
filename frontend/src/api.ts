import type { CoachReport, PlayerMemoryResponse } from "./types";

const API_BASE = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export async function analyzeSample(playerId: string): Promise<CoachReport> {
  const res = await fetch(`${API_BASE}/api/analyze/sample`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ player_id: playerId || "local_user" }),
  });
  return handle<CoachReport>(res);
}

export async function analyzeUpload(file: File, playerId: string): Promise<CoachReport> {
  const form = new FormData();
  form.append("file", file);
  form.append("player_id", playerId || "local_user");
  const res = await fetch(`${API_BASE}/api/analyze/upload`, {
    method: "POST",
    body: form,
  });
  return handle<CoachReport>(res);
}

export async function getPlayerMemory(playerId: string): Promise<PlayerMemoryResponse> {
  const res = await fetch(`${API_BASE}/api/player/${encodeURIComponent(playerId || "local_user")}/memory`);
  return handle<PlayerMemoryResponse>(res);
}
