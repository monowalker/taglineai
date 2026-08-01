"""原文一致検証。

このアプリの中核となる安全装置。LLM は「選ぶ」役割しか与えられていないが、
LLM である以上、要約・言い換え・創作をしてしまう可能性が常にある。
そこで **LLM の出力をそのまま信用せず、必ず元ページの文字列に突き合わせる**。

判定は 4 段階:

    exact      候補文と完全一致        → 候補文をそのまま採用
    contained  元テキストの部分文字列  → 元テキストから該当箇所を切り出して採用
    fuzzy      候補文と高い類似度      → 類似した候補文の**原文**で置き換える
    none       いずれにも該当しない    → 生成された文とみなして棄却

fuzzy で「LLM の出力」ではなく「原文」を返すのが要点で、これにより
出力される文字列は常にページ内に実在する文字列になる。


比較用の正規化 (``normalize_for_match``) は :mod:`app.services.preprocess` の
実装をそのまま使う。重複判定と規則がずれると、候補文と検証で挙動が食い違うため。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.services.preprocess import normalize_for_match

MatchMethod = Literal["exact", "contained", "fuzzy", "nearest", "none"]

# fuzzy 判定の対象にする最小文字数。極端に短い文は誤マッチしやすいので弾く。
_MIN_FUZZY_CHARS = 6


@dataclass(frozen=True)
class MatchResult:
    """検証結果。

    Attributes:
        text:   採用すべき原文そのままの文字列 (棄却時は空文字)。
        method: 一致方式。
        ratio:  類似度 (exact/contained は 1.0)。
    """

    text: str
    method: MatchMethod
    ratio: float

    @property
    def ok(self) -> bool:
        return self.method != "none" and bool(self.text)


def _normalize_with_index_map(text: str) -> tuple[str, list[int]]:
    """正規化文字列と、その各文字が元テキストの何文字目由来かの対応表を作る。

    NFKC は 1 文字が複数文字に展開されることがあるため、展開後の各文字に
    同じ元インデックスを割り当てる。これで正規化後に見つけた一致範囲を
    元テキストの範囲へ戻せる。
    """
    chars: list[str] = []
    indices: list[int] = []
    for position, char in enumerate(text):
        if char.isspace():
            continue
        for expanded in unicodedata.normalize("NFKC", char).lower():
            chars.append(expanded)
            indices.append(position)
    return "".join(chars), indices


class VerbatimVerifier:
    """1 ページ分の原文を保持し、LLM 出力を検証する。"""

    def __init__(
        self,
        *,
        source_text: str,
        candidates: list[str],
        min_ratio: float = 0.75,
    ) -> None:
        """
        Args:
            source_text: ページから取り出した本文 (description 含む) の全文。
            candidates:  前処理済みの候補文。
            min_ratio:   fuzzy 一致とみなす最小類似度。
        """
        self._candidates = candidates
        self._min_ratio = min_ratio
        self._normalized_candidates = [normalize_for_match(c) for c in candidates]
        self._source_text = source_text
        self._normalized_source, self._source_index_map = _normalize_with_index_map(source_text)

    @property
    def candidates(self) -> list[str]:
        return self._candidates

    def resolve(self, text: str) -> MatchResult:
        """LLM が返した文字列を、原文そのままの文字列へ解決する。"""
        query = normalize_for_match(text)
        if not query:
            return MatchResult("", "none", 0.0)

        exact = self._match_exact(query)
        if exact is not None:
            return exact

        contained = self._match_contained(query)
        if contained is not None:
            return contained

        return self._match_fuzzy(query)

    def _match_exact(self, query: str) -> MatchResult | None:
        for original, normalized in zip(self._candidates, self._normalized_candidates):
            if normalized == query:
                return MatchResult(original, "exact", 1.0)
        return None

    def _match_contained(self, query: str) -> MatchResult | None:
        """LLM 出力が原文の部分文字列になっているケースを拾う。

        候補文の一部だけを返してきた場合 (原文の切り出しとしては正しい) と、
        候補文が LLM 出力に含まれるケース (複数文の連結) の両方を扱う。
        """
        # 1. 出力が元テキストにそのまま含まれる → 元テキストから切り出す。
        position = self._normalized_source.find(query)
        if position >= 0:
            start = self._source_index_map[position]
            end = self._source_index_map[position + len(query) - 1]
            original = self._source_text[start : end + 1].strip()
            if original:
                return MatchResult(original, "contained", 1.0)

        # 2. 候補文が出力に含まれる → 最も長い候補文を原文として採用する。
        best: tuple[int, str] | None = None
        for original, normalized in zip(self._candidates, self._normalized_candidates):
            if normalized and normalized in query:
                if best is None or len(normalized) > best[0]:
                    best = (len(normalized), original)
        if best is not None:
            return MatchResult(best[1], "contained", 1.0)

        return None

    def nearest(
        self,
        text: str,
        *,
        min_ratio: float,
        exclude: list[str] | None = None,
    ) -> MatchResult:
        """厳密な検証に通らなかった出力について、最も近い候補文を返す。

        LLM が原文を言い換えたときは、言い換え元の文が候補の中にあることが多い。
        ``resolve()`` の閾値に届かなくても、ある程度似た候補があるなら
        「LLM が狙っていた文」とみなして、その**候補文 (＝原文)** を採用する。

        返すのは常に候補文なので、ここを通っても LLM の生成文が出ることはない。

        Args:
            min_ratio: これを下回る候補しか無ければ棄却する。
            exclude:   既に採用済みの文 (重複を避ける)。

        Returns:
            method が ``"nearest"`` の結果。該当が無ければ ``"none"``。
        """
        query = normalize_for_match(text)
        if not query:
            return MatchResult("", "none", 0.0)

        excluded_keys = {normalize_for_match(t) for t in (exclude or []) if t}
        original, ratio = self._best_fuzzy(query, min_ratio, excluded_keys)
        if original:
            return MatchResult(original, "nearest", ratio)
        return MatchResult("", "none", ratio)

    # ---- 内部 ----

    def _best_fuzzy(
        self, query: str, min_ratio: float, excluded_keys: set[str] | None = None
    ) -> tuple[str, float]:
        """``min_ratio`` 以上で最も似ている候補文と、その類似度を返す。

        見つからなければ ``("", これまでの最高類似度)``。
        """
        if len(query) < _MIN_FUZZY_CHARS:
            return "", 0.0

        excluded_keys = excluded_keys or set()
        best_ratio = 0.0
        best_original = ""
        matcher = SequenceMatcher(autojunk=False)
        matcher.set_seq2(query)
        for original, normalized in zip(self._candidates, self._normalized_candidates):
            if not normalized or normalized in excluded_keys:
                continue
            # 長さから決まる ratio の上限 (SequenceMatcher.real_quick_ratio と同じ式)
            # が閾値に届かない候補は、比較するまでもなく不一致。
            longer, shorter = max(len(normalized), len(query)), min(len(normalized), len(query))
            if 2 * shorter / (longer + shorter) < min_ratio:
                continue
            matcher.set_seq1(normalized)
            ratio = matcher.ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_original = original

        if best_ratio >= min_ratio and best_original:
            return best_original, best_ratio
        return "", best_ratio

    def _match_fuzzy(self, query: str) -> MatchResult:
        original, ratio = self._best_fuzzy(query, self._min_ratio)
        if original:
            return MatchResult(original, "fuzzy", ratio)
        return MatchResult("", "none", ratio)
