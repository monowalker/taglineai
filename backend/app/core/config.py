"""アプリケーション設定。

すべて環境変数から読み込む (12-factor)。既定値はローカル LLM を
``http://host.docker.internal:8080/v1`` で動かす想定に合わせてある。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- アプリ ----
    app_name: str = "商品キャッチフレーズ抽出 API"
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_urls_per_request: int = Field(default=20, ge=1, le=200, alias="MAX_URLS_PER_REQUEST")

    # ---- LLM (OpenAI 互換) ----
    # 使用できるモデルの定義ファイル。無ければ下の LLM_* から 1 件だけ組み立てる。
    models_file: str = Field(default="/app/models.yml", alias="MODELS_FILE")
    llm_api_base: str = Field(default="http://host.docker.internal:8080/v1", alias="LLM_API_BASE")
    llm_model: str = Field(default="local", alias="LLM_MODEL")
    # APIキー不要なサーバでは空文字のままでよい。
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_timeout: float = Field(default=180.0, gt=0, alias="LLM_TIMEOUT")
    llm_concurrency: int = Field(default=1, ge=1, le=32, alias="LLM_CONCURRENCY")
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=1024, ge=64, le=8192, alias="LLM_MAX_TOKENS")

    # ---- HTML 取得 ----
    http_timeout: float = Field(default=15.0, gt=0, alias="HTTP_TIMEOUT")
    http_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        alias="HTTP_USER_AGENT",
    )
    http_max_bytes: int = Field(default=5_000_000, ge=10_000, alias="HTTP_MAX_BYTES")
    fetch_concurrency: int = Field(default=4, ge=1, le=32, alias="FETCH_CONCURRENCY")
    http_max_redirects: int = Field(default=5, ge=0, le=20, alias="HTTP_MAX_REDIRECTS")
    # 既定では SSRF 対策としてプライベート/ループバック宛の取得を拒否する。
    # ローカルの検証用ページを読ませたい場合のみ true にする。
    http_allow_private_hosts: bool = Field(default=False, alias="HTTP_ALLOW_PRIVATE_HOSTS")

    # ---- 前処理 ----
    min_sentence_chars: int = Field(default=8, ge=1, alias="MIN_SENTENCE_CHARS")
    max_sentence_chars: int = Field(default=140, ge=20, alias="MAX_SENTENCE_CHARS")
    max_candidates: int = Field(default=120, ge=5, alias="MAX_CANDIDATES")
    max_body_chars: int = Field(default=6000, ge=200, alias="MAX_BODY_CHARS")

    # ---- 原文一致検証 ----
    verify_min_ratio: float = Field(default=0.75, ge=0.0, le=1.0, alias="VERIFY_MIN_RATIO")
    # 検証に通らなかった LLM 出力について「最も近い候補文」を採用する最低類似度。
    # VERIFY_MIN_RATIO より低くすること (高くするとこの段は働かない)。
    fallback_min_ratio: float = Field(default=0.5, ge=0.0, le=1.0, alias="FALLBACK_MIN_RATIO")

    # ---- 保存データ (ペルソナプリセット / お手本) ----
    data_dir: str = Field(default="/data", alias="DATA_DIR")
    max_persona_presets: int = Field(default=5, ge=1, le=50, alias="MAX_PERSONA_PRESETS")
    max_examples: int = Field(default=10, ge=1, le=100, alias="MAX_EXAMPLES")
    # LLM に渡すお手本の最大件数 (多すぎるとプロンプトが膨らむ)。
    max_prompt_examples: int = Field(default=6, ge=0, le=30, alias="MAX_PROMPT_EXAMPLES")
    max_persona_prompt_chars: int = Field(default=400, ge=10, alias="MAX_PERSONA_PROMPT_CHARS")

    @field_validator("llm_api_base")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """プロセス内で 1 度だけ評価される設定インスタンス。"""
    return Settings()
