/** バックエンド API クライアント。 */

import type {
  Example,
  ExampleState,
  ExtractResult,
  HealthResponse,
  ModelState,
  PersonaPreset,
  PersonaState,
} from './types';

/**
 * API のベース URL。
 * 本番は nginx が同一オリジンで /api を backend へ転送するので相対パスでよい。
 * 別ホストの API を叩きたい場合のみ VITE_API_BASE を指定する。
 */
const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

/** ローカル LLM の処理は長引くことがあるため、抽出だけ余裕を持たせる。 */
const EXTRACT_TIMEOUT_MS = 10 * 60 * 1000;
const DEFAULT_TIMEOUT_MS = 30 * 1000;

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

interface ErrorBody {
  detail?: string | { msg?: string }[];
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ErrorBody;
    if (typeof body?.detail === 'string') {
      return body.detail;
    }
    if (Array.isArray(body?.detail)) {
      const first = body.detail[0];
      if (first?.msg) {
        return first.msg;
      }
    }
  } catch {
    // JSON でない場合は既定のメッセージにフォールバックする。
  }
  return `サーバがエラーを返しました (HTTP ${response.status})`;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
}

/** fetch の共通処理 (タイムアウト・中断・エラー整形)。 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options;

  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const onAbort = () => controller.abort();
  signal?.addEventListener('abort', onAbort);

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new ApiError(await readErrorMessage(response), response.status);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('処理を中断しました。', 0);
    }
    throw new ApiError('バックエンドに接続できませんでした。', 0);
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}

// ---- 抽出 ----

/**
 * 複数 URL のキャッチフレーズ抽出をリクエストする。
 *
 * @param urls          対象 URL。空行は呼び出し側で除去済みであること。
 * @param personaPrompt 文を選ぶ際の観点。空文字なら観点なしで抽出する。
 * @param modelId       使用するモデルの id。
 * @param signal        中断用シグナル。
 */
export function extract(
  urls: string[],
  personaPrompt: string,
  modelId: string,
  signal?: AbortSignal,
): Promise<ExtractResult[]> {
  return request<ExtractResult[]>('/api/extract', {
    // 画面に表示されている観点とモデルをそのまま送る (空文字 = 観点なし)。
    // null は「サーバに保存されている値を使う」を意味するので送らない。
    method: 'POST',
    body: { urls, persona_prompt: personaPrompt, model_id: modelId || null },
    signal,
    timeoutMs: EXTRACT_TIMEOUT_MS,
  });
}

export function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/api/health');
}

// ---- モデル ----

export function fetchModels(): Promise<ModelState> {
  return request<ModelState>('/api/models');
}

export function selectModel(modelId: string): Promise<ModelState> {
  return request<ModelState>('/api/models/selected', {
    method: 'PUT',
    body: { model_id: modelId },
  });
}

// ---- 観点 (ペルソナ) ----

export function fetchPersonas(): Promise<PersonaState> {
  return request<PersonaState>('/api/personas');
}

export function savePersonaPreset(name: string, prompt: string): Promise<PersonaPreset> {
  return request<PersonaPreset>('/api/personas', { method: 'POST', body: { name, prompt } });
}

export function deletePersonaPreset(id: string): Promise<void> {
  return request<void>(`/api/personas/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function setActivePrompt(prompt: string): Promise<PersonaState> {
  return request<PersonaState>('/api/personas/active', { method: 'PUT', body: { prompt } });
}

// ---- お手本 ----

export function fetchExamples(): Promise<ExampleState> {
  return request<ExampleState>('/api/examples');
}

export function addExample(
  title: string,
  line2: string,
  line3: string,
  sourceUrl: string | null,
): Promise<Example> {
  return request<Example>('/api/examples', {
    method: 'POST',
    body: { title, line2, line3, source_url: sourceUrl },
  });
}

export function deleteExample(id: string): Promise<void> {
  return request<void>(`/api/examples/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
