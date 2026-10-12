export interface FileRecord {
  id: number;
  location_id: number;
  rel_path: string;
  name: string;
  extension: string;
  mime_type: string;
  category: string;
  size_bytes: number;
  sha256: string | null;
  modified_at: string | null;
  accessed_at: string | null;
  accessed_at_reliable: boolean;
  storage_class: string | null;
  text_chars: number;
  status: string;
  error: string | null;
  duplicate_group_id: number | null;
  embedding_model: string | null;
  last_scanned_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Location {
  id: number;
  name: string;
  kind: "local" | "s3";
  path: string | null;
  bucket: string | null;
  prefix: string | null;
  created_at: string;
  file_count: number;
  total_bytes: number;
  last_scan_status: string | null;
}

export interface ScanJob {
  id: number;
  location_id: number;
  status: string;
  phase: string;
  total_files: number;
  processed_files: number;
  error_count: number;
  skipped_count: number;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface CompressionResult {
  id: number;
  file_id: number;
  method: string;
  lossless: boolean;
  status: string;
  original_bytes: number;
  output_bytes: number | null;
  duration_ms: number | null;
  quality_note: string | null;
  quality_metric: Record<string, unknown>;
  restore_method: string | null;
  output_path: string | null;
  is_best: boolean;
  ratio: number | null;
  percent_saved: number | null;
  file_name?: string;
  file_path?: string;
  category?: string;
}

export interface Prediction {
  file_id: number;
  probability: number | null;
  reliability: "ok" | "low" | "unavailable";
  reason: string | null;
  horizon_days: number;
  history_source: string | null;
}

export interface FileDetail extends FileRecord {
  metadata_json: Record<string, unknown>;
  text_preview: string | null;
  chunk_count: number;
  duplicates: FileRecord[];
  similar: { file_id: number; name: string; path: string; score: number }[];
  compression: CompressionResult[];
  prediction: Prediction | null;
  access_event_count: number;
  location_name: string | null;
}

export interface DuplicateGroup {
  id: number;
  sha256: string;
  size_bytes: number;
  file_count: number;
  redundant_bytes: number;
  files: FileRecord[];
  suggested_keep_id: number | null;
}

export interface SimilarityPair {
  id: number;
  score: number;
  coverage_a: number;
  coverage_b: number;
  embedding_model: string;
  file_a: FileRecord;
  file_b: FileRecord;
}

export interface Conflict {
  kind: string;
  field: string | null;
  value_a?: string;
  value_b?: string;
  excerpt_a?: string;
  excerpt_b?: string;
  values?: { value: string; sources: number[] }[];
  statements?: { text: string; sources: number[] }[];
}

export interface Comparison {
  file_a: FileRecord;
  file_b: FileRecord;
  document_similarity: number | null;
  exact_duplicate: boolean;
  embedding_model: string;
  sentence_threshold: number;
  shared: { a: string; b: string; score: number; exact: boolean }[];
  unique_a: { text: string; best_score: number }[];
  unique_b: { text: string; best_score: number }[];
  conflicts: Conflict[];
  shared_ratio_a: number;
  shared_ratio_b: number;
  warnings: string[];
  disclaimer: string;
}

export interface Consolidation {
  id: number;
  title: string;
  source_file_ids: number[];
  draft_markdown: string;
  edited_markdown: string | null;
  report: {
    sources?: { index: number; file_id: number; path: string }[];
    units_total?: number;
    units_kept?: number;
    units_merged?: number;
    facts_consensus?: number;
    conflict_count?: number;
    conflicts?: Conflict[];
    preservation?: {
      method: string;
      tokens_checked: number;
      tokens_missing: number;
      missing_by_source: Record<string, string[]>;
      all_detected_tokens_present: boolean;
      note: string;
    };
  };
  status: string;
  original_bytes: number;
  output_bytes: number;
  export_path: string | null;
  export_format: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanAction {
  id: number;
  plan_id: number;
  priority: number;
  action_type: string;
  title: string;
  reason: string;
  evidence: Record<string, unknown>;
  target_file_ids: number[];
  params: Record<string, unknown>;
  expected_bytes_saved: number;
  expected_monthly_savings_usd: number;
  estimated_cost_usd: number;
  risk_level: "low" | "medium" | "high";
  risks: string[];
  reversible: boolean;
  reversibility_note: string;
  executable: boolean;
  status: string;
  result: Record<string, unknown>;
  decided_at: string | null;
  executed_at: string | null;
}

export interface Plan {
  id: number;
  location_id: number | null;
  strategy: string;
  status: string;
  summary: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  created_at: string;
  actions?: PlanAction[];
}

export interface Operation {
  id: number;
  timestamp: string;
  actor: string;
  operation: string;
  target: string | null;
  status: string;
  dry_run: boolean;
  details: Record<string, unknown>;
}

export interface Evaluation {
  id: number;
  dataset_kind: string;
  horizon_days: number;
  params: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  metrics: Record<string, Record<string, number | null>>;
  selected_model: string;
  created_at: string;
}

export interface EmbeddingStatus {
  configured_backend: string;
  configured_model: string;
  loaded: string;
  active_backend: string | null;
  active_model: string | null;
  error: string | null;
  semantic: string | null;
}
