"""前処理 — 抽出したテキストを LLM に渡せる候補文の列に整える。

やること:
  - 残留 HTML タグ・実体参照の除去
  - 改行整理 / 空白整理
  - 文分割 (日本語の句点・改行を境界にする)
  - 長すぎる文章の分割
  - 重複削除
  - ナビゲーション・定型文などのノイズ除去

**文字そのものは書き換えない。** 空白の畳み込みと分割だけを行い、
出力される候補文は必ず元ページに存在する文字列と一致する。
"""

from __future__ import annotations

import html as html_lib
import re
import unicodedata

# 残留タグ (本文抽出後に混じることがある)
_TAG_RE = re.compile(r"<[^>]{0,4000}?>")
# 制御文字 (改行・タブを除く)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 行内の空白の連続 (全角スペース U+3000 を含む)
_INLINE_SPACE_RE = re.compile(r"[^\S\n]+")
# 3 行以上の空行
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
# 文末とみなす記号。閉じ括弧が続く場合はそこまでを 1 文に含める。
_SENTENCE_END_RE = re.compile(r"(?<=[。．！？!?])[」』）\)】〉》〕］\]]*")

# 「ですます」等が続く読点。長文分割時の切れ目候補。
_SOFT_BREAK_CHARS = "、，,；;："

# 日本語 (ひらがな・カタカナ・漢字) を 1 文字でも含むか
_JAPANESE_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿々ー]")

# 商品説明ではない定型文・ナビゲーション。部分一致で弾く。
_NOISE_PHRASES = (
    "カートに入れる",
    "カートを見る",
    "買い物かご",
    "ショッピングカート",
    "お気に入りに追加",
    "レビューを書く",
    "レビューはまだありません",
    "ログイン",
    "ログアウト",
    "会員登録",
    "新規登録",
    "マイページ",
    "会社概要",
    "運営会社",
    "特定商取引法",
    "プライバシーポリシー",
    "利用規約",
    "個人情報保護",
    "お問い合わせ",
    "サイトマップ",
    "よくある質問",
    "配送について",
    "送料無料キャンペーン",
    "クーポン",
    "ポイント還元",
    "在庫状況",
    "検索結果",
    "絞り込み",
    "並び替え",
    "前のページ",
    "次のページ",
    "トップページ",
    "javascript",
    "cookie",
    "クッキー",
    "ブラウザの設定",
    "all rights reserved",
    "copyright",
)

# 記号・数字・英数字ばかりの行 (価格・型番・パンくず等) を弾くための閾値
_MIN_JAPANESE_RATIO = 0.3

# --- 文の前後に付く販促ブロックの除去 ---------------------------------------
# EC サイトの説明文は「【あす楽】【ポイント10倍】新潟県魚沼産コシヒカリを使用。」
# のように、先頭へ販促の括弧が並ぶことが多い。丸ごと捨てると後半の正当な
# 商品説明まで失うので、**前後に付いた販促ブロックだけ**を剥がす。
#
# 削るのは先頭と末尾からだけなので、戻り値は必ず元の文字列の
# 連続した部分文字列になる (= 原文一致検証をそのまま通せる)。
_AFFIX_BRACKETS = (
    ("【", "】"),
    ("［", "］"),
    ("[", "]"),
    ("〔", "〕"),
    ("≪", "≫"),
    ("《", "》"),
    ("〈", "〉"),
    ("＜", "＞"),
    ("（", "）"),
    ("(", ")"),
    # 開き / 閉じが同じ記号で囲う装飾 (★ポイント10倍★ など)
    ("＼", "／"),
    ("★", "★"),
    ("☆", "☆"),
    ("■", "■"),
    ("◆", "◆"),
    ("●", "●"),
)

# 括弧ブロックを「販促」と判断する語。ここに無い括弧 (【新米】など) は残す。
# 商品説明の書き出しになりうる語 (限定 / ギフト / プレゼント等) は、
# 過剰に落とさないよう意図的に入れていない。
_PROMO_WORDS = (
    "送料無料", "送料込", "全国送料", "あす楽", "即日発送", "当日発送", "翌日配送",
    "ポイント", "pt", "クーポン", "セール", "sale", "off", "オフ", "割引", "値引",
    "特価", "激安", "半額", "最安", "お買い得", "買い回り", "エントリー",
    "楽天", "amazon", "yahoo", "ランキング", "1位", "第1位", "受賞", "殿堂",
    "在庫", "税込", "税抜", "円", "%", "％", "倍",
    "まとめ買い", "定期便", "会員限定", "先着", "抽選", "無料", "キャンペーン",
    "数量限定", "期間限定", "限定価格",
)

