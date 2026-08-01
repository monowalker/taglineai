"""抽出パイプラインのテスト。"""

from __future__ import annotations

import asyncio

from app.core.errors import FetchTimeoutError, LlmError
from app.schemas import ExampleCreate, PersonaPresetCreate
from app.services.pipeline import ExtractionPipeline
from tests.conftest import SAMPLE_HTML, FakeFetcher, FakeLlm, make_registry

URL = "https://example.com/item/1"


def _run(settings, fetcher, llm, urls=(URL,), store=None, persona_prompt=None):
    pipeline = ExtractionPipeline(settings, fetcher, make_registry(llm), store)
    return asyncio.run(pipeline.run(list(urls), persona_prompt=persona_prompt))


def _plain(html: str) -> str:
    """タグを落とした HTML (原文含有チェック用)。"""
    from app.services.preprocess import clean_text

    return clean_text(html)


class TestHappyPath:
    def test_returns_title_and_two_verbatim_lines(self, settings, store):
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), FakeLlm(), store=store)[0]

        assert result.error is None
        assert result.title == "感謝をコメて 魚沼産コシヒカリ"
        assert result.line2 == "新潟県魚沼産コシヒカリを使用。"
        assert result.line3 == "米の宝石と呼ばれるブレンド米です。"
        assert result.meta.line2_match == "exact"
        assert result.meta.line3_match == "exact"
        assert result.meta.candidate_count > 0

    def test_llm_receives_product_name_and_body_only(self, settings, store):
        llm = FakeLlm()
        _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)

        call = llm.calls[0]
        assert call["product_name"] == "感謝をコメて 魚沼産コシヒカリ"
        # HTML タグは一切渡していない
        assert "<" not in call["body"]
        assert "script" not in call["body"]


class TestVerbatimEnforcement:
    def test_rewritten_line_is_replaced_by_the_original(self, settings, store):
        llm = FakeLlm(line2="新潟県魚沼産コシヒカリを使用しております。")
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)[0]

        assert result.line2 == "新潟県魚沼産コシヒカリを使用。"
        assert result.meta.line2_match == "fuzzy"

    def test_paraphrase_below_the_threshold_falls_back_to_the_nearest_sentence(
        self, settings, store
    ):
        """検証は通らないが言い換え元が分かる場合、その原文を採用する。

        いきなりスコア順に飛ぶと無関係な文が出てしまうため。
        """
        llm = FakeLlm(line2="米の宝石と称される高級ブレンド米になります。")  # 類似度 0.72
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)[0]

        assert result.line2 == "米の宝石と呼ばれるブレンド米です。"
        assert result.meta.line2_match == "nearest"

    def test_nearest_fallback_does_not_reuse_the_other_line(self, settings, store):
        """近い候補で選び直すときも line2 / line3 の重複は避ける。"""
        llm = FakeLlm(
            line2="米の宝石と呼ばれるブレンド米です。",
            line3="米の宝石と称される高級ブレンド米になります。",
        )
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)[0]

        assert result.line2 == "米の宝石と呼ばれるブレンド米です。"
        assert result.line3
        assert result.line3 != result.line2

    def test_generated_line_is_discarded_and_replaced_from_source(self, settings, store):
        """言い換え元すら無い完全な創作は、候補文からスコア順で選び直す。"""
        llm = FakeLlm(line2="毎日の食卓を彩る、心に響く至高の逸品をあなたに")  # 類似度 0.10
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)[0]

        assert result.line2 != "毎日の食卓を彩る、心に響く至高の逸品をあなたに"
        assert result.meta.line2_match == "unverified"
        assert result.line2 in _plain(SAMPLE_HTML)

    def test_the_fallback_never_returns_llm_text(self, settings, store):
        """どの段を通っても、出力は必ず候補文 (原文) のどれかになる。"""
        plain = _plain(SAMPLE_HTML).replace(" ", "")
        for generated in (
            "米の宝石と称される高級ブレンド米になります。",
            "毎日の食卓を彩る、心に響く至高の逸品をあなたに",
            "まったく無関係な創作の文章です",
        ):
            result = _run(
                settings, FakeFetcher({URL: SAMPLE_HTML}), FakeLlm(line2=generated), store=store
            )[0]
            assert result.line2 != generated
            assert result.line2.replace(" ", "") in plain

    def test_line2_and_line3_are_never_identical(self, settings, store):
        llm = FakeLlm(
            line2="米の宝石と呼ばれるブレンド米です。",
            line3="米の宝石と呼ばれるブレンド米です。",
        )
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)[0]

        assert result.line2
        assert result.line3
        assert result.line2 != result.line3

    def test_title_uses_html_priority_not_llm_output(self, settings, store):
        """商品名は og:title を正とし、LLM が別の文字列を返しても採用しない。"""
        llm = FakeLlm(title="LLMが勝手に作った商品名")
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)[0]
        assert result.title == "感謝をコメて 魚沼産コシヒカリ"
        assert result.meta.title_source == "og:title"

    def test_every_output_line_exists_in_the_page(self, settings, store):
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), FakeLlm(), store=store)[0]
        plain = _plain(SAMPLE_HTML).replace(" ", "")
        for line in (result.line2, result.line3):
            assert line.replace(" ", "") in plain


