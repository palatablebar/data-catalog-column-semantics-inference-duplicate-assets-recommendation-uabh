export type Role = 'admin' | 'analyst' | 'viewer'
export type ColumnSemanticType =
  | 'phone'
  | 'id_card'
  | 'email'
  | 'amount'
  | 'datetime'
  | 'enum'
  | 'ambiguous'
  | 'unknown'

export interface User {
  id: string
  email: string
  display_name: string
  role: Role
}

export interface TagItem {
  id: string
  tag_name: string
  category: string
  source: 'manual' | 'inferred'
  confidence: number
  tagged_by: string
  created_at: string | null
}

export interface ColumnItem {
  id: string
  column_name: string
  data_type: string
  inferred_type: ColumnSemanticType | null
  confidence: number
  inference_source: 'auto' | 'manual' | null
  sync_status?: 'active' | 'missing'
  detected_change?: 'added' | 'missing' | null
  evidence?: string[]
  candidates?: { type: string; score: number }[]
  sample_values?: (string | null)[]
  profiled?: boolean
  stats?: {
    sample_count: number
    null_rate: number
    unique_count: number
    profile_source: string
  }
  asset?: AssetBrief
  tags?: TagItem[]
  ordinal?: number
  nullable?: boolean
  comment?: string
}

export interface AssetBrief {
  id: string
  asset_key: string
  table_name: string
  schema_name?: string
  layer: string
  domain: string
  row_count: number
  column_count: number
}

export interface AssetListItem extends AssetBrief {
  description?: string
  owner_name?: string
  source_kind?: 'sample_table' | 'catalog_metadata'
  is_active?: boolean
  updated_at?: string | null
  sensitive_tag_count?: number
  typed_column_count?: number
  missing_column_count?: number
  columns_preview?: ColumnItem[]
}

export interface AssetDetail extends AssetListItem {
  columns: ColumnItem[]
  active_column_count?: number
}

export interface Facets {
  layers: { value: string; label: string; count: number }[]
  domains: { value: string; count: number }[]
  stats: {
    assets: number
    sample_tables: number
    metadata_only: number
    columns: number
    profiled_columns: number
    sensitive_columns: number
  }
}

export interface PairAsset extends AssetBrief {
  owner_name?: string
  source_kind?: string
}

export interface MergeSuggestion {
  drop_asset_key: string
  keep_asset_key: string
  union_columns: string[]
  extra_in_drop: string[]
  missing_in_drop: string[]
  score: number
  jaccard: number
  structure_score: number
  verdict: string
  actions: string[]
  merged_columns?: { left: string; right: string; ratio: number }[]
  generated_by?: string
}

export interface SimilarityPair {
  id: string
  score: number
  jaccard: number
  structure_score: number
  method: string
  status: string
  verdict: string
  computed_at: string | null
  left: PairAsset
  right: PairAsset
  suggestion: MergeSuggestion | Record<string, never>
  left_columns?: ColumnItem[]
  right_columns?: ColumnItem[]
  diff?: DiffRow[]
}

export interface DiffRow {
  state: 'shared' | 'left_only' | 'right_only'
  left_column: ColumnItem | null
  right_column: ColumnItem | null
  similarity: number
}

export interface IngestColumnChange {
  column_name: string
  data_type: string
  nullable?: boolean
  ordinal?: number
}

export interface IngestTableReport {
  table_name: string
  asset_key: string
  status: 'readable' | 'unreadable'
  row_count: number | null
  upstream_column_count: number | null
  catalog_column_count: number
  added: IngestColumnChange[]
  missing: IngestColumnChange[]
  unchanged: string[]
  failure_reason: string | null
}

export interface IngestJobStats {
  mode?: string
  changes_applied?: boolean
  duration_ms?: number
  tables_synced?: number
  rows_scanned?: number
  columns_profiled?: number
  tables_total?: number
  tables_readable?: number
  tables_unreadable?: number
  columns_added?: number
  columns_missing?: number
  columns_unchanged?: number
  table_reports?: IngestTableReport[]
  [key: string]: unknown
}

export interface IngestJob {
  id: string
  job_name: string
  connection_name: string
  scope: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  stats: IngestJobStats
  error_code: string | null
  error_message: string | null
  triggered_by: string
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

export interface TagRow extends TagItem {
  column: {
    id: string
    column_name: string
    data_type: string
    inferred_type: ColumnSemanticType | null
    confidence: number
  }
  asset: PairAsset
}

export interface Overview {
  assets: number
  columns: number
  inferred_columns: number
  semantic_tags: number
  sensitive_columns: number
  similarity_pairs: number
  pending_pairs: number
  ingest_jobs: number
  completed_jobs: number
}
