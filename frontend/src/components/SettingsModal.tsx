import { useEffect, useState } from 'react';
import type { Example, PersonaPreset } from '../api/types';

interface SettingsModalProps {
  open: boolean;
  /** 現在使用中の観点 (自由入力)。 */
  prompt: string;
  presets: PersonaPreset[];
  maxPresets: number;
  examples: Example[];
  maxExamples: number;
  error: string | null;
  busy: boolean;
  onPromptChange: (prompt: string) => void;
  onSavePreset: (name: string, prompt: string) => void;
  onDeletePreset: (id: string) => void;
  onDeleteExample: (id: string) => void;
  onClose: () => void;
}

/**
 * 設定ダイアログ。
 *
 * - 観点(プロンプト): 保存済みプリセットから選ぶ / 自由に入力する / 入力内容を保存する
 * - お手本: 👍 で貯めた抽出例の確認と削除
 *
 * Bootstrap の JS は読み込んでいないため、表示制御は React 側で行う。
 */
export function SettingsModal({
  open,
  prompt,
  presets,
  maxPresets,
  examples,
  maxExamples,
  error,
  busy,
  onPromptChange,
  onSavePreset,
  onDeletePreset,
  onDeleteExample,
  onClose,
}: SettingsModalProps) {
  const [presetName, setPresetName] = useState('');

  // Esc で閉じられるようにする。
  useEffect(() => {
    if (!open) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  const savedExamples = examples.filter((example) => !example.builtin);
  const canSavePreset =
    presetName.trim().length > 0 && prompt.trim().length > 0 && presets.length < maxPresets && !busy;

  const handleSavePreset = () => {
    if (!canSavePreset) {
      return;
    }
    onSavePreset(presetName.trim(), prompt.trim());
    setPresetName('');
  };

  return (
    <>
      <div className="modal-backdrop show" onClick={onClose} />
      <div
        className="modal d-block"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
      >
        <div className="modal-dialog modal-dialog-scrollable modal-lg modal-dialog-centered">
          <div className="modal-content app-card">
            <div className="modal-header">
              <h2 className="modal-title h5 mb-0" id="settings-title">
                設定
              </h2>
              <button type="button" className="btn-close" aria-label="閉じる" onClick={onClose} />
            </div>

            <div className="modal-body">
              {error && (
                <div className="alert alert-danger py-2" role="alert">
                  {error}
                </div>
              )}

              {/* ---- 観点 ---- */}
              <section className="settings-section">
                <h3 className="settings-heading">抽出の観点</h3>
                <p className="text-secondary small">
                  どんな文を選んでほしいかを書きます（例: 明るい感じ / お年寄り向け）。
                  <strong className="text-body">
                    観点は「どの文を選ぶか」にだけ使われ、文章の書き換えは行いません。
                  </strong>
                </p>

                <label htmlFor="persona-prompt" className="form-label small fw-semibold">
                  現在の観点
                </label>
                <textarea
                  id="persona-prompt"
                  className="form-control"
                  rows={3}
                  placeholder="例: 明るく前向きな印象の文を選んでください。専門用語は避けてください。"
                  value={prompt}
                  disabled={busy}
                  onChange={(event) => onPromptChange(event.target.value)}
                />
                <div className="d-flex flex-wrap gap-2 align-items-center mt-2">
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-secondary"
                    onClick={() => onPromptChange('')}
                    disabled={busy || prompt.length === 0}
                  >
                    観点をクリア
                  </button>
                  <span className="text-secondary small">
                    空にすると観点なしで抽出します。
                  </span>
                </div>

                {/* プリセット保存 */}
                <div className="settings-subsection">
                  <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
                    <span className="fw-semibold small">保存済みプリセット</span>
                    <span className="badge rounded-pill text-bg-light border fw-normal">
                      {presets.length} / {maxPresets}
                    </span>
                  </div>

                  {presets.length === 0 ? (
                    <p className="text-secondary small mb-2">まだ保存されていません。</p>
                  ) : (
                    <ul className="preset-list">
                      {presets.map((preset) => {
                        const active = preset.prompt === prompt;
                        return (
                          <li key={preset.id} className={`preset-item ${active ? 'active' : ''}`}>
                            <div className="min-w-0">
                              <div className="preset-name">
                                {preset.name}
                                {active && (
                                  <span className="badge rounded-pill text-bg-primary fw-normal ms-2">
                                    使用中
                                  </span>
                                )}
                              </div>
                              <div className="preset-prompt">{preset.prompt}</div>
                            </div>
                            <div className="d-flex gap-2 flex-shrink-0">
                              <button
                                type="button"
                                className="btn btn-sm btn-outline-primary"
                                onClick={() => onPromptChange(preset.prompt)}
                                disabled={busy || active}
                              >
                                使う
                              </button>
                              <button
                                type="button"
                                className="btn btn-sm btn-outline-danger"
                                onClick={() => onDeletePreset(preset.id)}
                                disabled={busy}
                              >
                                削除
                              </button>
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  )}

                  <div className="row g-2 mt-2 align-items-center">
                    <div className="col-12 col-sm">
                      <input
                        type="text"
                        className="form-control form-control-sm"
                        placeholder="プリセット名（例: 明るい / シニア向け）"
                        maxLength={40}
                        value={presetName}
                        disabled={busy || presets.length >= maxPresets}
                        onChange={(event) => setPresetName(event.target.value)}
                      />
                    </div>
                    <div className="col-12 col-sm-auto">
                      <button
                        type="button"
                        className="btn btn-sm btn-primary w-100"
                        onClick={handleSavePreset}
                        disabled={!canSavePreset}
                      >
                        現在の観点を保存
                      </button>
                    </div>
                  </div>
                  {presets.length >= maxPresets && (
                    <div className="form-text">
                      保存できるのは {maxPresets} 件までです。不要なものを削除してください。
                    </div>
                  )}
                </div>
              </section>

              {/* ---- お手本 ---- */}
              <section className="settings-section">
                <div className="d-flex flex-wrap align-items-center gap-2">
                  <h3 className="settings-heading mb-0">お手本</h3>
                  <span className="badge rounded-pill text-bg-light border fw-normal">
                    {savedExamples.length} / {maxExamples}
                  </span>
                </div>
                <p className="text-secondary small mt-2">
                  結果カードの 👍 を押すと、そのときの抽出結果がお手本として貯まり、
                  次回以降の抽出でLLMの参考になります。
                  {maxExamples} 件を超えると古いものから自動的に消えます。
                </p>

                <ul className="example-list">
                  {examples.map((example) => (
                    <li key={example.id} className="example-item">
                      <div className="min-w-0">
                        <div className="example-title">
                          {example.title}
                          {example.builtin && (
                            <span className="badge rounded-pill text-bg-light border fw-normal ms-2">
                              組み込み
                            </span>
                          )}
                        </div>
                        {[example.line2, example.line3]
                          .filter(Boolean)
                          .map((part, index) => (
                            <div key={`${example.id}-${index}`} className="example-part">
                              {part}
                            </div>
                          ))}
                      </div>
                      {!example.builtin && (
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-danger flex-shrink-0"
                          onClick={() => onDeleteExample(example.id)}
                          disabled={busy}
                        >
                          削除
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            </div>

            <div className="modal-footer">
              <span className="text-secondary small me-auto">
                プリセットの保存・削除は即時反映されます。観点は閉じると保存されます。
              </span>
              <button type="button" className="btn btn-primary" onClick={onClose} disabled={busy}>
                閉じる
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
