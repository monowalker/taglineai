"""ローカル LLM (OpenAI 互換 API) クライアント。

LLM に渡すのは **商品名・抽出本文・観点(ペルソナ)・お手本だけ**。HTML は送らない。
LLM の役割は「本文から文を選ぶ」ことに限定されており、その出力は後段の
:mod:`app.services.verifier` で必ず原文と突き合わされる。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai import OpenAIError

from app.core.errors import LlmError, LlmResponseParseError
from app.core.logging import get_logger, kv
from app.schemas import Example

if TYPE_CHECKING:  # 循環インポートを避ける
    from app.services.model_registry import ModelConfig

logger = get_logger(__name__)

# 生成を禁止する中核ルール。文言は変更しないこと。
EXTRACTION_RULES = """・文章を生成してはいけません
・文章を書き換えてはいけません
・HTML内に存在する文章のみ利用してください
・原文そのまま返してください
・不足している情報は作らない
・文章を短く加工しない
・日本語のみ返してください"""

# 仕様で定められたシステムプロンプト。文言を変更しないこと。
BASE_SYSTEM_PROMPT = f"""あなたは文章抽出AIです。

目的は商品ページから
1行目
商品名
2行目
商品の魅力を表す文章
3行目
商品の特徴を表す文章
を抽出することです。

以下を必ず守ってください。

{EXTRACTION_RULES}

JSONのみ返してください

出力
{{
    "title":"",
    "line2":"",
    "line3":""
}}"""

# 出力 JSON のスキーマ。対応するサーバでは文法制約として効かせる。
_JSON_SCHEMA = {
    "name": "tagline_selection",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "line2": {"type": "string"},
            "line3": {"type": "string"},
        },
        "required": ["title", "line2", "line3"],
        "additionalProperties": False,
    },
}

# API キーが不要なサーバ向けのダミー値 (OpenAI SDK は空文字を許さない)。
_DUMMY_API_KEY = "no-key"

# 構造化出力の指定方法を、対応度の高い順に並べたもの。
# サーバが受け付けなければ 1 段ずつ緩めていき、最後は指定なしで投げる。
# (json_schema は OpenAI / llama.cpp などが対応、json_object はより広く対応)
_RESPONSE_FORMAT_MODES: tuple[str | None, ...] = ("json_schema", "json_object", None)

# 「そのパラメータには対応していない」ことを示すとみなすステータス。
_UNSUPPORTED_STATUSES = (400, 404, 415, 422, 501)

# エラー本文をそのまま出すと長すぎるので切り詰める。
_ERROR_DETAIL_CHARS = 300


def _provider_error_detail(exc: APIStatusError) -> str:
    """プロバイダが返したエラーメッセージを取り出す。

    どこが悪いのかはサーバ側の本文にしか書かれていないので、
    握り潰さずに利用者とログの両方へ渡す。
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
            if message:
                return str(message)[:_ERROR_DETAIL_CHARS]
        elif isinstance(error, str) and error:
            return error[:_ERROR_DETAIL_CHARS]
        if body.get("message"):
            return str(body["message"])[:_ERROR_DETAIL_CHARS]

    response = getattr(exc, "response", None)
    text = getattr(response, "text", "") or ""
    return " ".join(text.split())[:_ERROR_DETAIL_CHARS]


def _status_error_message(exc: APIStatusError) -> str:
    detail = _provider_error_detail(exc)
    if detail:
        return f"LLM がエラーを返しました (HTTP {exc.status_code}): {detail}"
    return f"LLM がエラーを返しました (HTTP {exc.status_code})。"

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


@dataclass(frozen=True)
class LlmSelection:
    """LLM が選んだ 3 行 (未検証)。"""

    title: str
    line2: str = ""
    line3: str = ""
    raw: str = ""
    elapsed_ms: float = 0.0


