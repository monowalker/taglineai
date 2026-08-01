"""前処理のテスト。"""

from __future__ import annotations

import pytest

from app.services import preprocess


class TestCleanText:
    def test_removes_html_tags_and_unescapes_entities(self):
        assert preprocess.clean_text("<p>おいしい&amp;安心</p>") == "おいしい&安心"

    def test_normalizes_whitespace(self):
        raw = "  新潟県魚沼産　　コシヒカリを使用。 \r\n\r\n\r\n  米の宝石です。  "
        assert preprocess.clean_text(raw) == "新潟県魚沼産 コシヒカリを使用。\n\n米の宝石です。"

    def test_removes_control_characters(self):
        assert preprocess.clean_text("あ\x00い\x07う") == "あいう"


class TestSplitSentences:
    def test_splits_on_japanese_punctuation(self):
        text = "新潟県魚沼産コシヒカリを使用。米の宝石と呼ばれるブレンド米です。おいしい！"
        assert preprocess.split_sentences(text) == [
            "新潟県魚沼産コシヒカリを使用。",
            "米の宝石と呼ばれるブレンド米です。",
            "おいしい！",
        ]

    def test_splits_on_newlines(self):
        assert preprocess.split_sentences("一行目\n二行目\n\n三行目") == [
            "一行目",
            "二行目",
            "三行目",
        ]

    def test_keeps_closing_bracket_with_sentence(self):
        assert preprocess.split_sentences("「おいしい。」と評判です。") == [
            "「おいしい。」",
            "と評判です。",
        ]


class TestSplitLongSentence:
    def test_short_sentence_is_untouched(self):
        assert preprocess.split_long_sentence("短い文です。", 100) == ["短い文です。"]

    def test_splits_at_comma_and_preserves_characters(self):
        sentence = "あ" * 40 + "、" + "い" * 40 + "、" + "う" * 40
        parts = preprocess.split_long_sentence(sentence, 50)

        assert all(len(p) <= 50 for p in parts)
        # 文字は追加も削除もされない
        assert "".join(parts) == sentence

    def test_splits_without_commas(self):
        sentence = "あ" * 200
        parts = preprocess.split_long_sentence(sentence, 60)
        assert all(len(p) <= 60 for p in parts)
        assert "".join(parts) == sentence


class TestDedupe:
    def test_removes_exact_duplicates(self):
        assert preprocess.dedupe(["同じ文です。", "同じ文です。", "違う文です。"]) == [
            "同じ文です。",
            "違う文です。",
        ]

    def test_removes_duplicates_differing_only_in_width_or_space(self):
        result = preprocess.dedupe(["ＡＢＣの商品です。", "ABC の商品です。"])
        assert result == ["ＡＢＣの商品です。"]  # 先に現れた原文が残る


class TestIsNoise:
    def test_navigation_phrases_are_noise(self):
        assert preprocess.is_noise("カートに入れる")
        assert preprocess.is_noise("Copyright 2026 サンプル通販 All rights reserved.")

    def test_non_japanese_is_noise(self):
        assert preprocess.is_noise("Add to cart now")

    def test_product_description_is_not_noise(self):
        assert not preprocess.is_noise("新潟県魚沼産コシヒカリを使用。")


