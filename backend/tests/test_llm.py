"""LLM クライアント (応答パース / エラー処理 / プロンプト) のテスト。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.core.errors import LlmError, LlmResponseParseError
from app.schemas import Example
from app.services.model_registry import ModelConfig
from app.services.llm import (
    EXTRACTION_RULES,
    LlmClient,
    build_system_prompt,
    build_user_prompt,
    parse_json_object,
)

SELECTION_JSON = (
    '{"title":"感謝をコメて 魚沼産コシヒカリ",'
    '"line2":"新潟県魚沼産コシヒカリを使用。",'
    '"line3":"米の宝石と呼ばれるブレンド米です。"}'
)


def _completion(content: str) -> SimpleNamespace:
    """OpenAI SDK のレスポンス形状を最小限で模したオブジェクト。"""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _request() -> httpx.Request:
    return httpx.Request("POST", "http://llm.invalid/v1/chat/completions")


@pytest.fixture
def model_config() -> ModelConfig:
    return ModelConfig(
        id="test",
        label="テストモデル",
        api_base="http://llm.invalid/v1",
        model="test-model",
        api_key="",
        timeout=30.0,
        concurrency=2,
        temperature=0.0,
        max_tokens=1024,
    )


def _example(title: str, line2: str) -> Example:
    return Example(
        id="x", title=title, line2=line2, builtin=False, created_at="2026-01-01T00:00:00+00:00"
    )


class TestParseJsonObject:
    def test_plain_json(self):
        assert parse_json_object(SELECTION_JSON)["title"] == "感謝をコメて 魚沼産コシヒカリ"

    def test_code_fenced_json(self):
        assert parse_json_object(f"```json\n{SELECTION_JSON}\n```")["line2"]

    def test_json_with_surrounding_prose(self):
        text = f"はい、抽出しました。\n{SELECTION_JSON}\n以上です。"
        assert parse_json_object(text)["line2"] == "新潟県魚沼産コシヒカリを使用。"

    def test_json_after_think_block(self):
        text = f"<think>どれを選ぼうか</think>{SELECTION_JSON}"
        assert parse_json_object(text)["title"] == "感謝をコメて 魚沼産コシヒカリ"

    def test_braces_inside_strings_do_not_break_parsing(self):
        text = '{"title":"{特価}の商品","line2":"説明文です。","line3":""}'
        assert parse_json_object(text)["title"] == "{特価}の商品"

    def test_empty_response_raises(self):
        with pytest.raises(LlmResponseParseError):
            parse_json_object("   ")

    def test_broken_json_raises(self):
        with pytest.raises(LlmResponseParseError):
            parse_json_object("JSONではありません")


class TestSystemPrompt:
    def test_forbids_generation(self):
        prompt = build_system_prompt()
        assert "文章を生成してはいけません" in prompt
        assert "原文そのまま返してください" in prompt
        assert EXTRACTION_RULES in prompt

    def test_keeps_the_specified_three_line_structure(self):
        prompt = build_system_prompt()
        assert "1行目" in prompt
        assert "商品の魅力を表す文章" in prompt
        assert "商品の特徴を表す文章" in prompt

    def test_persona_is_scoped_to_selection_only(self):
        prompt = build_system_prompt(persona_prompt="明るく元気な印象で")
        assert "明るく元気な印象で" in prompt
        assert "どの文を選ぶか" in prompt
        # 観点を口実に書き換えさせない
        assert "書き換えたり、作ったりすることは禁止です。" in prompt

    def test_persona_is_absent_when_not_given(self):
        assert "【選ぶ際の観点】" not in build_system_prompt()

    def test_examples_are_included_with_a_warning(self):
        prompt = build_system_prompt(examples=[_example("お手本商品", "お手本の説明文です。")])
        assert "【お手本】" in prompt
        assert "お手本商品" in prompt
        assert "お手本の説明文です。" in prompt
        assert "お手本の文章そのものを出力に使ってはいけません。" in prompt

    def test_examples_are_absent_when_not_given(self):
        assert "【お手本】" not in build_system_prompt()


class TestUserPrompt:
    def test_contains_only_name_and_body(self):
        prompt = build_user_prompt("商品名テスト", "1. 説明文です。")
        assert "商品名テスト" in prompt
        assert "1. 説明文です。" in prompt
        # HTML は一切含まれない
        assert "<" not in prompt

    def test_forbids_duplicate_lines(self):
        assert "同じ文を選んではいけません" in build_user_prompt("商品", "1. 説明。")


class TestLlmClientSelect:
    def test_returns_parsed_selection(self, model_config):
        client = LlmClient(model_config)
        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return _completion(SELECTION_JSON)

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        selection = asyncio.run(
            client.select(product_name="感謝をコメて 魚沼産コシヒカリ", body_for_prompt="1. 説明。")
        )

        assert selection.title == "感謝をコメて 魚沼産コシヒカリ"
        assert selection.line2 == "新潟県魚沼産コシヒカリを使用。"
        assert selection.line3 == "米の宝石と呼ばれるブレンド米です。"
        assert selection.elapsed_ms >= 0

        # 生成を抑えるため temperature は 0、モデルは設定値
        assert captured["temperature"] == 0.0
        assert captured["model"] == "test-model"
        assert "文章を生成してはいけません" in captured["messages"][0]["content"]

    def test_persona_and_examples_reach_the_system_prompt(self, model_config):
        client = LlmClient(model_config)
        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return _completion(SELECTION_JSON)

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        asyncio.run(
            client.select(
                product_name="商品",
                body_for_prompt="1. 説明。",
                persona_prompt="お年寄りにも分かりやすく",
                examples=[_example("お手本商品", "お手本の説明文です。")],
            )
        )

        system_prompt = captured["messages"][0]["content"]
        assert "お年寄りにも分かりやすく" in system_prompt
        assert "お手本商品" in system_prompt

    def test_non_string_values_are_coerced(self, model_config):
        client = LlmClient(model_config)

        async def fake_create(**kwargs):
            return _completion('{"title":123,"line2":null,"line3":["ア","イ"]}')

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        selection = asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        assert selection.title == "123"
        assert selection.line2 == ""
        assert selection.line3 == "ア イ"

    def test_broken_json_raises_parse_error(self, model_config):
        client = LlmClient(model_config)

        async def fake_create(**kwargs):
            return _completion("すみません、JSONを返せません")

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        with pytest.raises(LlmResponseParseError):
            asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))

    def test_connection_error_is_wrapped(self, model_config):
        client = LlmClient(model_config)

        async def fake_create(**kwargs):
            raise APIConnectionError(request=_request())

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        with pytest.raises(LlmError) as excinfo:
            asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        assert "接続できませんでした" in str(excinfo.value)

    def test_timeout_is_wrapped(self, model_config):
        client = LlmClient(model_config)

        async def fake_create(**kwargs):
            raise APITimeoutError(request=_request())

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        with pytest.raises(LlmError) as excinfo:
            asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        assert "タイムアウト" in str(excinfo.value)

    def test_falls_back_from_json_schema_to_json_object(self, model_config):
        """json_schema 非対応のサーバでは json_object に落とす。"""
        client = LlmClient(model_config)
        modes: list[str | None] = []

        async def fake_create(**kwargs):
            mode = (kwargs.get("response_format") or {}).get("type")
            modes.append(mode)
            if mode == "json_schema":
                raise APIStatusError(
                    "unsupported", response=httpx.Response(400, request=_request()), body=None
                )
            return _completion(SELECTION_JSON)

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        selection = asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        assert modes == ["json_schema", "json_object"]
        assert selection.line2

    def test_falls_back_all_the_way_to_no_response_format(self, model_config):
        client = LlmClient(model_config)
        modes: list[str | None] = []

        async def fake_create(**kwargs):
            mode = (kwargs.get("response_format") or {}).get("type")
            modes.append(mode)
            if mode is not None:
                raise APIStatusError(
                    "unsupported", response=httpx.Response(400, request=_request()), body=None
                )
            return _completion(SELECTION_JSON)

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        selection = asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        assert modes == ["json_schema", "json_object", None]
        assert selection.line2

    def test_the_working_mode_is_remembered(self, model_config):
        """一度落とした段は覚えておき、毎回試し直さない。"""
        client = LlmClient(model_config)
        modes: list[str | None] = []

        async def fake_create(**kwargs):
            mode = (kwargs.get("response_format") or {}).get("type")
            modes.append(mode)
            if mode == "json_schema":
                raise APIStatusError(
                    "unsupported", response=httpx.Response(400, request=_request()), body=None
                )
            return _completion(SELECTION_JSON)

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        for _ in range(3):
            asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        assert modes == ["json_schema", "json_object", "json_object", "json_object"]

    def test_a_bad_request_that_is_not_about_response_format_is_reported(self, model_config):
        """全段で 400 になる場合は、プロバイダのメッセージを添えて返す。

        ここを素通しすると呼び出し側で「BadRequestError」としか分からない。
        """
        client = LlmClient(model_config)

        async def fake_create(**kwargs):
            raise APIStatusError(
                "bad request",
                response=httpx.Response(400, request=_request()),
                body={"error": {"message": "Invalid value at 'generation_config.temperature'"}},
            )

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        with pytest.raises(LlmError) as excinfo:
            asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        message = str(excinfo.value)
        assert "HTTP 400" in message
        assert "generation_config.temperature" in message  # 原因が分かること

    def test_server_error_is_wrapped_with_the_provider_message(self, model_config):
        client = LlmClient(model_config)

        async def fake_create(**kwargs):
            raise APIStatusError(
                "boom",
                response=httpx.Response(500, request=_request()),
                body={"error": {"message": "internal failure"}},
            )

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        with pytest.raises(LlmError) as excinfo:
            asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        assert "HTTP 500" in str(excinfo.value)
        assert "internal failure" in str(excinfo.value)

    def test_falls_back_to_the_raw_body_when_there_is_no_error_field(self, model_config):
        client = LlmClient(model_config)

        async def fake_create(**kwargs):
            raise APIStatusError(
                "boom",
                response=httpx.Response(503, request=_request(), text="upstream unavailable"),
                body=None,
            )

        client._client.chat.completions.create = fake_create  # type: ignore[assignment]

        with pytest.raises(LlmError) as excinfo:
            asyncio.run(client.select(product_name="商品", body_for_prompt="1. 説明。"))
        assert "upstream unavailable" in str(excinfo.value)