class TestPersonaAndExamples:
    def test_request_persona_is_passed_to_the_llm(self, settings, store):
        llm = FakeLlm()
        _run(
            settings,
            FakeFetcher({URL: SAMPLE_HTML}),
            llm,
            store=store,
            persona_prompt="明るく元気な印象の文を選ぶ",
        )
        assert llm.calls[0]["persona_prompt"] == "明るく元気な印象の文を選ぶ"

    def test_saved_active_prompt_is_used_when_the_request_omits_it(self, settings, store):
        asyncio.run(store.set_active_prompt("お年寄りにも分かりやすい文を選ぶ"))
        llm = FakeLlm()
        _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)

        assert llm.calls[0]["persona_prompt"] == "お年寄りにも分かりやすい文を選ぶ"

    def test_empty_request_persona_means_no_persona(self, settings, store):
        """空文字は「観点なし」。保存済みの観点にフォールバックしない。"""
        asyncio.run(store.set_active_prompt("保存された観点"))
        llm = FakeLlm()
        result = _run(
            settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store, persona_prompt=""
        )[0]

        assert llm.calls[0]["persona_prompt"] is None
        assert result.meta.persona_applied is False

    def test_request_persona_overrides_the_saved_one(self, settings, store):
        asyncio.run(store.set_active_prompt("保存された観点"))
        llm = FakeLlm()
        _run(
            settings,
            FakeFetcher({URL: SAMPLE_HTML}),
            llm,
            store=store,
            persona_prompt="リクエストの観点",
        )
        assert llm.calls[0]["persona_prompt"] == "リクエストの観点"

    def test_persona_applied_flag_reflects_usage(self, settings, store):
        without = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), FakeLlm(), store=store)[0]
        assert without.meta.persona_applied is False

        with_persona = _run(
            settings,
            FakeFetcher({URL: SAMPLE_HTML}),
            FakeLlm(),
            store=store,
            persona_prompt="明るい感じ",
        )[0]
        assert with_persona.meta.persona_applied is True

    def test_examples_are_passed_to_the_llm(self, settings, store):
        asyncio.run(
            store.add_example(
                ExampleCreate(title="お手本商品", line2="お手本の説明文です。", line3="")
            )
        )
        llm = FakeLlm()
        _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)

        titles = [e.title for e in llm.calls[0]["examples"]]
        assert "お手本商品" in titles
        assert llm.calls[0]["examples"]  # 組み込みのお手本も含まれる

    def test_saved_presets_do_not_change_the_prompt_by_themselves(self, settings, store):
        """プリセットは保存されているだけでは適用されない (選択して初めて効く)。"""
        asyncio.run(
            store.add_preset(PersonaPresetCreate(name="未選択", prompt="選ばれていない観点"))
        )
        llm = FakeLlm()
        _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)

        assert llm.calls[0]["persona_prompt"] is None


class TestErrorHandling:
    def test_invalid_url_is_reported_per_url(self, settings, store):
        result = _run(settings, FakeFetcher({}), FakeLlm(), urls=("not-a-url",), store=store)[0]
        assert result.error_code == "invalid_url"
        assert result.title == ""
        assert result.line2 == ""

    def test_fetch_timeout_is_reported(self, settings, store):
        fetcher = FakeFetcher({}, error=FetchTimeoutError("HTML の取得がタイムアウトしました (15 秒)。"))
        result = _run(settings, fetcher, FakeLlm(), store=store)[0]
        assert result.error_code == "fetch_timeout"
        assert "タイムアウト" in result.error

    def test_llm_error_is_reported(self, settings, store):
        llm = FakeLlm(error=LlmError("LLM に接続できませんでした。"))
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), llm, store=store)[0]
        assert result.error_code == "llm_error"

    def test_page_without_content_is_reported(self, settings, store):
        from tests.conftest import NO_CONTENT_HTML

        result = _run(settings, FakeFetcher({URL: NO_CONTENT_HTML}), FakeLlm(), store=store)[0]
        assert result.error_code == "content_extraction_error"

    def test_failure_of_one_url_does_not_affect_others(self, settings, store):
        bad = "https://example.com/missing"
        results = _run(
            settings, FakeFetcher({URL: SAMPLE_HTML}), FakeLlm(), urls=(URL, bad), store=store
        )

        assert [r.url for r in results] == [URL, bad]
        assert results[0].error is None
        assert results[1].error is not None

    def test_works_without_a_store(self, settings):
        """保存領域が無くても抽出そのものは動く。"""
        result = _run(settings, FakeFetcher({URL: SAMPLE_HTML}), FakeLlm(), store=None)[0]
        assert result.error is None
        assert result.line2
