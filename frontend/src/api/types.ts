/** バックエンド API の型定義 (backend/app/schemas.py と対応)。 */

export type MatchMethod = 'exact' | 'contained' | 'fuzzy' | 'nearest' | 'unverified' | 'none';

export interface ExtractResultMeta {
  fetch_ms: number | null;
  extract_ms: number | null;
  llm_ms: number | null;
  total_ms: number | null;
  http_status: number | null;
  extractor: string | null;
  candidate_count: number | null;
  /** LLM に渡した候補文そのもの。 */
  candidates: string[];
  title_source: string | null;
  line2_match: MatchMethod | null;
  line3_match: MatchMethod | null;
  persona_applied: boolean;
  example_count: number | null;
  llm_model: string | null;
  llm_model_id: string | null;
  llm_model_label: string | null;
}

/** URL 1 件分の抽出結果。 */
export interface ExtractResult {
  url: string;
  /** 商品名 (原文そのまま)。 */
  title: string;
  /** 商品の魅力を表す、原文そのままの一文。 */
  line2: string;
  /** 商品の特徴を表す、原文そのままの一文。 */
  line3: string;
  error: string | null;
  error_code: string | null;
  meta: ExtractResultMeta;
}

export interface HealthResponse {
  status: 'ok';
  version: string;
  llm_api_base: string;
  llm_model: string;
  max_urls_per_request: number;
}

/** 選択できる LLM 1 件。API キーそのものは含まれない。 */
export interface ModelInfo {
  id: string;
  label: string;
  api_base: string;
  model: string;
  /** API キーが設定されているか (値そのものは返らない)。 */
  has_api_key: boolean;
  /** api_key が参照している環境変数が未設定。 */
  missing_env_key: boolean;
  concurrency: number;
}

export interface ModelState {
  models: ModelInfo[];
  selected_id: string;
}

/** 保存された観点プリセット。 */
export interface PersonaPreset {
  id: string;
  name: string;
  prompt: string;
  created_at: string;
}

export interface PersonaState {
  presets: PersonaPreset[];
  /** 現在使用中の観点 (プリセットから選んだものでも、自由入力でもよい)。 */
  active_prompt: string;
  max_presets: number;
}

/** 👍 で貯まるお手本。 */
export interface Example {
  id: string;
  title: string;
  line2: string;
  line3: string;
  source_url: string | null;
  builtin: boolean;
  created_at: string;
}

export interface ExampleState {
  examples: Example[];
  max_examples: number;
}