class TestStripPromoAffixes:
    """先頭・末尾の販促ブロックだけを剥がす (中身は削らない)。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                "【あす楽】【ポイント10倍】新潟県魚沼産コシヒカリを使用。",
                "新潟県魚沼産コシヒカリを使用。",
            ),
            ("【最大2000円OFFクーポン配布中】米の宝石です。", "米の宝石です。"),
            ("★ポイント10倍★ 感謝をコメて 魚沼産コシヒカリ", "感謝をコメて 魚沼産コシヒカリ"),
            ("＼ 全品ポイント10倍 ／ 魚沼産コシヒカリ", "魚沼産コシヒカリ"),
            ("魚沼産コシヒカリ【送料無料】", "魚沼産コシヒカリ"),
            ("(送料無料) 魚沼産コシヒカリ", "魚沼産コシヒカリ"),
            ("[楽天ランキング1位] 魚沼産コシヒカリ", "魚沼産コシヒカリ"),
        ],
    )
    def test_removes_promotional_blocks(self, raw, expected):
        assert preprocess.strip_promo_affixes(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "【新米】魚沼産コシヒカリ",          # 販促語を含まない括弧は残す
            "【産地直送】朝採れ野菜をお届け。",
            "限定生産の希少な茶葉だけを使用しています。",
            "新潟県魚沼産コシヒカリを使用。",
        ],
    )
    def test_keeps_non_promotional_text(self, raw):
        assert preprocess.strip_promo_affixes(raw) == raw

    def test_does_not_touch_brackets_in_the_middle(self):
        """途中の括弧を消すと連続した部分文字列でなくなるので触らない。"""
        raw = "本商品は【ポイント10倍】の対象です。"
        assert preprocess.strip_promo_affixes(raw) == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "【あす楽】【ポイント10倍】新潟県魚沼産コシヒカリを使用。",
            "★ポイント10倍★ 感謝をコメて 魚沼産コシヒカリ",
            "魚沼産コシヒカリ【送料無料】",
            "＼ 全品ポイント10倍 ／ 魚沼産コシヒカリ",
        ],
    )
    def test_result_is_always_a_substring_of_the_input(self, raw):
        """原文一致検証をそのまま通せること。"""
        assert preprocess.strip_promo_affixes(raw) in raw

    def test_empty_input(self):
        assert preprocess.strip_promo_affixes("") == ""

    def test_a_block_only_string_is_not_emptied_into_noise(self):
        """販促ブロックしか無い文は空になり、候補から外れる。"""
        assert preprocess.strip_promo_affixes("【ポイント10倍】") == ""


class TestIsPromotional:
    """価格・割引・ポイントは形で判定するので誤爆しにくい。"""

    @pytest.mark.parametrize(
        "sentence",
        [
            "通常価格 6,980円 のところ 期間限定 5,480円（税込）",
            "価格 5,480円（税込）送料無料",
            "いまだけ1980円",
            "全品20%OFF",
            "本日限り30％オフ",
            "エントリーでポイント10倍",
            "ｐｔ5倍キャンペーン実施中",
        ],
    )
    def test_promotional_sentences_are_detected(self, sentence):
        assert preprocess.is_promotional(sentence)
        assert preprocess.is_noise(sentence)

    @pytest.mark.parametrize(
        "sentence",
        [
            # 数字を含むが販促ではない、正当な商品説明
            "宮城県岩沼市に本社・工場を構える、1939年創業のレトルト食品メーカーです。",
            "たっぷり使える大容量の詰め替え用パック 500ml をご用意しました。",
            "約30時間の連続再生に対応し、10分の充電で5時間の再生が可能です。",
            "新潟県魚沼産コシヒカリを100%使用しています。",
            "米の宝石と呼ばれるブレンド米です。",
            "厚さ3センチの極厚カットステーキ。",
        ],
    )
    def test_legitimate_sentences_with_numbers_are_kept(self, sentence):
        assert not preprocess.is_promotional(sentence)


class TestBuildCandidates:
    def test_descriptions_come_first(self):
        candidates = preprocess.build_candidates(
            body="粒立ちがよく、冷めてももちもちとした食感が続きます。",
            descriptions=["新潟県魚沼産コシヒカリを使用。"],
        )
        assert candidates[0] == "新潟県魚沼産コシヒカリを使用。"

    def test_filters_noise_and_short_sentences(self):
        candidates = preprocess.build_candidates(
            body="カートに入れる\nはい\n新潟県魚沼産コシヒカリを使用。\nAdd to cart",
            min_chars=8,
        )
        assert candidates == ["新潟県魚沼産コシヒカリを使用。"]

    def test_respects_limit(self):
        body = "\n".join(f"これは{i}番目の商品説明の文章です。" for i in range(50))
        assert len(preprocess.build_candidates(body=body, limit=10)) == 10

    def test_every_candidate_exists_in_source(self):
        """候補文は必ず元テキストに存在する文字列であること。"""
        body = "新潟県魚沼産コシヒカリを使用。米の宝石と呼ばれるブレンド米です。"
        for candidate in preprocess.build_candidates(body=body):
            assert candidate in body


class TestJoinForPrompt:
    def test_numbers_candidates(self):
        prompt = preprocess.join_for_prompt(["一つ目。", "二つ目。"], max_chars=1000)
        assert prompt == "1. 一つ目。\n2. 二つ目。"

    def test_truncates_at_max_chars(self):
        prompt = preprocess.join_for_prompt(["あ" * 50, "い" * 50], max_chars=60)
        assert "い" not in prompt
