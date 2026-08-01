"""REST API のルーティング。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app import __version__
from app.api.deps import get_pipeline, get_registry, get_settings_dep, get_store
from app.core.config import Settings
from app.core.logging import get_logger, kv
from app.schemas import (
    ActivePromptUpdate,
    Example,
    ExampleCreate,
    ExampleState,
    ExtractRequest,
    ExtractResult,
    HealthResponse,
    LlmPingResponse,
    ModelState,
    PersonaPreset,
    PersonaPresetCreate,
    PersonaState,
    SelectedModelUpdate,
)
from app.services.model_registry import LlmRegistry
from app.services.pipeline import ExtractionPipeline
from app.services.store import SettingsStore, StoreError

logger = get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# システム
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="ヘルスチェック",
    tags=["system"],
)
async def health(
    settings: Settings = Depends(get_settings_dep),
    registry: LlmRegistry = Depends(get_registry),
    store: SettingsStore = Depends(get_store),
) -> HealthResponse:
    config = registry.config(store.selected_model_id or None)
    return HealthResponse(
        version=__version__,
        llm_api_base=config.api_base,
        llm_model=config.model,
        max_urls_per_request=settings.max_urls_per_request,
    )


@router.get(
    "/llm/ping",
    response_model=LlmPingResponse,
    summary="ローカル LLM への疎通確認",
    tags=["system"],
)
async def llm_ping(
    model_id: str | None = None,
    registry: LlmRegistry = Depends(get_registry),
    store: SettingsStore = Depends(get_store),
) -> LlmPingResponse:
    target = model_id or store.selected_model_id or None
    client = registry.client(target)
    reachable, latency_ms, detail = await client.ping()
    return LlmPingResponse(
        reachable=reachable,
        llm_api_base=client.config.api_base,
        llm_model=client.config.model,
        latency_ms=latency_ms,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# 抽出
# ---------------------------------------------------------------------------


@router.post(
    "/extract",
    response_model=list[ExtractResult],
    summary="商品ページからキャッチフレーズを抽出する",
    description=(
        "複数の商品ページ URL を受け取り、URL ごとに商品名と原文そのままの 2 文を返す。"
        "1 件が失敗しても他の URL の処理は継続し、失敗した URL の結果には error が入る。"
    ),
    tags=["extract"],
)
async def extract(
    payload: ExtractRequest,
    settings: Settings = Depends(get_settings_dep),
    pipeline: ExtractionPipeline = Depends(get_pipeline),
) -> list[ExtractResult]:
    if len(payload.urls) > settings.max_urls_per_request:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"一度に指定できる URL は {settings.max_urls_per_request} 件までです "
                f"(指定: {len(payload.urls)} 件)。"
            ),
        )

    started = time.perf_counter()
    logger.info(
        "extract requested %s",
        kv(
            urls=len(payload.urls),
            persona="yes" if payload.persona_prompt else "no",
            model=payload.model_id or "(selected)",
        ),
    )

    results = await pipeline.run(
        payload.urls,
        persona_prompt=payload.persona_prompt,
        model_id=payload.model_id,
    )

    elapsed_ms = (time.perf_counter() - started) * 1000
    succeeded = sum(1 for r in results if r.error is None)
    logger.info(
        "extract finished %s",
        kv(total=len(results), ok=succeeded, ng=len(results) - succeeded, elapsed_ms=elapsed_ms),
    )
    return results


# ---------------------------------------------------------------------------
# モデル切り替え
# ---------------------------------------------------------------------------


def _model_state(registry: LlmRegistry, store: SettingsStore) -> ModelState:
    return ModelState(
        models=registry.list_infos(),
        selected_id=registry.resolve_id(store.selected_model_id or None),
    )


@router.get(
    "/models",
    response_model=ModelState,
    summary="使用できるモデルの一覧と選択状態を取得する",
    description="API キーそのものは返さない (設定されているかどうかのみ)。",
    tags=["models"],
)
async def list_models(
    registry: LlmRegistry = Depends(get_registry),
    store: SettingsStore = Depends(get_store),
) -> ModelState:
    return _model_state(registry, store)


@router.put(
    "/models/selected",
    response_model=ModelState,
    summary="使用するモデルを選択する",
    tags=["models"],
)
async def select_model(
    payload: SelectedModelUpdate,
    registry: LlmRegistry = Depends(get_registry),
    store: SettingsStore = Depends(get_store),
) -> ModelState:
    if not registry.has(payload.model_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"モデル '{payload.model_id}' は定義されていません。",
        )
    await store.set_selected_model_id(payload.model_id)
    return _model_state(registry, store)


# ---------------------------------------------------------------------------
# ペルソナ (観点) プリセット
# ---------------------------------------------------------------------------


@router.get(
    "/personas",
    response_model=PersonaState,
    summary="保存済みの観点プリセットと現在の観点を取得する",
    tags=["personas"],
)
async def list_personas(
    settings: Settings = Depends(get_settings_dep),
    store: SettingsStore = Depends(get_store),
) -> PersonaState:
    return PersonaState(
        presets=store.list_presets(),
        active_prompt=store.active_prompt,
        max_presets=settings.max_persona_presets,
    )


@router.post(
    "/personas",
    response_model=PersonaPreset,
    status_code=status.HTTP_201_CREATED,
    summary="観点をプリセットとして保存する",
    tags=["personas"],
)
async def create_persona(
    payload: PersonaPresetCreate,
    store: SettingsStore = Depends(get_store),
) -> PersonaPreset:
    try:
        return await store.add_preset(payload)
    except StoreError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete(
    "/personas/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="観点プリセットを削除する",
    tags=["personas"],
)
async def delete_persona(
    preset_id: str,
    store: SettingsStore = Depends(get_store),
) -> Response:
    if not await store.delete_preset(preset_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="プリセットが見つかりません。"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/personas/active",
    response_model=PersonaState,
    summary="現在使用する観点を設定する (保存せずに使う自由入力も含む)",
    tags=["personas"],
)
async def set_active_persona(
    payload: ActivePromptUpdate,
    settings: Settings = Depends(get_settings_dep),
    store: SettingsStore = Depends(get_store),
) -> PersonaState:
    try:
        await store.set_active_prompt(payload.prompt)
    except StoreError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PersonaState(
        presets=store.list_presets(),
        active_prompt=store.active_prompt,
        max_presets=settings.max_persona_presets,
    )


# ---------------------------------------------------------------------------
# お手本
# ---------------------------------------------------------------------------


@router.get(
    "/examples",
    response_model=ExampleState,
    summary="お手本の一覧を取得する",
    tags=["examples"],
)
async def list_examples(
    settings: Settings = Depends(get_settings_dep),
    store: SettingsStore = Depends(get_store),
) -> ExampleState:
    return ExampleState(examples=store.list_examples(), max_examples=settings.max_examples)


@router.post(
    "/examples",
    response_model=Example,
    status_code=status.HTTP_201_CREATED,
    summary="抽出結果をお手本に追加する (👍)",
    description="直近 MAX_EXAMPLES 件だけが保持され、古いものから自動的に削除される。",
    tags=["examples"],
)
async def create_example(
    payload: ExampleCreate,
    store: SettingsStore = Depends(get_store),
) -> Example:
    try:
        return await store.add_example(payload)
    except StoreError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete(
    "/examples/{example_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="お手本を削除する",
    tags=["examples"],
)
async def delete_example(
    example_id: str,
    store: SettingsStore = Depends(get_store),
) -> Response:
    if not await store.delete_example(example_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="お手本が見つかりません (組み込みのお手本は削除できません)。",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
