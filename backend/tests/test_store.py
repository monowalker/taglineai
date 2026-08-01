"""ペルソナプリセット / お手本 の永続化のテスト。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.schemas import ExampleCreate, PersonaPresetCreate
from app.services.store import SettingsStore, StoreError


def _persona(name: str, prompt: str) -> PersonaPresetCreate:
    return PersonaPresetCreate(name=name, prompt=prompt)


def _example(title: str, line2: str) -> ExampleCreate:
    return ExampleCreate(title=title, line2=line2)


class TestPersonaPresets:
    def test_saves_and_lists_presets(self, store):
        asyncio.run(store.add_preset(_persona("明るい", "明るく前向きな印象の文を選ぶ")))
        asyncio.run(store.add_preset(_persona("シニア", "お年寄りにも分かりやすい文を選ぶ")))

        presets = store.list_presets()
        assert [p.name for p in presets] == ["明るい", "シニア"]
        assert presets[0].prompt == "明るく前向きな印象の文を選ぶ"
        assert presets[0].id

    def test_rejects_more_than_the_limit(self, store, settings):
        for i in range(settings.max_persona_presets):
            asyncio.run(store.add_preset(_persona(f"preset{i}", f"観点{i}")))

        with pytest.raises(StoreError) as excinfo:
            asyncio.run(store.add_preset(_persona("over", "溢れる観点")))
        assert "5 件までです" in str(excinfo.value)

    def test_rejects_duplicate_prompt(self, store):
        asyncio.run(store.add_preset(_persona("A", "同じ観点")))
        with pytest.raises(StoreError):
            asyncio.run(store.add_preset(_persona("B", "同じ観点")))

    def test_rejects_too_long_prompt(self, store, settings):
        long_prompt = "あ" * (settings.max_persona_prompt_chars + 1)
        with pytest.raises(StoreError):
            asyncio.run(store.add_preset(_persona("長い", long_prompt)))

    def test_deletes_a_preset(self, store):
        preset = asyncio.run(store.add_preset(_persona("消す", "削除される観点")))
        assert asyncio.run(store.delete_preset(preset.id)) is True
        assert store.list_presets() == []

    def test_delete_returns_false_for_unknown_id(self, store):
        assert asyncio.run(store.delete_preset("nope")) is False


class TestActivePrompt:
    def test_defaults_to_empty(self, store):
        assert store.active_prompt == ""

    def test_stores_free_text_without_saving_a_preset(self, store):
        asyncio.run(store.set_active_prompt("その場で入力した観点"))
        assert store.active_prompt == "その場で入力した観点"
        assert store.list_presets() == []  # プリセットは増えない

    def test_can_be_cleared(self, store):
        asyncio.run(store.set_active_prompt("観点"))
        asyncio.run(store.set_active_prompt(""))
        assert store.active_prompt == ""


class TestExamples:
    def test_builtin_example_is_always_present(self, store):
        examples = store.list_examples()
        assert any(e.builtin for e in examples)

    def test_adds_a_user_example_before_the_builtin_ones(self, store):
        asyncio.run(_example_add(store, "テスト商品", "すばらしい商品です。"))
        examples = store.list_examples()
        assert examples[0].title == "テスト商品"
        assert examples[0].builtin is False
        assert examples[-1].builtin is True

    def test_keeps_only_the_most_recent_n(self, store, settings):
        for i in range(settings.max_examples + 5):
            asyncio.run(_example_add(store, f"商品{i}", f"説明文{i}です。"))

        saved = store.list_examples(include_builtin=False)
        assert len(saved) == settings.max_examples
        # 新しい順に並び、最も古いものは消えている
        assert saved[0].title == f"商品{settings.max_examples + 4}"
        assert all(e.title != "商品0" for e in saved)

    def test_adding_the_same_content_twice_does_not_duplicate(self, store):
        asyncio.run(_example_add(store, "同じ商品", "同じ説明文です。"))
        asyncio.run(_example_add(store, "同じ商品", "同じ説明文です。"))
        assert len(store.list_examples(include_builtin=False)) == 1

    def test_deletes_a_user_example(self, store):
        created = asyncio.run(_example_add(store, "消す商品", "消える説明文です。"))
        assert asyncio.run(store.delete_example(created.id)) is True
        assert store.list_examples(include_builtin=False) == []

    def test_builtin_examples_cannot_be_deleted(self, store):
        builtin = [e for e in store.list_examples() if e.builtin][0]
        assert asyncio.run(store.delete_example(builtin.id)) is False

    def test_prompt_examples_are_capped(self, store, settings):
        for i in range(settings.max_examples):
            asyncio.run(_example_add(store, f"商品{i}", f"説明文{i}です。"))
        assert len(store.prompt_examples()) <= settings.max_prompt_examples


class TestPersistence:
    def test_survives_a_restart(self, settings):
        first = SettingsStore(settings)
        first.load()
        asyncio.run(first.add_preset(_persona("残る", "再起動しても残る観点")))
        asyncio.run(first.set_active_prompt("現在の観点"))
        asyncio.run(_example_add(first, "残る商品", "残る説明文です。"))

        second = SettingsStore(settings)
        second.load()
        assert [p.name for p in second.list_presets()] == ["残る"]
        assert second.active_prompt == "現在の観点"
        assert second.list_examples(include_builtin=False)[0].title == "残る商品"

    def test_broken_file_is_quarantined_and_does_not_crash(self, settings):
        path = Path(settings.data_dir) / "store.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")

        store = SettingsStore(settings)
        store.load()

        assert store.list_presets() == []
        # 壊れた内容は消さずに退避される
        assert list(path.parent.glob("store.corrupt.*"))

    def test_written_file_is_valid_json(self, settings):
        store = SettingsStore(settings)
        store.load()
        asyncio.run(store.add_preset(_persona("JSON", "検証用の観点")))

        raw = json.loads((Path(settings.data_dir) / "store.json").read_text(encoding="utf-8"))
        assert raw["presets"][0]["name"] == "JSON"

    def test_unwritable_data_dir_does_not_crash_startup(self, tmp_path):
        # ディレクトリではなくファイルを data_dir に指定して、作成を失敗させる
        blocker = tmp_path / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        settings = Settings(DATA_DIR=str(blocker / "data"))

        store = SettingsStore(settings)
        store.load()  # 例外を投げない

        assert store.list_presets() == []


async def _example_add(store: SettingsStore, title: str, line2: str):
    return await store.add_example(_example(title, line2))
