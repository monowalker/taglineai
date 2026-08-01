"""抽出パイプライン。

1 URL あたりの流れ:

    HTML 取得 → 解析 (商品名 / 本文) → 前処理 (候補文) → LLM で選択
              → 原文一致検証 → 結果組み立て

どの段階で失敗しても、その URL だけがエラーになり他の URL には波及しない。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import (
    ContentExtractionError,
    ExtractionError,
    LlmError,
    LlmResponseParseError,
)
from app.core.logging import get_logger, kv
from app.schemas import Example, ExtractResult, ExtractResultMeta
from app.services import preprocess, selector
from app.services.fetcher import HtmlFetcher, validate_url
from app.services.html_parser import parse_page
from app.services.llm import LlmClient, LlmSelection
from app.services.model_registry import LlmRegistry
from app.services.store import SettingsStore
from app.services.verifier import VerbatimVerifier, normalize_for_match

logger = get_logger(__name__)


@dataclass
class _PageContext:
    """1 ページ分の前処理済みデータ。"""

    url: str
    title: str
    title_source: str
    candidates: list[str]
    source_text: str
    extractor: str
    http_status: int
    fetch_ms: float
    extract_ms: float


class ExtractionPipeline:
    """URL 群を並列に処理して抽出結果を返す。"""

    def __init__(
        self,
        settings: Settings,
        fetcher: HtmlFetcher,
        registry: LlmRegistry,
        store: SettingsStore | None = None,
    ) -> None:
        self._settings = settings
        self._fetcher = fetcher
        self._registry = registry
        self._store = store
        self._fetch_semaphore = asyncio.Semaphore(settings.fetch_concurrency)

    async def run(
        self,
        urls: list[str],
        *,
        persona_prompt: str | None = None,
        model_id: str | None = None,
    ) -> list[ExtractResult]:
        """複数 URL を処理する。入力順と 1 対 1 で結果を返す。

        Args:
            persona_prompt: 文を選ぶ際の観点。None なら保存済みの観点を使う。
            model_id:       使用するモデル。None なら選択中のモデルを使う。
        """
        persona = self._resolve_persona(persona_prompt)
        examples = self._store.prompt_examples() if self._store else []
        llm = self._registry.client(self._resolve_model_id(model_id))

        logger.info(
            "extraction started %s",
            kv(urls=len(urls), model=llm.config.id, llm_model=llm.config.model),
        )

        tasks = [
            asyncio.create_task(
                self.run_one(url, llm=llm, persona_prompt=persona, examples=examples)
            )
            for url in urls
        ]
        return list(await asyncio.gather(*tasks))

    async def run_one(
        self,
        url: str,
        *,
        llm: LlmClient | None = None,
        persona_prompt: str | None = None,
        examples: list[Example] | None = None,
    ) -> ExtractResult:
        """1 URL を処理する。例外は必ずエラー結果に変換して返す。"""
        client = llm or self._registry.client(None)
        started = time.perf_counter()
        try:
            context = await self._prepare(url)
            result = await self._select_lines(context, client, persona_prompt, examples or [])
        except ExtractionError as exc:
            logger.warning("extraction failed %s", kv(url=url, code=exc.code, reason=exc.message))
            return ExtractResult(
                url=url,
                error=exc.message,
                error_code=exc.code,
                meta=ExtractResultMeta(total_ms=(time.perf_counter() - started) * 1000),
            )
        except Exception as exc:  # pragma: no cover - 想定外は 500 にせず個別エラーにする
            logger.exception("unexpected extraction failure %s", kv(url=url))
            return ExtractResult(
                url=url,
                error=f"予期しないエラーが発生しました: {type(exc).__name__}",
                error_code="internal_error",
                meta=ExtractResultMeta(total_ms=(time.perf_counter() - started) * 1000),
            )

        result.meta.total_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "extraction succeeded %s",
            kv(
                url=url,
                title=result.title[:60],
                line2_match=result.meta.line2_match,
                line3_match=result.meta.line3_match,
                total_ms=result.meta.total_ms,
            ),
        )
        return result

    # ---- 段階ごとの処理 ----

    def _resolve_model_id(self, model_id: str | None) -> str | None:
        """リクエスト指定を優先し、無ければ保存されている選択を使う。"""
        if model_id:
            return model_id
        if self._store is not None and self._store.selected_model_id:
            return self._store.selected_model_id
        return None

    def _resolve_persona(self, persona_prompt: str | None) -> str | None:
        """リクエスト指定を優先し、無ければ保存されている観点を使う。"""
        if persona_prompt is not None:
            return persona_prompt or None
        if self._store is not None:
            return self._store.active_prompt or None
        return None

    async def _prepare(self, url: str) -> _PageContext:
        """HTML を取得し、候補文まで組み立てる。"""
        validate_url(url)

        async with self._fetch_semaphore:
            # requests は同期 API なのでワーカースレッドへ逃がす。
            page = await asyncio.to_thread(self._fetcher.fetch, url)

        extract_started = time.perf_counter()
        parsed = parse_page(page.html, url=page.url)

        cleaned_body = preprocess.clean_text(parsed.body)
        cleaned_descriptions = [preprocess.clean_text(d) for d in parsed.descriptions]

        candidates = preprocess.build_candidates(
            body=cleaned_body,
            descriptions=cleaned_descriptions,
            min_chars=self._settings.min_sentence_chars,
            max_chars=self._settings.max_sentence_chars,
            limit=self._settings.max_candidates,
        )
        extract_ms = (time.perf_counter() - extract_started) * 1000

        if not candidates:
            raise ContentExtractionError("商品説明として使える文章が見つかりませんでした。")

        # 検証用の原文。候補文の元になったテキストと同じ正規化状態にしておく。
        source_text = "\n".join([*cleaned_descriptions, cleaned_body])

        logger.info(
            "page prepared %s",
            kv(
                url=url,
                extractor=parsed.extractor,
                title_source=parsed.title_source or "-",
                candidates=len(candidates),
                extract_ms=extract_ms,
            ),
        )

        return _PageContext(
            url=url,
            title=parsed.title,
            title_source=parsed.title_source,
            candidates=candidates,
            source_text=source_text,
            extractor=parsed.extractor,
            http_status=page.status_code,
            fetch_ms=page.elapsed_ms,
            extract_ms=extract_ms,
        )

    async def _select_lines(
        self,
        context: _PageContext,
        llm: LlmClient,
        persona_prompt: str | None,
        examples: list[Example],
    ) -> ExtractResult:
        """LLM に選択させ、原文一致を検証して結果を組み立てる。"""
        verifier = VerbatimVerifier(
            source_text=context.source_text,
            candidates=context.candidates,
            min_ratio=self._settings.verify_min_ratio,
        )

        body_for_prompt = preprocess.join_for_prompt(
            context.candidates, self._settings.max_body_chars
        )

        try:
            selection = await llm.select(
                product_name=context.title,
                body_for_prompt=body_for_prompt,
                persona_prompt=persona_prompt,
                examples=examples,
            )
        except (LlmError, LlmResponseParseError):
            raise
        except Exception as exc:  # pragma: no cover
            raise LlmError(f"LLM の呼び出しに失敗しました: {type(exc).__name__}") from exc

        line2, line2_match = self._resolve_line(verifier, selection.line2, exclude=[])
        line3, line3_match = self._resolve_line(
            verifier, selection.line3, exclude=[line2] if line2 else []
        )

        # 「同一文章は禁止」— 検証後に一致してしまった場合は line3 を選び直す。
        if line2 and line3 and normalize_for_match(line2) == normalize_for_match(line3):
            replacement = selector.pick_best(context.candidates, exclude=[line2])
            line3, line3_match = (replacement, "unverified") if replacement else ("", "none")

        title = self._resolve_title(context, verifier, selection)

        return ExtractResult(
            url=context.url,
            title=title,
            line2=line2,
            line3=line3,
            meta=ExtractResultMeta(
                fetch_ms=context.fetch_ms,
                extract_ms=context.extract_ms,
                llm_ms=selection.elapsed_ms,
                http_status=context.http_status,
                extractor=context.extractor,
                candidate_count=len(context.candidates),
                candidates=context.candidates,
                title_source=context.title_source or None,
                line2_match=line2_match,
                line3_match=line3_match,
                persona_applied=bool(persona_prompt),
                example_count=len(examples),
                llm_model=llm.config.model,
                llm_model_id=llm.config.id,
                llm_model_label=llm.config.label,
            ),
        )

    def _resolve_line(
        self,
        verifier: VerbatimVerifier,
        llm_text: str,
        *,
        exclude: list[str],
    ) -> tuple[str, str]:
        """LLM の 1 行を原文へ解決する。

        **LLM の文をそのまま採用することは決してない。** 3 段構えで、
        必ず候補文 (＝原文) のどれかに着地させる。

            1. 検証に通れば、その原文を採用            (exact / contained / fuzzy)
            2. 通らなければ、LLM が狙っていた文を探す  (nearest)
               言い換えの場合は元の文が候補の中にあるので、最も近いものを拾う
            3. それも無ければ、候補文からスコア順に選び直す (unverified)

        Returns:
            (採用した文字列, 一致方式)
        """
        excluded_keys = {normalize_for_match(t) for t in exclude if t}

        if llm_text:
            match = verifier.resolve(llm_text)
            if match.ok and normalize_for_match(match.text) not in excluded_keys:
                if match.method == "fuzzy":
                    logger.info(
                        "llm output normalized to source text %s",
                        kv(ratio=round(match.ratio, 3), llm=llm_text[:60], source=match.text[:60]),
                    )
                return match.text, match.method

            logger.warning(
                "llm output rejected (not verbatim) %s",
                kv(ratio=round(match.ratio, 3), llm=llm_text[:80]),
            )

            # 棄却したが、言い換え元らしき候補があればそれを採用する。
            # いきなりスコア順に飛ぶと、LLM が何を選ぼうとしたかを捨ててしまい、
            # 無関係な見出しなどが出てしまうため。
            nearest = verifier.nearest(
                llm_text,
                min_ratio=self._settings.fallback_min_ratio,
                exclude=exclude,
            )
            if nearest.ok:
                logger.info(
                    "fell back to the nearest source sentence %s",
                    kv(
                        ratio=round(nearest.ratio, 3),
                        llm=llm_text[:60],
                        source=nearest.text[:60],
                    ),
                )
                return nearest.text, nearest.method

        fallback = selector.pick_best(verifier.candidates, exclude=exclude)
        if fallback:
            logger.info(
                "fell back to the highest scoring candidate %s", kv(source=fallback[:60])
            )
            return fallback, "unverified"
        return "", "none"

    def _resolve_title(
        self,
        context: _PageContext,
        verifier: VerbatimVerifier,
        selection: LlmSelection,
    ) -> str:
        """商品名を決定する。

        仕様の優先順位 (og:title > title > h1) で取れた値を正とする。
        取れなかった場合に限り、LLM が返した title を原文検証のうえ採用する。
        """
        if context.title:
            return context.title
        if selection.title:
            match = verifier.resolve(selection.title)
            if match.ok:
                return match.text
        return ""
