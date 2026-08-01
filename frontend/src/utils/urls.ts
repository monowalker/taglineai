/** URL 入力欄のテキストを扱うユーティリティ。 */

/** 改行区切りのテキストを URL の配列にする (空行は除去)。 */
export function parseUrls(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/** http(s) の URL として妥当か (サーバ側でも検証するが、入力時に気づけるように)。 */
export function isValidHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

/** 妥当でない行だけを返す。 */
export function findInvalidUrls(urls: string[]): string[] {
  return urls.filter((url) => !isValidHttpUrl(url));
}

/** 表示用にホスト名を取り出す (取れなければ元の文字列)。 */
export function toHostname(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}
