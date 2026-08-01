"""使用できる LLM の定義と、モデルごとのクライアント管理。

``models.yml`` に列挙されたモデルを読み込み、id で引ける形に保持する。
モデルごとに ``LlmClient`` を 1 つ持つので、同時実行数やタイムアウトを
「ローカルは 1 並列で 3 分、クラウドは 4 並列で 1 分」のように別々に設定できる。

**API キーはこのモジュールの外に出さない。** 一覧を返す ``ModelInfo`` には
キーの有無しか含めず、値そのものはフロントエンドに渡らない。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings
from app.core.logging import get_logger, kv
from app.schemas import ModelInfo

logger = get_logger(__name__)

# api_key に書ける環境変数参照 (${NAME} 形式)。
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 定義ファイルが無いときに使う、環境変数だけから作る 1 件分の id。
_ENV_MODEL_ID = "default"


@dataclass(frozen=True)
class ModelConfig:
    """1 つの LLM の接続設定。"""

    id: str
    label: str
    api_base: str
    model: str
    api_key: str
    timeout: float
    concurrency: int
    temperature: float
    max_tokens: int
    # api_key に書かれた環境変数が未設定だった場合に True。UI で警告を出す。
    missing_env_key: bool = False

    def to_info(self) -> ModelInfo:
        """フロントエンドに返す形。**API キーは含めない。**"""
        return ModelInfo(
            id=self.id,
            label=self.label,
            api_base=self.api_base,
            model=self.model,
            has_api_key=bool(self.api_key),
            missing_env_key=self.missing_env_key,
            concurrency=self.concurrency,
        )


class ModelConfigError(Exception):
    """定義ファイルの内容が不正。"""


def _expand_env(value: str) -> tuple[str, bool]:
    """``${NAME}`` を環境変数の値に置き換える。

    Returns:
        (展開後の文字列, 未設定の参照があったか)
    """
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        name = match.group(1)
        resolved = os.environ.get(name, "")
        if not resolved:
            missing = True
        return resolved

    return _ENV_REF_RE.sub(replace, value), missing


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _model_from_env(settings: Settings) -> ModelConfig:
    """定義ファイルが無い場合に、環境変数から 1 件だけ組み立てる。

    既存の設定 (LLM_API_BASE / LLM_MODEL / LLM_API_KEY) のまま動かせるように
    しておくための後方互換。
    """
    return ModelConfig(
        id=_ENV_MODEL_ID,
        label=settings.llm_model or "既定のモデル",
        api_base=settings.llm_api_base,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout,
        concurrency=settings.llm_concurrency,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )


def _parse_entry(raw: Any, settings: Settings, index: int) -> ModelConfig:
    """models.yml の 1 エントリを ModelConfig にする。"""
    if not isinstance(raw, dict):
        raise ModelConfigError(f"models[{index}] はマッピングである必要があります。")

    model_id = _as_str(raw.get("id"))
    api_base = _as_str(raw.get("api_base"))
    model = _as_str(raw.get("model"))
    if not model_id:
        raise ModelConfigError(f"models[{index}] に id がありません。")
    if not api_base:
        raise ModelConfigError(f"モデル '{model_id}' に api_base がありません。")
    if not model:
        raise ModelConfigError(f"モデル '{model_id}' に model がありません。")

    api_key, missing_env_key = _expand_env(_as_str(raw.get("api_key")))
    if missing_env_key:
        logger.warning(
            "model references an unset environment variable for its api key %s",
            kv(model=model_id, api_key=_as_str(raw.get("api_key"))),
        )

    return ModelConfig(
        id=model_id,
        label=_as_str(raw.get("label")) or model_id,
        api_base=api_base.rstrip("/"),
        model=model,
        api_key=api_key,
        timeout=float(raw.get("timeout") or settings.llm_timeout),
        concurrency=int(raw.get("concurrency") or settings.llm_concurrency),
        temperature=(
            settings.llm_temperature
            if raw.get("temperature") is None
            else float(raw["temperature"])
        ),
        max_tokens=int(raw.get("max_tokens") or settings.llm_max_tokens),
        missing_env_key=missing_env_key,
    )


def load_model_configs(settings: Settings) -> tuple[list[ModelConfig], str]:
    """``models.yml`` を読み込む。

    ファイルが無い / 壊れている場合は環境変数から 1 件だけ作って続行する
    (起動できなくなるより、既定のモデルで動くほうがよい)。

    Returns:
        (モデル定義のリスト, 既定モデルの id)
    """
    path = Path(settings.models_file)

    if not path.is_file():
        logger.warning(
            "models file not found; falling back to environment variables %s",
            kv(path=str(path)),
        )
        fallback = _model_from_env(settings)
        return [fallback], fallback.id

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.error("failed to read models file %s", kv(path=str(path), error=exc))
        fallback = _model_from_env(settings)
        return [fallback], fallback.id

    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        logger.error(
            "models file must have a top level 'models' list %s", kv(path=str(path))
        )
        fallback = _model_from_env(settings)
        return [fallback], fallback.id

    configs: list[ModelConfig] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw["models"]):
        try:
            config = _parse_entry(entry, settings, index)
        except (ModelConfigError, TypeError, ValueError) as exc:
            # 1 件の記述ミスで全部使えなくなると困るので、その行だけ捨てる。
            logger.error("skipping invalid model entry %s", kv(index=index, error=exc))
            continue
        if config.id in seen:
            logger.error("skipping duplicate model id %s", kv(id=config.id))
            continue
        seen.add(config.id)
        configs.append(config)

    if not configs:
        logger.error(
            "models file contained no usable entry; falling back to environment variables"
        )
        fallback = _model_from_env(settings)
        return [fallback], fallback.id

    default_id = _as_str(raw.get("default"))
    if default_id not in seen:
        if default_id:
            logger.warning(
                "default model is not defined; using the first entry %s",
                kv(default=default_id, using=configs[0].id),
            )
        default_id = configs[0].id

    logger.info(
        "models loaded %s",
        kv(path=str(path), count=len(configs), default=default_id,
           ids=",".join(c.id for c in configs)),
    )
    return configs, default_id


class LlmRegistry:
    """モデル id → LlmClient の対応を保持する。"""

    def __init__(self, configs: list[ModelConfig], default_id: str) -> None:
        # 循環インポートを避けるためここで取り込む。
        from app.services.llm import LlmClient

        self._configs = {config.id: config for config in configs}
        self._order = [config.id for config in configs]
        self._default_id = default_id
        self._clients = {config.id: LlmClient(config) for config in configs}

    @property
    def default_id(self) -> str:
        return self._default_id

    def list_infos(self) -> list[ModelInfo]:
        """一覧表示用 (API キーを含まない)。"""
        return [self._configs[model_id].to_info() for model_id in self._order]

    def has(self, model_id: str) -> bool:
        return model_id in self._clients

    def config(self, model_id: str | None = None):
        return self._configs[self.resolve_id(model_id)]

    def client(self, model_id: str | None = None):
        """指定 id のクライアント。未知の id なら既定のモデルを返す。"""
        return self._clients[self.resolve_id(model_id)]

    def resolve_id(self, model_id: str | None) -> str:
        """使用するモデル id を決める。未知・未指定なら既定。"""
        if model_id and model_id in self._clients:
            return model_id
        if model_id:
            logger.warning(
                "unknown model id; falling back to the default %s",
                kv(requested=model_id, default=self._default_id),
            )
        return self._default_id

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
