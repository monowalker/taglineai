import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from './api/client';
import { ApiError } from './api/client';
import type { Example, ExtractResult, ModelInfo, PersonaPreset } from './api/types';
import { LoadingPanel } from './components/LoadingPanel';
import { ModelSelect } from './components/ModelSelect';
import { ResultList } from './components/ResultList';
import { SettingsModal } from './components/SettingsModal';
import { UrlForm } from './components/UrlForm';
import { findExample } from './utils/examples';

/** バックエンドから取得できるまでの暫定値。 */
const FALLBACK_MAX_URLS = 20;

export default function App() {
  const [urlText, setUrlText] = useState('');
  const [results, setResults] = useState<ExtractResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [submittedCount, setSubmittedCount] = useState(0);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [maxUrls, setMaxUrls] = useState(FALLBACK_MAX_URLS);

  // 使用するモデル
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModelId, setSelectedModelId] = useState('');
  const [modelBusy, setModelBusy] = useState(false);

  // 観点 (ペルソナ)
  const [personaPrompt, setPersonaPrompt] = useState('');
  const [presets, setPresets] = useState<PersonaPreset[]>([]);
  const [maxPresets, setMaxPresets] = useState(5);

  // お手本
  const [examples, setExamples] = useState<Example[]>([]);
  const [maxExamples, setMaxExamples] = useState(10);
  const [exampleBusyUrl, setExampleBusyUrl] = useState<string | null>(null);

  // 設定ダイアログ
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);

  // 実行中のリクエストを中断できるようにしておく。
  const abortRef = useRef<AbortController | null>(null);

  // 起動時に、表示制約・観点・お手本をサーバから読み込む。
  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const [health, modelState, personas, exampleState] = await Promise.all([
          api.fetchHealth(),
          api.fetchModels(),
          api.fetchPersonas(),
          api.fetchExamples(),
        ]);
        if (cancelled) {
          return;
        }
        setMaxUrls(health.max_urls_per_request);
        setModels(modelState.models);
        setSelectedModelId(modelState.selected_id);
        setPersonaPrompt(personas.active_prompt);
        setPresets(personas.presets);
        setMaxPresets(personas.max_presets);
        setExamples(exampleState.examples);
        setMaxExamples(exampleState.max_examples);
      } catch {
        // 設定が読めなくても抽出自体は既定値で動くので、ここでは黙って続行する。
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = useCallback(
    async (urls: string[]) => {
      const controller = new AbortController();
      abortRef.current = controller;

      setLoading(true);
      setGlobalError(null);
      setResults([]);
      setSubmittedCount(urls.length);

      try {
        setResults(await api.extract(urls, personaPrompt, selectedModelId, controller.signal));
      } catch (error) {
        setGlobalError(
          error instanceof ApiError ? error.message : '予期しないエラーが発生しました。',
        );
      } finally {
        setLoading(false);
        abortRef.current = null;
      }
    },
    [personaPrompt, selectedModelId],
  );

  /** モデルを切り替える。選択はサーバに保存する。 */
  const handleModelChange = useCallback(
    async (modelId: string) => {
      // 失敗したときに戻すため、切り替える前の選択を控えておく。
      const previous = selectedModelId;
      setSelectedModelId(modelId);
      setModelBusy(true);
      setGlobalError(null);
      try {
        const state = await api.selectModel(modelId);
        setModels(state.models);
        setSelectedModelId(state.selected_id);
      } catch (error) {
        setGlobalError(
          error instanceof ApiError ? error.message : 'モデルの切り替えに失敗しました。',
        );
        // 保存に失敗したら画面の表示も戻す。サーバの状態を取れればそちらを優先する。
        const state = await api.fetchModels().catch(() => null);
        setSelectedModelId(state ? state.selected_id : previous);
      } finally {
        setModelBusy(false);
      }
    },
    [selectedModelId],
  );

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // ---- 設定 ----

  const closeSettings = useCallback(async () => {
    setSettingsError(null);
    setSettingsBusy(true);
    try {
      // 観点はダイアログを閉じるときにサーバへ保存する。
      const state = await api.setActivePrompt(personaPrompt);
      setPresets(state.presets);
      setMaxPresets(state.max_presets);
      setSettingsOpen(false);
    } catch (error) {
      setSettingsError(
        error instanceof ApiError ? error.message : '観点の保存に失敗しました。',
      );
    } finally {
      setSettingsBusy(false);
    }
  }, [personaPrompt]);

  const handleSavePreset = useCallback(async (name: string, prompt: string) => {
    setSettingsError(null);
    setSettingsBusy(true);
    try {
      await api.savePersonaPreset(name, prompt);
      setPresets((await api.fetchPersonas()).presets);
    } catch (error) {
      setSettingsError(
        error instanceof ApiError ? error.message : 'プリセットの保存に失敗しました。',
      );
    } finally {
      setSettingsBusy(false);
    }
  }, []);

  const handleDeletePreset = useCallback(async (id: string) => {
    setSettingsError(null);
    setSettingsBusy(true);
    try {
      await api.deletePersonaPreset(id);
      setPresets((await api.fetchPersonas()).presets);
    } catch (error) {
      setSettingsError(
        error instanceof ApiError ? error.message : 'プリセットの削除に失敗しました。',
      );
    } finally {
      setSettingsBusy(false);
    }
  }, []);

  const handleDeleteExample = useCallback(async (id: string) => {
    setSettingsError(null);
    setSettingsBusy(true);
    try {
      await api.deleteExample(id);
      setExamples((await api.fetchExamples()).examples);
    } catch (error) {
      setSettingsError(
        error instanceof ApiError ? error.message : 'お手本の削除に失敗しました。',
      );
    } finally {
      setSettingsBusy(false);
    }
  }, []);

  /** 👍 の切り替え。登録済みなら外す。 */
  const handleToggleExample = useCallback(
    async (result: ExtractResult) => {
      setExampleBusyUrl(result.url);
      setGlobalError(null);
      try {
        const existing = findExample(examples, result.title, result.line2, result.line3);
        if (existing) {
          if (existing.builtin) {
            return; // 組み込みのお手本は外せない
          }
          await api.deleteExample(existing.id);
        } else {
          await api.addExample(result.title, result.line2, result.line3, result.url);
        }
        setExamples((await api.fetchExamples()).examples);
      } catch (error) {
        setGlobalError(
          error instanceof ApiError ? error.message : 'お手本の更新に失敗しました。',
        );
      } finally {
        setExampleBusyUrl(null);
      }
    },
    [examples],
  );

  const savedExampleCount = examples.filter((example) => !example.builtin).length;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="container app-header-inner">
          <div className="min-w-0">
            <h1 className="app-title">商品キャッチフレーズ抽出</h1>
            <p className="app-subtitle mb-0">
              商品ページの URL から、ページ内に実在する文章だけを抜き出します。
            </p>
          </div>
          <div className="header-actions">
            <ModelSelect
              models={models}
              selectedId={selectedModelId}
              disabled={loading || modelBusy}
              onChange={(id) => void handleModelChange(id)}
            />
            <button
            type="button"
            className="btn btn-outline-secondary settings-button"
            onClick={() => {
              setSettingsError(null);
              setSettingsOpen(true);
            }}
            title="抽出の観点とお手本を設定する"
          >
            <span aria-hidden="true">⚙</span>
            <span className="ms-1">設定</span>
            {(personaPrompt || savedExampleCount > 0) && (
              <span className="settings-dot" aria-hidden="true" />
            )}
            </button>
          </div>
        </div>
      </header>

      <main className="container app-main">
        <UrlForm
          value={urlText}
          loading={loading}
          maxUrls={maxUrls}
          personaPrompt={personaPrompt}
          exampleCount={savedExampleCount}
          onChange={setUrlText}
          onClear={() => setUrlText('')}
          onSubmit={handleSubmit}
          onCancel={handleCancel}
          onOpenSettings={() => setSettingsOpen(true)}
        />

        {globalError && (
          <div className="alert alert-danger mt-4" role="alert">
            <strong className="d-block mb-1">処理を実行できませんでした</strong>
            <span className="small">{globalError}</span>
          </div>
        )}

        {loading && <LoadingPanel count={submittedCount} />}

        {!loading && (
          <ResultList
            results={results}
            examples={examples}
            busyUrl={exampleBusyUrl}
            onToggleExample={handleToggleExample}
          />
        )}
      </main>

      <footer className="app-footer">
        <div className="container">
          <span>
            LLM は文章の生成・要約・言い換えを行いません。出力は必ず元ページに存在する文章です。
          </span>
        </div>
      </footer>

      <SettingsModal
        open={settingsOpen}
        prompt={personaPrompt}
        presets={presets}
        maxPresets={maxPresets}
        examples={examples}
        maxExamples={maxExamples}
        error={settingsError}
        busy={settingsBusy}
        onPromptChange={setPersonaPrompt}
        onSavePreset={handleSavePreset}
        onDeletePreset={handleDeletePreset}
        onDeleteExample={handleDeleteExample}
        onClose={() => void closeSettings()}
      />
    </div>
  );
}
