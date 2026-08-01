import type { Example, ExtractResult } from '../api/types';
import { findExample } from '../utils/examples';
import { ResultCard } from './ResultCard';

interface ResultListProps {
  results: ExtractResult[];
  examples: Example[];
  busyUrl: string | null;
  onToggleExample: (result: ExtractResult) => void;
}

/** 抽出結果の一覧。成功件数・失敗件数のサマリも出す。 */
export function ResultList({ results, examples, busyUrl, onToggleExample }: ResultListProps) {
  if (results.length === 0) {
    return null;
  }

  const failed = results.filter((result) => result.error).length;
  const succeeded = results.length - failed;

  return (
    <section className="mt-4" aria-label="抽出結果">
      <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
        <h2 className="h5 mb-0 me-2">抽出結果</h2>
        <span className="badge rounded-pill text-bg-light border">全 {results.length} 件</span>
        {succeeded > 0 && (
          <span className="badge rounded-pill text-bg-success fw-normal">成功 {succeeded}</span>
        )}
        {failed > 0 && (
          <span className="badge rounded-pill text-bg-danger fw-normal">失敗 {failed}</span>
        )}
      </div>

      <div className="d-flex flex-column gap-3">
        {results.map((result, index) => (
          <ResultCard
            key={`${result.url}-${index}`}
            result={result}
            index={index}
            bookmarked={Boolean(
              findExample(examples, result.title, result.line2, result.line3),
            )}
            busy={busyUrl === result.url}
            onToggleExample={onToggleExample}
          />
        ))}
      </div>
    </section>
  );
}
