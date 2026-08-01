"""FastAPI アプリケーションのエントリポイント。"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, kv, new_request_id, request_id_var
from app.services.fetcher import HtmlFetcher
from app.services.model_registry import LlmRegistry, load_model_configs
from app.services.pipeline import ExtractionPipeline
from app.services.store import SettingsStore

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """起動時に共有リソースを組み立て、終了時に片付ける。"""
    settings = get_settings()
    configure_logging(settings.log_level)

    fetcher = HtmlFetcher(settings)
    model_configs, default_model_id = load_model_configs(settings)
    registry = LlmRegistry(model_configs, default_model_id)
    store = SettingsStore(settings)
    store.load()

    app.state.settings = settings
    app.state.fetcher = fetcher
    app.state.registry = registry
    app.state.store = store
    app.state.pipeline = ExtractionPipeline(settings, fetcher, registry, store)

    logger.info(
        "backend started %s",
        kv(
            version=__version__,
            models=len(model_configs),
            default_model=default_model_id,
            selected_model=store.selected_model_id or "(default)",
            http_timeout=settings.http_timeout,
        ),
    )
    try:
        yield
    finally:
        fetcher.close()
        await registry.close()
        logger.info("backend stopped")


app = FastAPI(
    title="商品キャッチフレーズ抽出 API",
    description=(
        "商品ページの HTML から本文を抽出し、ローカル LLM に**原文のまま**"
        "2 文を選ばせて返す API。LLM による生成・要約・言い換えは行わない。"
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# フロントは nginx 経由の同一オリジンで動くため CORS は本来不要だが、
# `npm run dev` (Vite) から直接叩く開発フローのために許可しておく。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """アクセスログ + リクエスト ID 付与。"""
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception(
            "access %s",
            kv(
                method=request.method,
                path=request.url.path,
                status=500,
                elapsed_ms=elapsed_ms,
                client=request.client.host if request.client else "-",
            ),
        )
        request_id_var.reset(token)
        raise

    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "access %s",
        kv(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            client=request.client.host if request.client else "-",
        ),
    )
    response.headers["X-Request-ID"] = request_id
    request_id_var.reset(token)
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """入力バリデーションエラーを日本語メッセージで返す。"""
    logger.warning("validation error %s", kv(path=request.url.path, errors=len(exc.errors())))
    return JSONResponse(
        status_code=422,
        content={
            "detail": "リクエストの形式が正しくありません。",
            "errors": [
                {"loc": ".".join(str(p) for p in err.get("loc", [])), "msg": err.get("msg", "")}
                for err in exc.errors()
            ],
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """想定外の例外もログに残したうえで JSON で返す。"""
    logger.exception("unhandled error %s", kv(path=request.url.path))
    return JSONResponse(
        status_code=500,
        content={"detail": "サーバ内部でエラーが発生しました。"},
    )


# nginx が /api/ をそのまま転送するため、API は /api 配下に置く。
app.include_router(router, prefix="/api")


@app.get("/health", include_in_schema=False)
async def container_health() -> dict[str, str]:
    """コンテナのヘルスチェック用。

    以前はルータ全体を prefix 無しでも登録していたが、それだと
    プリセットやモデルの更新系まで /api 無しで叩けてしまい、
    公開する面が無駄に増えていた。必要なのはここだけ。
    """
    return {"status": "ok"}