# 行頭行末に付く飾り記号。
_DECORATION_CHARS = "★☆■□◆◇●○▲△▼▽※＼／\\/｜|＊*＋+〜~ 　"

# --- 販促表現の検出 ---------------------------------------------------------
# 語彙リストと違い、こちらは形が決まっているので誤爆がほぼ無い。
# 「1,980円」「20%OFF」「ポイント5倍」を含む文は商品説明ではなく販促。
_PRICE_RE = re.compile(r"[0-9０-９][0-9０-９,，.．]*\s*円")
_PERCENT_OFF_RE = re.compile(r"[0-9０-９]+\s*[%％]\s*(off|オフ|引き?)", re.I)
_POINT_TIMES_RE = re.compile(r"(ポイント|ｐｔ|pt)\s*[0-9０-９]+\s*倍", re.I)


def strip_html(text: str) -> str:
    """残留 HTML タグと実体参照を除去する。"""
    if not text:
        return ""
    without_tags = _TAG_RE.sub(" ", text)
    return html_lib.unescape(without_tags)


def normalize_whitespace(text: str) -> str:
    """改行整理・空白整理。

    - 制御文字を除去
    - 行内の連続空白 (全角スペース含む) を半角スペース 1 つに畳む
    - 各行を strip
    - 3 行以上の空行を 2 行に畳む
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text)
    text = _INLINE_SPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def clean_text(text: str) -> str:
    """HTML タグ除去 → 空白整理 をまとめて行う。"""
    return normalize_whitespace(strip_html(text))


def split_sentences(text: str) -> list[str]:
    """テキストを文の列に分割する。

    改行を最優先の境界とし、行内は句点 (。．！？!?) で切る。
    """
    sentences: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        start = 0
        for match in _SENTENCE_END_RE.finditer(line):
            end = match.end()
            chunk = line[start:end].strip()
            if chunk:
                sentences.append(chunk)
            start = end
        tail = line[start:].strip()
        if tail:
            sentences.append(tail)
    return sentences


def split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    """長すぎる文を読点で分割する。

    読点が無ければ ``max_chars`` で機械的に切る。いずれの場合も
    連結すれば元の文字列に戻る (文字は追加も削除もしない)。
    """
    if len(sentence) <= max_chars:
        return [sentence]

    parts: list[str] = []
    remaining = sentence
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        # max_chars 以内で最後に現れる読点を切れ目にする。
        cut = max((window.rfind(ch) for ch in _SOFT_BREAK_CHARS), default=-1)
        if cut < max_chars // 3:
            # 読点が無い / 前すぎる場合は機械的に切る。
            cut = max_chars - 1
        parts.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1 :].strip()
    if remaining:
        parts.append(remaining)
    return [p for p in parts if p]


def _japanese_ratio(text: str) -> float:
    if not text:
        return 0.0
    # 空白と記号を除いた実質的な文字数で比率を取る。
    meaningful = [c for c in text if not c.isspace() and unicodedata.category(c)[0] != "P"]
    if not meaningful:
        return 0.0
    japanese = sum(1 for c in meaningful if _JAPANESE_RE.match(c))
    return japanese / len(meaningful)


def _contains_promo(text: str) -> bool:
    """括弧ブロックの中身が販促を表すか。"""
    lowered = text.lower()
    return any(word in lowered for word in _PROMO_WORDS)


def strip_promo_affixes(text: str) -> str:
    """文の**先頭・末尾**に付いた販促ブロックと飾り記号を取り除く。

    例:
        ``【あす楽】【ポイント10倍】新潟県魚沼産コシヒカリを使用。``
        → ``新潟県魚沼産コシヒカリを使用。``

    先頭と末尾からしか削らないので、戻り値は必ず元の文字列の連続した
    部分文字列になる。文の途中にある括弧は (途切れてしまうので) 触らない。
    販促語を含まない括弧 (``【新米】`` など) はそのまま残す。
    """
    if not text:
        return ""

    result = text.strip()
    changed = True
    while changed and result:
        changed = False

        # 括弧の対を先に見る。飾り記号を先に削ると ★ポイント10倍★ の
        # 開き記号が消えてしまい、対として認識できなくなる。
        for opening, closing in _AFFIX_BRACKETS:
            # 先頭の販促ブロック。開き記号の**次**から閉じ記号を探す
            # (開閉が同じ記号でも正しく対になるように)。
            if result.startswith(opening):
                end = result.find(closing, len(opening))
                if end > 0 and _contains_promo(result[len(opening) : end]):
                    result = result[end + len(closing) :].strip()
                    changed = True
                    break
            # 末尾の販促ブロック。閉じ記号の**手前**から開き記号を探す。
            if result.endswith(closing):
                start = result.rfind(opening, 0, len(result) - len(closing))
                if start >= 0 and _contains_promo(result[start + len(opening) : -len(closing)]):
                    result = result[:start].strip()
                    changed = True
                    break

        if changed:
            continue

        # 括弧の対で削れなくなったら、残った飾り記号を落とす。
        stripped = result.strip(_DECORATION_CHARS).strip()
        if stripped != result:
            result = stripped
            changed = True

    return result


def is_promotional(sentence: str) -> bool:
    """価格・割引・ポイントを含む、明らかに販促の文かどうか。

    語彙リストではなく形で判定するので誤爆が起きにくい。
    「1,980円」「20%OFF」「ポイント5倍」のような表現は、
    商品そのものの説明ではなく売り文句なのでキャッチフレーズに使わない。
    """
    if _PRICE_RE.search(sentence):
        return True
    if _PERCENT_OFF_RE.search(sentence):
        return True
    if _POINT_TIMES_RE.search(sentence):
        return True
    return False


def is_noise(sentence: str) -> bool:
    """商品説明として使えない文かどうか。"""
    lowered = sentence.lower()
    if any(phrase in lowered for phrase in _NOISE_PHRASES):
        return True
    if is_promotional(sentence):
        return True
    if not _JAPANESE_RE.search(sentence):
        # 日本語のみを返す要件があるため、日本語を含まない文は候補にしない。
        return True
    if _japanese_ratio(sentence) < _MIN_JAPANESE_RATIO:
        return True
    # 「・」「|」「>」区切りのメニュー行を弾く。
    if sentence.count("|") >= 3 or sentence.count("＞") >= 2 or sentence.count(">") >= 3:
        return True
    return False


def dedupe(sentences: list[str]) -> list[str]:
    """重複削除。

    比較は「空白を除き NFKC 正規化して小文字化した形」で行い、
    残す文字列は元のまま。表記ゆれ由来の重複も 1 つに畳める。
    """
    seen: set[str] = set()
    result: list[str] = []
    for sentence in sentences:
        key = dedupe_key(sentence)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(sentence)
    return result


def normalize_for_match(text: str) -> str:
    """比較用の正規化。

    空白を全て落とし、NFKC で全角/半角の揺れを吸収し、小文字化する。
    **比較にのみ使う** 表現で、採用する文字列は常に原文のまま。

    重複判定 (dedupe) と原文一致検証 (verifier) で同じ規則を使う必要があるので、
    実装はここ 1 箇所に置く。
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(normalized.split())


