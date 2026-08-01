"""依存性の提供。

HTTP セッションと LLM クライアントはアプリ起動時に 1 度だけ生成し、
``app.state`` 経由で使い回す (コネクション再利用とセマフォ共有のため)。
"""

from __future__ import annotations

from fastapi import Request

from app.core.config import Settings
from app.services.model_registry import LlmRegistry
from app.services.pipeline import ExtractionPipeline
from app.services.store import SettingsStore


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_pipeline(request: Request) -> ExtractionPipeline:
    return request.app.state.pipeline


def get_registry(request: Request) -> LlmRegistry:
    return request.app.state.registry


def get_store(request: Request) -> SettingsStore:
    return request.app.state.store
