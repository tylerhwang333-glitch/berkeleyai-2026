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
}

export interface PlayerMemoryResponse {
  player_id: string;
  count: number;
  moments: DecisionMoment[];
}
