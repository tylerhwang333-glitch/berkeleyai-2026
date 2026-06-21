import type { CoachReport, HealthResponse, PlayerMemoryResponse } from "./types";

// When VITE_BACKEND_URL is undefined (e.g. local `npm run dev`) we fall back to
// the dev backend. In the Docker/production build it is set to "" so requests go
// to the same origin and nginx proxies /api + /health to the backend container.
export const API_BASE = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8000";

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

export async function analyzeUpload(
  file: File,
  playerId: string,
  playerName?: string,
): Promise<CoachReport> {
  const form = new FormData();
  form.append("file", file);
  form.append("player_id", playerId || "local_user");
  // For real .dem files, picks which player in the demo to coach (optional).
  if (playerName && playerName.trim()) form.append("player_name", playerName.trim());
  const res = await fetch(`${API_BASE}/api/analyze/upload`, {
    method: "POST",
    body: form,
  });
  return handle<CoachReport>(res);
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/health`);
  return handle<HealthResponse>(res);
}

export async function getPlayerMemory(playerId: string): Promise<PlayerMemoryResponse> {
  const res = await fetch(`${API_BASE}/api/player/${encodeURIComponent(playerId || "local_user")}/memory`);
  return handle<PlayerMemoryResponse>(res);
}
