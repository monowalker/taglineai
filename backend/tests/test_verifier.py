"""原文一致検証のテスト。

このアプリの「LLM に文章を作らせない」保証はここで担保される。
"""

from __future__ import annotations

import pytest

from app.services.verifier import VerbatimVerifier, normalize_for_match

SOURCE = (
    "新潟県魚沼産コシヒカリを使用。\n"
    "米の宝石と呼ばれるブレンド米です。\n"
    "粒立ちがよく、冷めてももちもちとした食感が続きます。"
)
CANDIDATES = [
    "新潟県魚沼産コシヒカリを使用。",
    "米の宝石と呼ばれるブレンド米です。",
    "粒立ちがよく、冷めてももちもちとした食感が続きます。",
]


@pytest.fixture
def verifier() -> VerbatimVerifier:
    return VerbatimVerifier(source_text=SOURCE, candidates=CANDIDATES, min_ratio=0.75)


class TestNormalizeForMatch:
    def test_ignores_width_case_and_spaces(self):
        assert normalize_for_match("ＡＢＣ の 商品") == normalize_for_match("abc の商品")


class TestExactMatch:
    def test_returns_candidate_as_is(self, verifier):
        result = verifier.resolve("米の宝石と呼ばれるブレンド米です。")
        assert result.method == "exact"
        assert result.text == "米の宝石と呼ばれるブレンド米です。"

    def test_matches_despite_whitespace_differences(self, verifier):
        result = verifier.resolve("  米の宝石と呼ばれる ブレンド米です。 ")
        assert result.method == "exact"
        assert result.text == "米の宝石と呼ばれるブレンド米です。"


class TestContainedMatch:
    def test_fragment_is_mapped_back_to_original_slice(self, verifier):
        """LLM が文の一部だけ返した場合、原文から該当箇所を切り出す。"""
        result = verifier.resolve("冷めてももちもちとした食感")
        assert result.method == "contained"
        assert result.text == "冷めてももちもちとした食感"
        assert result.text in SOURCE

    def test_concatenated_sentences_resolve_to_a_candidate(self, verifier):
        result = verifier.resolve(
            "新潟県魚沼産コシヒカリを使用。米の宝石と呼ばれるブレンド米です。さらに追記。"
        )
        assert result.method == "contained"
        assert result.text in CANDIDATES


class TestFuzzyMatch:
    def test_rewritten_sentence_is_replaced_by_the_original(self, verifier):
        """言い換えられた文は、元の文で置き換えて返す (LLM の文は採用しない)。"""
        result = verifier.resolve("米の宝石と呼ばれるブレンド米になります。")
        assert result.method == "fuzzy"
        assert result.text == "米の宝石と呼ばれるブレンド米です。"
        assert result.ratio >= 0.75


class TestRejection:
    def test_generated_sentence_is_rejected(self, verifier):
        """原文に無い創作文は棄却される。"""
        result = verifier.resolve("このお米は毎日の食卓を彩る最高のパートナーです")
        assert result.method == "none"
        assert result.text == ""
        assert not result.ok

    def test_empty_input_is_rejected(self, verifier):
        assert verifier.resolve("").method == "none"

    def test_too_short_input_is_rejected(self, verifier):
        assert verifier.resolve("最高").method == "none"


class TestNearest:
    """検証に通らなかった出力について、言い換え元らしき候補を拾う段。"""

    def test_returns_the_closest_candidate_above_the_threshold(self, verifier):
        result = verifier.nearest("米の宝石と称される高級ブレンド米になります。", min_ratio=0.5)
        assert result.method == "nearest"
        assert result.text == "米の宝石と呼ばれるブレンド米です。"
        assert 0.5 <= result.ratio < 0.75  # resolve() では棄却される帯

    def test_rejects_when_nothing_is_close_enough(self, verifier):
        """言い換え元が無い完全な創作は、ここでも拾わない。"""
        result = verifier.nearest("毎日の食卓を彩る、心に響く至高の逸品をあなたに", min_ratio=0.5)
        assert result.method == "none"
        assert result.text == ""

    def test_respects_the_exclude_list(self, verifier):
        excluded = "米の宝石と呼ばれるブレンド米です。"
        result = verifier.nearest(
            "米の宝石と称される高級ブレンド米になります。", min_ratio=0.5, exclude=[excluded]
        )
        assert result.text != excluded

    def test_returns_a_candidate_not_the_input(self, verifier):
        """返るのは常に候補文。入力文字列がそのまま返ることはない。"""
        query = "米の宝石と称される高級ブレンド米になります。"
        result = verifier.nearest(query, min_ratio=0.5)
        assert result.text != query
        assert result.text in CANDIDATES

    def test_empty_input_is_rejected(self, verifier):
        assert verifier.nearest("", min_ratio=0.5).method == "none"

    def test_a_high_threshold_disables_the_stage(self, verifier):
        assert verifier.nearest("米の宝石と称される高級ブレンド米になります。", min_ratio=0.99).method == "none"


class TestVerbatimGuarantee:
    @pytest.mark.parametrize(
        "llm_output",
        [
            "米の宝石と呼ばれるブレンド米です。",
            "冷めてももちもちとした食感",
            "米の宝石と呼ばれるブレンド米になります。",
            "新潟県魚沼産コシヒカリを100%使用しています。",
            "まったく関係のない創作された文章です",
        ],
    )
    def test_output_always_exists_in_source(self, verifier, llm_output):
        """採用される文字列は必ず原文中に存在する (棄却時は空文字)。"""
        result = verifier.resolve(llm_output)
        if result.text:
            assert normalize_for_match(result.text) in normalize_for_match(SOURCE)
