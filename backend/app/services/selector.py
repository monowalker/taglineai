"""ヒューリスティックによる候補文の選択。

LLM が落ちている / 応答が壊れている / 出力が原文と一致しない、といった場合の
フォールバック。ここでも文章は一切生成せず、候補文から**選ぶ**だけ。
"""

from __future__ import annotations

import re

from app.services.verifier import normalize_for_match

# 商品の魅力・特徴を語る文に現れやすい語。加点に使う。
_APPEAL_WORDS = (
    "おいしい", "美味しい", "うまい", "旨味", "風味", "香り", "食感",
    "人気", "話題", "評判", "定番", "名物", "自慢", "看板",
    "厳選", "こだわり", "こだわっ", "選び抜", "吟味",
    "贅沢", "上質", "最高", "極上", "特別", "希少", "限定",
    "職人", "老舗", "伝統", "手作り", "手づくり", "自家製",
    "産", "国産", "天然", "無添加", "有機", "オーガニック",
    "品質", "安心", "安全", "新鮮", "獲れたて", "採れたて",
    "おすすめ", "ぴったり", "喜ば", "贈り物", "ギフト", "プレゼント",
    "特徴", "使用", "採用", "仕上げ", "製法", "配合", "設計", "機能",
)

# 商品説明として弱い文 (説明ではなく事務連絡) に現れる語。減点に使う。
_WEAK_WORDS = (
    "円", "税込", "税抜", "発送", "配送", "納期", "在庫", "注文",
    "返品", "交換", "キャンセル", "支払", "決済", "銀行", "クレジット",
    "営業日", "定休日", "電話", "メール", "住所",
)

# 数字・記号が主体の文を弾くための判定
_DIGIT_RE = re.compile(r"[0-9０-９]")

# 読みやすい 1 文の目安 (文字数)
_IDEAL_MIN = 15
_IDEAL_MAX = 80


def score_sentence(sentence: str, position: int) -> float:
    """候補文の「商品説明らしさ」を点数化する。

    Args:
        sentence: 候補文。
        position: 候補リスト内の位置 (先頭ほど有力とみなす)。
    """
    length = len(sentence)
    score = 0.0

    # 長さ: 理想範囲に入っていれば加点、外れるほど減点。
    if _IDEAL_MIN <= length <= _IDEAL_MAX:
        score += 3.0
    elif length < _IDEAL_MIN:
        score -= (_IDEAL_MIN - length) * 0.2
    else:
        score -= (length - _IDEAL_MAX) * 0.05

    score += sum(1.5 for word in _APPEAL_WORDS if word in sentence)
    score -= sum(1.5 for word in _WEAK_WORDS if word in sentence)

    # 数字比率が高い文 (スペック表・価格) は説明文として弱い。
    digits = len(_DIGIT_RE.findall(sentence))
    if length and digits / length > 0.25:
        score -= 3.0

    # 文として完結しているものを優先。
    if sentence.endswith(("。", "．", "！", "？", "!", "?")):
        score += 1.0

    # 前方 (description や本文冒頭) ほど有力。
    score -= position * 0.05

    return score


def rank_candidates(candidates: list[str]) -> list[str]:
    """候補文をスコア降順に並べ替える。"""
    scored = [
        (score_sentence(sentence, index), index, sentence)
        for index, sentence in enumerate(candidates)
    ]
    # スコア同点は元の並び順を保つ。
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [sentence for _, _, sentence in scored]


def pick_best(candidates: list[str], exclude: list[str] | None = None) -> str:
    """除外リストと重複しない最良の候補文を 1 つ返す。

    見つからなければ空文字を返す (文章を作ることはしない)。
    """
    excluded_keys = {normalize_for_match(text) for text in (exclude or []) if text}
    for sentence in rank_candidates(candidates):
        key = normalize_for_match(sentence)
        if key and key not in excluded_keys:
            return sentence
    return ""
