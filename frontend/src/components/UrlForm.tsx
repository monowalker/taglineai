import { useMemo } from 'react';
import type { FormEvent } from 'react';
import { findInvalidUrls, parseUrls } from '../utils/urls';

const PLACEHOLDER = ['https://aaa.com', 'https://bbb.com', 'https://ccc.com'].join('\n');

interface UrlFormProps {
  value: string;
  loading: boolean;
  maxUrls: number;
  /** 現在の観点 (空なら未指定)。 */
  personaPrompt: string;
  /** 👍 で貯まっているお手本の件数。 */
  exampleCount: number;
  onChange: (value: string) => void;
  /** 入力欄を空にする。 */
  onClear: () => void;
  onSubmit: (urls: string[]) => void;
  onCancel: () => void;
  onOpenSettings: () => void;
}

/** URL 入力フォーム。改行区切りで複数 URL を受け付ける。 */
export function UrlForm({
  value,
  loading,
  maxUrls,
  personaPrompt,
  exampleCount,
  onChange,
  onClear,
  onSubmit,
  onCancel,
  onOpenSettings,
}: UrlFormProps) {
  const urls = useMemo(() => parseUrls(value), [value]);
  const invalidUrls = useMemo(() => findInvalidUrls(urls), [urls]);

  const tooMany = urls.length > maxUrls;
  const canSubmit = !loading && urls.length > 0 && invalidUrls.length === 0 && !tooMany;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (canSubmit) {
      onSubmit(urls);
    }
  };

  return (
    <form className="card app-card" onSubmit={handleSubmit} noValidate>
      <div className="card-body">
        <div className="d-flex align-items-center gap-2 mb-1">
          <label htmlFor="urls" className="form-label fw-semibold mb-0">
            商品ページの URL
            <span className="text-secondary fw-normal ms-2 small">（1 行に 1 件）</span>
          </label>
          <button
            type="button"
            className="btn btn-sm btn-outline-secondary ms-auto"
            onClick={onClear}
            disabled={loading || value.length === 0}
            title="入力した URL をすべて消す"
          >
            クリア
          </button>
        </div>

        <textarea
          id="urls"
          className="form-control url-textarea"
          rows={6}
          spellCheck={false}
          placeholder={PLACEHOLDER}
          value={value}
          disabled={loading}
          onChange={(event) => onChange(event.target.value)}
          aria-describedby="urls-help"
        />

        <div id="urls-help" className="form-text d-flex flex-wrap gap-3 mt-2">
          <span>
            入力件数: <strong>{urls.length}</strong> / {maxUrls}
          </span>
          <span className="text-secondary">
            HTML 内に存在する文章のみを抽出します（生成・要約は行いません）
          </span>
        </div>

        {/* 現在の抽出条件を、設定を開かなくても確認できるようにする。 */}
        <div className="condition-bar">
          <button
            type="button"
            className="btn btn-sm btn-link p-0 text-decoration-none"
            onClick={onOpenSettings}
            disabled={loading}
          >
            観点:
          </button>
          {personaPrompt ? (
            <span className="condition-value" title={personaPrompt}>
              {personaPrompt}
            </span>
          ) : (
            <span className="text-secondary">未指定</span>
          )}
          <span className="text-secondary">／ お手本 {exampleCount} 件</span>
        </div>

        {invalidUrls.length > 0 && (
          <div className="alert alert-warning mt-3 mb-0 py-2" role="alert">
            <strong>URL の形式が正しくありません。</strong>
            <ul className="mb-0 mt-1 small">
              {invalidUrls.slice(0, 5).map((url) => (
                <li key={url} className="text-break">
                  {url}
                </li>
              ))}
              {invalidUrls.length > 5 && <li>ほか {invalidUrls.length - 5} 件</li>}
            </ul>
          </div>
        )}

        {tooMany && (
          <div className="alert alert-warning mt-3 mb-0 py-2" role="alert">
            一度に指定できる URL は {maxUrls} 件までです。
          </div>
        )}
      </div>

      <div className="card-footer bg-white border-top-0 pt-0 pb-4 px-4 d-flex flex-wrap gap-2">
        <button type="submit" className="btn btn-primary px-4" disabled={!canSubmit}>
          {loading ? (
            <>
              <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
              抽出中…
            </>
          ) : (
            '抽出開始'
          )}
        </button>

        {loading && (
          <button type="button" className="btn btn-outline-secondary" onClick={onCancel}>
            中断
          </button>
        )}
      </div>
    </form>
  );
}
