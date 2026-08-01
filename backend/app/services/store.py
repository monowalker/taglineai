"""ペルソナプリセットとお手本の永続化。

規模が小さく単一プロセスなので、RDB は使わず JSON ファイル 1 枚で保持する。
named volume 上に置くのでコンテナを作り直しても残る。

- 書き込みは一時ファイル + ``os.replace`` による**アトミック置換**。
  途中でプロセスが落ちても、壊れた JSON が残らない。
- 同時書き込みは ``asyncio.Lock`` で直列化する。
- ファイルが壊れていた場合は初期状態にフォールバックし、壊れた内容は
  ``.corrupt`` として退避してからログに残す (黙って消さない)。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger, kv
from app.schemas import Example, ExampleCreate, PersonaPreset, PersonaPresetCreate

logger = get_logger(__name__)

_STORE_FILENAME = "store.json"
_SCHEMA_VERSION = 1

# 最初から用意しておくお手本 (仕様の出力例)。削除・上書きはできない。
BUILTIN_EXAMPLES: list[dict[str, Any]] = [
    {
        "id": "builtin-1",
        "title": "感謝をコメて 魚沼産コシヒカリ",
        "line2": "新潟県魚沼産コシヒカリを使用。",
        "line3": "米の宝石と呼ばれるブレンド米です。",
        "source_url": None,
        "builtin": True,
        "created_at": "2026-01-01T00:00:00+00:00",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class StoreError(Exception):
    """保存操作の失敗 (件数上限など、利用者に返すべきもの)。"""


class SettingsStore:
    """ペルソナプリセット / お手本 / 現在の観点 を保持する。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = Path(settings.data_dir) / _STORE_FILENAME
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = self._empty()

    # ---- ライフサイクル ----

    def load(self) -> None:
        """起動時に 1 度だけ呼ぶ。読めなければ初期状態で開始する。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("data dir is not writable %s", kv(path=str(self._path.parent), error=exc))
            self._data = self._empty()
            return

        if not self._path.exists():
            self._data = self._empty()
            logger.info("store initialized %s", kv(path=str(self._path)))
            return

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._quarantine(exc)
            self._data = self._empty()
            return

        self._data = self._migrate(raw)
        logger.info(
            "store loaded %s",
            kv(
                path=str(self._path),
                presets=len(self._data["presets"]),
                examples=len(self._data["examples"]),
            ),
        )

    def _empty(self) -> dict[str, Any]:
        return {
            "version": _SCHEMA_VERSION,
            "presets": [],
            "examples": [],
            "active_prompt": "",
            "selected_model_id": "",
        }

    def _migrate(self, raw: Any) -> dict[str, Any]:
        """将来のスキーマ変更に備え、欠けているキーを補う。"""
        if not isinstance(raw, dict):
            return self._empty()
        data = self._empty()
        for key in ("presets", "examples"):
            value = raw.get(key)
            if isinstance(value, list):
                data[key] = value
        data["examples"] = [self._migrate_example(item) for item in data["examples"]]
        for key in ("active_prompt", "selected_model_id"):
            if isinstance(raw.get(key), str):
                data[key] = raw[key]
        return data

    @staticmethod
    def _migrate_example(item: Any) -> Any:
        """旧スキーマ (catchphrase_parts: list) を line2 / line3 に変換する。"""
        if not isinstance(item, dict) or "catchphrase_parts" not in item:
            return item
        parts = item.pop("catchphrase_parts") or []
        item.setdefault("line2", parts[0] if len(parts) > 0 else "")
        item.setdefault("line3", parts[1] if len(parts) > 1 else "")
        return item

    def _quarantine(self, exc: Exception) -> None:
        """壊れたファイルを退避する (消さずに残す)。"""
        backup = self._path.with_suffix(f".corrupt.{int(time.time())}")
        try:
            self._path.rename(backup)
            logger.error(
                "store file is broken; quarantined %s",
                kv(path=str(self._path), backup=str(backup), error=exc),
            )
        except OSError:
            logger.exception("failed to quarantine broken store file")

    async def _persist(self) -> None:
        """アトミックに書き出す。呼び出し側でロックを取っていること。"""
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self._write_atomic, payload)

    def _write_atomic(self, payload: str) -> None:
        temp = self._path.with_suffix(f".tmp.{os.getpid()}")
        try:
            temp.write_text(payload, encoding="utf-8")
            os.replace(temp, self._path)
        except OSError:
            logger.exception("failed to persist store %s", kv(path=str(self._path)))
            temp.unlink(missing_ok=True)
            raise

    # ---- ペルソナプリセット ----

    def list_presets(self) -> list[PersonaPreset]:
        return [PersonaPreset(**item) for item in self._data["presets"]]

    @property
    def active_prompt(self) -> str:
        return self._data.get("active_prompt", "")

    async def set_active_prompt(self, prompt: str) -> str:
        limit = self._settings.max_persona_prompt_chars
        if len(prompt) > limit:
            raise StoreError(f"観点は {limit} 文字以内で入力してください。")
        async with self._lock:
            self._data["active_prompt"] = prompt
            await self._persist()
        return prompt

    # ---- 選択中のモデル ----

    @property
    def selected_model_id(self) -> str:
        """画面で選ばれているモデルの id (未選択なら空文字)。"""
        return self._data.get("selected_model_id", "")

    async def set_selected_model_id(self, model_id: str) -> str:
        async with self._lock:
            self._data["selected_model_id"] = model_id
            await self._persist()
        logger.info("model selection saved %s", kv(id=model_id))
        return model_id

    async def add_preset(self, payload: PersonaPresetCreate) -> PersonaPreset:
        limit = self._settings.max_persona_prompt_chars
        if len(payload.prompt) > limit:
            raise StoreError(f"観点は {limit} 文字以内で入力してください。")

        async with self._lock:
            presets = self._data["presets"]
            if len(presets) >= self._settings.max_persona_presets:
                raise StoreError(
                    f"保存できるプリセットは {self._settings.max_persona_presets} 件までです。"
                    "不要なものを削除してください。"
                )
            if any(item["prompt"] == payload.prompt for item in presets):
                raise StoreError("同じ内容のプリセットが既に保存されています。")

            preset = PersonaPreset(
                id=_new_id(),
                name=payload.name,
                prompt=payload.prompt,
                created_at=_now(),
            )
            presets.append(preset.model_dump())
            await self._persist()

        logger.info("persona preset added %s", kv(id=preset.id, name=preset.name))
        return preset

    async def delete_preset(self, preset_id: str) -> bool:
        async with self._lock:
            presets = self._data["presets"]
            remaining = [item for item in presets if item["id"] != preset_id]
            if len(remaining) == len(presets):
                return False
            self._data["presets"] = remaining
            await self._persist()
        logger.info("persona preset deleted %s", kv(id=preset_id))
        return True

    # ---- お手本 ----

    def list_examples(self, *, include_builtin: bool = True) -> list[Example]:
        """新しい順に返す。組み込みのお手本は末尾に置く。"""
        saved = [Example(**item) for item in reversed(self._data["examples"])]
        if not include_builtin:
            return saved
        return saved + [Example(**item) for item in BUILTIN_EXAMPLES]

    async def add_example(self, payload: ExampleCreate) -> Example:
        async with self._lock:
            examples = self._data["examples"]
            key = self._example_key(payload.title, payload.line2, payload.line3)

            # 同じ内容が既にあれば、重複させず最新として並べ直す。
            examples = [
                item
                for item in examples
                if self._example_key(
                    item.get("title", ""), item.get("line2", ""), item.get("line3", "")
                )
                != key
            ]

            example = Example(
                id=_new_id(),
                title=payload.title,
                line2=payload.line2,
                line3=payload.line3,
                source_url=payload.source_url,
                builtin=False,
                created_at=_now(),
            )
            examples.append(example.model_dump())

            # 直近 N 件だけを残すリングバッファ。
            overflow = len(examples) - self._settings.max_examples
            if overflow > 0:
                dropped = examples[:overflow]
                examples = examples[overflow:]
                logger.info(
                    "examples trimmed %s",
                    kv(dropped=len(dropped), kept=len(examples)),
                )

            self._data["examples"] = examples
            await self._persist()

        logger.info("example added %s", kv(id=example.id, title=example.title[:40]))
        return example

    async def delete_example(self, example_id: str) -> bool:
        async with self._lock:
            examples = self._data["examples"]
            remaining = [item for item in examples if item["id"] != example_id]
            if len(remaining) == len(examples):
                return False
            self._data["examples"] = remaining
            await self._persist()
        logger.info("example deleted %s", kv(id=example_id))
        return True

    @staticmethod
    def _example_key(title: str, line2: str, line3: str) -> str:
        """内容の同一性を判定するキー (空白差を無視する)。"""
        return "".join(f"{title} {line2} {line3}".split())

    # ---- プロンプト組み立て用 ----

    def prompt_examples(self) -> list[Example]:
        """LLM に渡すお手本 (新しい順に上限まで)。"""
        limit = self._settings.max_prompt_examples
        if limit <= 0:
            return []
        return self.list_examples(include_builtin=True)[:limit]
