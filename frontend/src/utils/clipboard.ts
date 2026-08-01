/** クリップボードへのコピー。 */

/**
 * テキストをクリップボードにコピーする。
 *
 * Clipboard API は「安全なコンテキスト」(https か localhost) でしか使えない。
 * 社内 IP (`http://192.168.x.x:8090`) などで開いた場合はそちらが使えないので、
 * 旧来の `execCommand('copy')` にフォールバックする。
 *
 * @returns コピーできたら true
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) {
    return false;
  }

  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 権限が拒否された場合などは下のフォールバックを試す。
    }
  }

  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    // 画面に見えないが選択はできる位置に置く (スクロール位置も動かさない)。
    area.style.position = 'fixed';
    area.style.top = '0';
    area.style.left = '0';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand('copy');
    document.body.removeChild(area);
    return copied;
  } catch {
    return false;
  }
}