def build_system_prompt(
    *,
    persona_prompt: str | None = None,
    examples: list[Example] | None = None,
) -> str:
    """システムプロンプトを組み立てる。

    本体は仕様で定められた文言そのまま。観点 (ペルソナ) とお手本は
    あくまで「どれを選ぶか」の判断材料として後ろに足すだけで、
    生成禁止のルールは緩めない。
    """
    sections = [BASE_SYSTEM_PROMPT]

    if persona_prompt:
        sections += [
            "",
            "【選ぶ際の観点】",
            persona_prompt,
            "",
            "この観点は「どの文を選ぶか」の判断にのみ使ってください。",
            "観点に合わせて文章を書き換えたり、作ったりすることは禁止です。",
        ]

    if examples:
        sections += [
            "",
            "【お手本】",
            "良いとされた抽出結果の例です。選び方の参考にしてください。",
            "お手本の文章そのものを出力に使ってはいけません。",
        ]
        for index, example in enumerate(examples, start=1):
            sections.append("")
            sections.append(f"例{index}")
            sections.append(f"1行目: {example.title}")
            if example.line2:
                sections.append(f"2行目: {example.line2}")
            if example.line3:
                sections.append(f"3行目: {example.line3}")

    return "\n".join(sections)


def build_user_prompt(product_name: str, body_for_prompt: str) -> str:
    """LLM へ渡すユーザメッセージを組み立てる。

    渡すのは商品名と抽出本文のみ。本文は番号付きの文リストにして、
    「書く」のではなく「選ぶ」作業であることを構造で示す。
    """
    return (
        "商品名:\n"
        f"{product_name or '(不明)'}\n"
        "\n"
        "抽出本文(この中の文だけを使うこと):\n"
        f"{body_for_prompt}\n"
        "\n"
        "上記の抽出本文から、line2 と line3 に入れる文を 1 つずつ選び、"
        "番号を除いた本文をそのままコピーしてください。\n"
        "line2 と line3 に同じ文を選んではいけません。\n"
        "該当する文が無い項目は空文字にしてください。"
    )


def parse_json_object(text: str) -> dict:
    """LLM 応答から JSON オブジェクトを取り出す。

    素の JSON、コードフェンス付き、前後に説明文が付いたもの、
    reasoning が ``<think>`` で混入したものに対応する。

    Raises:
        LlmResponseParseError: JSON として解釈できなかった場合。
    """
    if not text or not text.strip():
        raise LlmResponseParseError("LLM の応答が空でした。")

    cleaned = _THINK_BLOCK_RE.sub("", text).strip()

    fence = _CODE_FENCE_RE.search(cleaned)
    if fence:
        cleaned = fence.group(1).strip()

    for snippet in _iter_json_object_candidates(cleaned):
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise LlmResponseParseError("LLM の応答を JSON として解釈できませんでした。")


def _iter_json_object_candidates(text: str):
    """文字列中の波括弧で囲まれた部分を、外側から順に切り出す。

    文字列リテラル内の波括弧は数えない。
    """
    stripped = text.strip()
    if stripped.startswith("{"):
        yield stripped

    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : index + 1]


