import { useMemo, useState } from 'react';
import type { ExtractResult, MatchMethod } from '../api/types';
import { toHostname } from '../utils/urls';
import { CopyButton } from './CopyButton';

interface ResultCardProps {
  result: ExtractResult;
  index: number;
  /** お手本に登録済みか (👍 の状態)。 */
  bookmarked: boolean;
  /** 👍 の処理中はボタンを無効化する。 */
  busy: boolean;
  onToggleExample: (result: ExtractResult) => void;
}

/** 原文一致の方式を、利用者に意味が伝わるラベルにする。 */
const MATCH_LABEL: Record<MatchMethod, { text: string; className: string }> = {
  exact: { text: '原文一致', className: 'text-bg-success' },
  contained: { text: '原文一致', className: 'text-bg-success' },
  fuzzy: { text: '原文に補正', className: 'text-bg-info' },
  nearest: { text: '近い原文に置換', className: 'text-bg-info' },
  unverified: { text: '本文から自動選択', className: 'text-bg-warning' },
  none: { text: '該当なし', className: 'text-bg-secondary' },
};

/** 空白の違いを無視した比較キー。 */
function compareKey(text: string): string {
  return text.replace(/\s+/g, '');
}

function MatchBadge({ method }: { method: MatchMethod | null }) {
  if (!method) {
    return null;
  }
  const label = MATCH_LABEL[method];
  return (
    <span
      className={`badge rounded-pill ${label.className} fw-normal`}
      title="原文との突き合わせ結果"
    >
      {label.text}
    </span>
  );
}

function Line({
  label,
  text,
  method,
}: {
  label: string;
  text: string;
  method: MatchMethod | null;
}) {
  return (
    <div className="result-line">
      <div className="d-flex align-items-center gap-2 mb-1">
        <span className="result-line-label">{label}</span>
        <MatchBadge method={method} />
        <span className="ms-auto">
          <CopyButton text={text} label={label} />
        </span>
      </div>
      {text ? (
        <p className="result-line-text mb-0">{text}</p>
      ) : (
        <p className="result-line-text text-secondary fst-italic mb-0">
          該当する文章が見つかりませんでした
        </p>
      )}
    </div>
  );
}

/** URL 1 件分の結果カード。 */
export function ResultCard({ result, index, bookmarked, busy, onToggleExample }: ResultCardProps) {
  const failed = Boolean(result.error);
  const [showCandidates, setShowCandidates] = useState(false);

  /** 候補文のうち、実際に採用されたものを判別するためのキー集合。 */
  const selectedKeys = useMemo(
    () => new Set([result.line2, result.line3].filter(Boolean).map(compareKey)),
    [result.line2, result.line3],
  );

  const candidates = result.meta.candidates;

  /** カード全体をまとめてコピーするときの文字列。 */
  const wholeText = [result.title, result.line2, result.line3].filter(Boolean).join('\n');

  return (
    <article className={`card app-card result-card ${failed ? 'result-card-error' : ''}`}>
      <div className="card-body">
        <header className="result-header">
          <span className="result-index">{index + 1}</span>
          <div className="min-w-0 flex-grow-1">
            <a
              className="result-url text-break"
              href={result.url}
              target="_blank"
              rel="noreferrer noopener"
              title={result.url}
            >
              {result.url}
            </a>
            <div className="text-secondary small">{toHostname(result.url)}</div>
          </div>

          {!failed && (
            <div className="d-flex gap-2 flex-shrink-0">
              <CopyButton text={wholeText} label="抽出結果すべて" showLabel />
              <button
                type="button"
                className={`btn btn-sm thumb-button ${bookmarked ? 'btn-primary' : 'btn-outline-secondary'}`}
                onClick={() => onToggleExample(result)}
                disabled={busy}
                aria-pressed={bookmarked}
                title={
                  bookmarked
                    ? 'お手本から外す'
                    : 'この結果をお手本に追加する (次回以降の抽出の参考になります)'
                }
              >
                <span aria-hidden="true">👍</span>
                <span className="ms-1 d-none d-sm-inline">
                  {bookmarked ? 'お手本に登録済み' : 'お手本にする'}
                </span>
              </button>
            </div>
          )}
        </header>

        {failed ? (
          <div className="alert alert-danger mb-0 mt-3" role="alert">
            <div className="fw-semibold mb-1">抽出に失敗しました</div>
            <div className="small">{result.error}</div>
            {result.error_code && (
              <div className="small text-danger-emphasis mt-1">
                エラーコード: <code>{result.error_code}</code>
              </div>
            )}
          </div>
        ) : (
          <>
            <div className="result-line mt-3">
              <div className="d-flex align-items-center gap-2 mb-1">
                <span className="result-line-label">商品名</span>
                <span className="ms-auto">
                  <CopyButton text={result.title} label="商品名" />
                </span>
              </div>
              {result.title ? (
                <p className="result-title mb-0">{result.title}</p>
              ) : (
                <p className="result-line-text text-secondary fst-italic mb-0">
                  商品名が見つかりませんでした
                </p>
              )}
            </div>

            <Line label="キャッチフレーズ 1" text={result.line2} method={result.meta.line2_match} />
            <Line label="キャッチフレーズ 2" text={result.line3} method={result.meta.line3_match} />

            <footer className="result-meta">
              {result.meta.candidate_count !== null && (
                <button
                  type="button"
                  className="btn btn-sm btn-link result-meta-toggle"
                  onClick={() => setShowCandidates((open) => !open)}
                  aria-expanded={showCandidates}
                  disabled={candidates.length === 0}
                  title="LLM に渡した候補文を表示する"
                >
                  候補文: {result.meta.candidate_count}
                  {candidates.length > 0 && (
                    <span aria-hidden="true"> {showCandidates ? '▲' : '▼'}</span>
                  )}
                </button>
              )}
              {result.meta.llm_model_label && <span>モデル: {result.meta.llm_model_label}</span>}
              {result.meta.title_source && <span>商品名: {result.meta.title_source}</span>}
              {result.meta.extractor && <span>抽出: {result.meta.extractor}</span>}
              {result.meta.persona_applied && <span>観点あり</span>}
              {result.meta.fetch_ms !== null && (
                <span>HTML取得: {Math.round(result.meta.fetch_ms)}ms</span>
              )}
              {result.meta.llm_ms !== null && <span>LLM: {Math.round(result.meta.llm_ms)}ms</span>}
              {result.meta.total_ms !== null && (
                <span>合計: {(result.meta.total_ms / 1000).toFixed(1)}s</span>
              )}
            </footer>

            {showCandidates && candidates.length > 0 && (
              <section className="candidate-panel" aria-label="LLM に渡した候補文">
                <p className="candidate-panel-note">
                  ページから抽出し、LLM に選択肢として渡した文です。
                  <strong>採用されたもの</strong>には ★ が付いています。
                </p>
                <ol className="candidate-list">
                  {candidates.map((candidate, position) => {
                    const chosen = selectedKeys.has(compareKey(candidate));
                    return (
                      <li
                        key={`${position}-${candidate}`}
                        className={`candidate-item ${chosen ? 'chosen' : ''}`}
                      >
                        <span className="candidate-mark" aria-hidden="true">
                          {chosen ? '★' : ''}
                        </span>
                        <span className="candidate-text">{candidate}</span>
                        <CopyButton text={candidate} label="候補文" />
                      </li>
                    );
                  })}
                </ol>
              </section>
            )}
          </>
        )}
      </div>
    </article>
  );
}
