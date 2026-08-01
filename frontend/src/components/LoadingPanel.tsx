interface LoadingPanelProps {
  count: number;
}

/**
 * 抽出中の待機表示。
 * ローカル LLM は 1 件あたり数十秒かかることがあるため、
 * 「止まっていない」ことが伝わるよう処理の流れも見せる。
 */
export function LoadingPanel({ count }: LoadingPanelProps) {
  const steps = ['HTML 取得', '本文抽出', 'ノイズ除去', 'LLM で選択', '原文照合'];

  return (
    <div className="card app-card mt-4" role="status" aria-live="polite">
      <div className="card-body text-center py-5">
        <div className="spinner-border text-primary mb-3" aria-hidden="true" />
        <p className="mb-1 fw-semibold">抽出しています…（{count} 件）</p>
        <p className="text-secondary small mb-3">
          ローカル LLM の処理には時間がかかることがあります。このままお待ちください。
        </p>
        <div className="d-flex flex-wrap justify-content-center gap-2">
          {steps.map((step) => (
            <span key={step} className="badge rounded-pill text-bg-light border fw-normal">
              {step}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