class LlmClient:
    """OpenAI 互換エンドポイントへの薄いラッパ。

    ローカル LLM は同時実行数が限られる (llama.cpp の ``--parallel`` 等) ため、
    セマフォで並列度を絞る。
    """

    def __init__(self, config: "ModelConfig") -> None:
        self._config = config
        self._semaphore = asyncio.Semaphore(config.concurrency)
        self._client = AsyncOpenAI(
            base_url=config.api_base,
            api_key=config.api_key or _DUMMY_API_KEY,
            timeout=config.timeout,
            max_retries=0,  # リトライは呼び出し側で制御する
        )
        # 使用する構造化出力の指定方法。サーバに拒否されたら 1 段緩め、
        # 成功した段をクライアント単位で覚えておく (毎回試し直さない)。
        self._mode_index = 0

    @property
    def config(self) -> "ModelConfig":
        return self._config

    async def close(self) -> None:
        await self._client.close()

    async def select(
        self,
        *,
        product_name: str,
        body_for_prompt: str,
        persona_prompt: str | None = None,
        examples: list[Example] | None = None,
    ) -> LlmSelection:
        """商品名と本文を渡し、選択結果を得る。

        Raises:
            LlmError:               接続・タイムアウト・サーバエラー。
            LlmResponseParseError:  応答が JSON として壊れている。
        """
        messages = [
            {
                "role": "system",
                "content": build_system_prompt(
                    persona_prompt=persona_prompt, examples=examples
                ),
            },
            {"role": "user", "content": build_user_prompt(product_name, body_for_prompt)},
        ]

        queued_at = time.perf_counter()
        async with self._semaphore:
            # 計測はセマフォ取得後に開始する。順番待ちの時間を LLM 応答時間に
            # 混ぜると、同時実行時に実際より遅く見えてしまうため。
            started = time.perf_counter()
            content = await self._create_completion(messages)
            elapsed_ms = (time.perf_counter() - started) * 1000
        wait_ms = (started - queued_at) * 1000

        # 要件: LLM 応答時間をログに出す。
        logger.info(
            "llm responded %s",
            kv(
                model=self._config.model,
                model_id=self._config.id,
                llm_ms=elapsed_ms,
                wait_ms=wait_ms,
                chars=len(content or ""),
                persona="yes" if persona_prompt else "no",
                examples=len(examples or []),
            ),
        )

        payload = parse_json_object(content)
        return LlmSelection(
            title=_as_text(payload.get("title")),
            line2=_as_text(payload.get("line2")),
            line3=_as_text(payload.get("line3")),
            raw=content,
            elapsed_ms=elapsed_ms,
        )

    async def ping(self) -> tuple[bool, float | None, str | None]:
        """疎通確認。``(到達可否, 応答ミリ秒, 詳細)`` を返す。"""
        started = time.perf_counter()
        try:
            await self._client.models.list()
        except OpenAIError as exc:
            return False, None, f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # pragma: no cover - 想定外の例外も握りつぶさない
            return False, None, f"{type(exc).__name__}: {exc}"
        return True, (time.perf_counter() - started) * 1000, None

    # ---- 内部 ----

    async def _create_completion(self, messages: list[dict]) -> str:
        """構造化出力の指定を段階的に緩めながら呼び出す。

        json_schema → json_object → 指定なし の順に試す。プロバイダによって
        対応度が違うため (Gemini や一部の互換サーバは json_schema を拒否する)。

        **どの段で失敗しても、必ず ``LlmError`` に包んでプロバイダの
        エラーメッセージを添える。** ここを素通しすると呼び出し側で
        「BadRequestError」としか分からず、原因が追えなくなる。
        """
        last_error: APIStatusError | None = None

        while self._mode_index < len(_RESPONSE_FORMAT_MODES):
            mode = _RESPONSE_FORMAT_MODES[self._mode_index]
            try:
                return await self._request(messages, response_format_mode=mode)
            except APIStatusError as exc:
                last_error = exc
                if mode is not None and exc.status_code in _UNSUPPORTED_STATUSES:
                    logger.warning(
                        "llm rejected the response_format; falling back %s",
                        kv(
                            model=self._config.id,
                            mode=mode,
                            next=_RESPONSE_FORMAT_MODES[self._mode_index + 1] or "none",
                            status=exc.status_code,
                            detail=_provider_error_detail(exc),
                        ),
                    )
                    self._mode_index += 1
                    continue
                logger.warning(
                    "llm returned an error %s",
                    kv(
                        model=self._config.id,
                        llm_model=self._config.model,
                        status=exc.status_code,
                        detail=_provider_error_detail(exc),
                    ),
                )
                raise LlmError(_status_error_message(exc)) from exc

        # ここに来るのは全ての段で拒否された場合のみ。
        raise LlmError(_status_error_message(last_error)) from last_error

    async def _request(self, messages: list[dict], *, response_format_mode: str | None) -> str:
        kwargs: dict = {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }
        if response_format_mode == "json_schema":
            kwargs["response_format"] = {"type": "json_schema", "json_schema": _JSON_SCHEMA}
        elif response_format_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except APITimeoutError as exc:
            raise LlmError(
                f"LLM の応答がタイムアウトしました ({self._config.timeout:.0f} 秒)。"
            ) from exc
        except APIConnectionError as exc:
            raise LlmError(
                f"LLM に接続できませんでした ({self._config.api_base})。"
            ) from exc
        except APIStatusError:
            raise
        except OpenAIError as exc:
            raise LlmError(f"LLM の呼び出しに失敗しました: {type(exc).__name__}") from exc

        if not response.choices:
            raise LlmError("LLM の応答に choices が含まれていませんでした。")
        return response.choices[0].message.content or ""


def _as_text(value: object) -> str:
    """LLM が文字列以外を返してきた場合も安全に文字列化する。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(item) for item in value).strip()
    return str(value).strip()
