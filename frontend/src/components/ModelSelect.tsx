import type { ModelInfo } from '../api/types';

interface ModelSelectProps {
  models: ModelInfo[];
  selectedId: string;
  disabled: boolean;
  onChange: (modelId: string) => void;
}

/** 使用する LLM を選ぶプルダウン。定義は models.yml。 */
export function ModelSelect({ models, selectedId, disabled, onChange }: ModelSelectProps) {
  if (models.length === 0) {
    return null;
  }

  const selected = models.find((model) => model.id === selectedId);

  return (
    <div className="model-select">
      <label htmlFor="model-select" className="model-select-label">
        モデル
      </label>
      <select
        id="model-select"
        className="form-select form-select-sm"
        value={selectedId}
        disabled={disabled || models.length < 2}
        onChange={(event) => onChange(event.target.value)}
        title={selected ? `${selected.model} @ ${selected.api_base}` : undefined}
      >
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
            {model.missing_env_key ? '（APIキー未設定）' : ''}
          </option>
        ))}
      </select>

      {selected?.missing_env_key && (
        <span
          className="badge rounded-pill text-bg-warning fw-normal"
          title="models.yml が参照している環境変数が設定されていません。抽出を実行すると失敗する可能性があります。"
        >
          APIキー未設定
        </span>
      )}
    </div>
  );
}
