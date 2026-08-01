import { useCallback, useEffect, useRef, useState } from 'react';
import { copyText } from '../utils/clipboard';

interface CopyButtonProps {
  /** コピーする文字列。空なら押せない。 */
  text: string;
  /** ボタンの説明 (例: 「キャッチフレーズ 1」)。 */
  label: string;
  /** ラベルを表示するか (false ならアイコンのみ)。 */
  showLabel?: boolean;
}

type State = 'idle' | 'copied' | 'failed';

const FEEDBACK_MS = 1600;

/** クリックでテキストをクリップボードにコピーするボタン。 */
export function CopyButton({ text, label, showLabel = false }: CopyButtonProps) {
  const [state, setState] = useState<State>('idle');
  const timerRef = useRef<number | null>(null);

  // 表示が戻る前にアンマウントされた場合に備えて後片付けする。
  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  const handleClick = useCallback(async () => {
    const ok = await copyText(text);
    setState(ok ? 'copied' : 'failed');
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
    }
    timerRef.current = window.setTimeout(() => setState('idle'), FEEDBACK_MS);
  }, [text]);

  const caption =
    state === 'copied' ? 'コピーしました' : state === 'failed' ? 'コピーできません' : 'コピー';

  return (
    <button
      type="button"
      className={`btn btn-sm copy-button ${
        state === 'copied'
          ? 'btn-success'
          : state === 'failed'
            ? 'btn-outline-danger'
            : 'btn-outline-secondary'
      }`}
      onClick={() => void handleClick()}
      disabled={!text}
      title={`${label}をコピー`}
      aria-label={`${label}をコピー`}
    >
      <span aria-hidden="true">{state === 'copied' ? '✓' : '⧉'}</span>
      {(showLabel || state !== 'idle') && <span className="ms-1 copy-button-text">{caption}</span>}
    </button>
  );
}
