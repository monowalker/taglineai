/** お手本の同一性判定。バックエンド (store._example_key) と同じ規則。 */

import type { Example } from '../api/types';

/** 空白の違いを無視した内容キー。 */
export function exampleKey(title: string, line2: string, line3: string): string {
  return `${title} ${line2} ${line3}`.replace(/\s+/g, '');
}

/** 同じ内容のお手本が既に登録されていれば返す。 */
export function findExample(
  examples: Example[],
  title: string,
  line2: string,
  line3: string,
): Example | undefined {
  const key = exampleKey(title, line2, line3);
  return examples.find((example) => exampleKey(example.title, example.line2, example.line3) === key);
}