# 重複判定でも同じ正規化を使う (別実装にすると規則がずれる)。
dedupe_key = normalize_for_match


def build_candidates(
    *,
    body: str,
    descriptions: list[str] | None = None,
    min_chars: int = 8,
    max_chars: int = 140,
    limit: int = 120,
) -> list[str]:
    """本文と description から候補文の列を組み立てる。

    description を先頭に置くのは、商品ページでは meta description に
    最も凝縮された紹介文が入っていることが多く、キャッチフレーズとして
    有力な候補だから。いずれも HTML 内に実在する文字列である。

    Args:
        body:         抽出済みの本文テキスト。
        descriptions: meta description / og:description。
        min_chars:    候補として採用する最小文字数。
        max_chars:    これを超える文は分割する。
        limit:        候補文の最大件数。

    Returns:
        重複・ノイズを除いた候補文のリスト (元の文字列そのまま)。
    """
    raw_sentences: list[str] = []

    for description in descriptions or []:
        raw_sentences.extend(split_sentences(clean_text(description)))

    raw_sentences.extend(split_sentences(clean_text(body)))

    # 先頭・末尾の販促ブロックを剥がしてから長さで分割する。
    # 先に剥がさないと「【ポイント10倍】新潟県魚沼産コシヒカリを使用。」が
    # 販促文とみなされ、後半の正当な商品説明ごと捨てられてしまう。
    stripped = [strip_promo_affixes(s) for s in raw_sentences]

    expanded: list[str] = []
    for sentence in stripped:
        if not sentence:
            continue
        expanded.extend(split_long_sentence(sentence, max_chars))

    filtered = [s for s in expanded if len(s) >= min_chars and not is_noise(s)]
    return dedupe(filtered)[:limit]


def join_for_prompt(candidates: list[str], max_chars: int) -> str:
    """候補文を番号付きでプロンプト用に整形する。

    番号を振るのは、LLM が「新しく書く」のではなく「並んでいる文から選ぶ」
    という作業だと理解しやすくするため。合計文字数が上限を超えたら打ち切る。
    """
    lines: list[str] = []
    total = 0
    for index, sentence in enumerate(candidates, start=1):
        line = f"{index}. {sentence}"
        if total + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)
