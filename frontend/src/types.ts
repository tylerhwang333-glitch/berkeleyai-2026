export interface DecisionMoment {
  moment_id: string;
  player_id: string;
  demo_id: string;
  round_id: string;
  map: string;
  side: string;
  // Canonical map zone resolved deterministically on the backend ("Unknown" if
  // no nav/coordinate/callout data was available). Never inferred by the LLM.
  zone?: string | null;
  timestamp_seconds: number;
  enemy_action: string;
  user_response: string;
  outcome: string;
  mistake_type: string;
  evidence: string[];
  recommended_response: string;
  confidence: number;
  summary_text: string;
}

// One player or piece of utility on the map at a single tick. nx/ny are a 0..1
// fraction of the radar image (scaled on the backend), so the client just
// multiplies them by the rendered image size.
export interface EntityPosition {
  kind: "player" | "util";
  label: string;
  team?: string | null; // "T" | "CT" | null
  alive?: boolean | null;
  is_analyzed_player: boolean;
  util_type?: string | null;
  x: number;
  y: number;
  z: number;
  nx?: number | null;
  ny?: number | null;
}

export interface EventSnapshot {
  players: EntityPosition[];
  utils: EntityPosition[];
}

export interface GameEvent {
  timestamp_seconds: number;
  tick?: number | null;
  event_type: string;
  actor: string;
  target?: string | null;
  location?: string | null;
  zone?: string | null;
  description: string;
  snapshot?: EventSnapshot | null;
}

export interface BombState {
  status: "planted" | "dropped" | "not_planted";
  site?: string | null;
  tick?: number | null;
  nx?: number | null;
  ny?: number | null;
}

export interface RoundView {
  round_id: string;
  round_number: number;
  side: string;
  round_winner: string;
  bombsite?: string | null;
  bomb?: BombState | null;
  events: GameEvent[];
}

export interface MapRadar {
  map: string;
  image_url: string;
  pos_x: number;
  pos_y: number;
  scale: number;
  size: number;
}

export interface SimilarMemoryItem {
  moment_id: string;
  mistake_type: string;
  summary_text: string;
  similarity: number;
  map: string;
}

export interface CoachReport {
  report_id: string;
  player_id: string;
  demo_id: string;
  parser_mode: string;
  map: string;
  analyzed_player?: string | null;
  moments: DecisionMoment[];
  similar_memory: SimilarMemoryItem[];
  final_coaching_summary: string;
  drills: string[];
  map_radar?: MapRadar | null;
  rounds: RoundView[];
}

export interface PlayerMemoryResponse {
  player_id: string;
  count: number;
  moments: DecisionMoment[];
}

export interface HealthResponse {
  status: string;
  // False when the backend can't reach Redis. The memory layer (saved patterns
  // + recurring-pattern retrieval) silently no-ops in that state, so the UI
  // surfaces it instead of letting "0 patterns" look like a clean record.
  redis_connected: boolean;
  vector_index: boolean;
}
