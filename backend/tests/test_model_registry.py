"""モデル定義ファイルの読み込みと、モデルごとのクライアント管理のテスト。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.model_registry import LlmRegistry, load_model_configs

MODELS_YML = """
default: cloud

models:
  - id: local
    label: ローカルLLM
    api_base: http://host.docker.internal:8080/v1
    model: local-model.gguf
    api_key: ""
    concurrency: 1
    timeout: 180

  - id: cloud
    label: クラウドLLM
    api_base: https://api.example.com/v1/
    model: cloud-model
    api_key: "${TEST_CLOUD_KEY}"
    concurrency: 4
    timeout: 60
    temperature: 0.3
    max_tokens: 512
"""


def _settings(tmp_path: Path, yml: str | None) -> Settings:
    path = tmp_path / "models.yml"
    if yml is not None:
        path.write_text(yml, encoding="utf-8")
    return Settings(
        MODELS_FILE=str(path),
        DATA_DIR=str(tmp_path / "data"),
        LLM_API_BASE="http://env.invalid/v1",
        LLM_MODEL="env-model",
        LLM_API_KEY="env-key",
        LLM_CONCURRENCY=2,
        LLM_TIMEOUT=99,
    )


class TestLoading:
    def test_loads_all_entries_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_CLOUD_KEY", "sk-test-123")
        configs, default_id = load_model_configs(_settings(tmp_path, MODELS_YML))

        assert [c.id for c in configs] == ["local", "cloud"]
        assert default_id == "cloud"

    def test_per_model_settings_override_the_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_CLOUD_KEY", "sk-test-123")
        configs, _ = load_model_configs(_settings(tmp_path, MODELS_YML))
        local, cloud = configs

        assert (local.concurrency, local.timeout) == (1, 180.0)
        assert (cloud.concurrency, cloud.timeout) == (4, 60.0)
        assert cloud.temperature == 0.3
        assert cloud.max_tokens == 512

    def test_omitted_settings_fall_back_to_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_CLOUD_KEY", "sk-test-123")
        configs, _ = load_model_configs(_settings(tmp_path, MODELS_YML))
        local = configs[0]

        # temperature / max_tokens は YAML に書いていないので .env の値
        assert local.temperature == 0.0
        assert local.max_tokens == 1024

    def test_expands_environment_variables_in_the_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_CLOUD_KEY", "sk-test-123")
        configs, _ = load_model_configs(_settings(tmp_path, MODELS_YML))

        assert configs[1].api_key == "sk-test-123"
        assert configs[1].missing_env_key is False

    def test_flags_an_unset_environment_variable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_CLOUD_KEY", raising=False)
        configs, _ = load_model_configs(_settings(tmp_path, MODELS_YML))

        assert configs[1].api_key == ""
        assert configs[1].missing_env_key is True

    def test_trailing_slash_in_api_base_is_removed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_CLOUD_KEY", "x")
        configs, _ = load_model_configs(_settings(tmp_path, MODELS_YML))
        assert configs[1].api_base == "https://api.example.com/v1"


class TestFallbacks:
    """定義ファイルが読めなくても起動できること。"""

    def test_missing_file_falls_back_to_env(self, tmp_path):
        configs, default_id = load_model_configs(_settings(tmp_path, None))

        assert len(configs) == 1
        assert configs[0].api_base == "http://env.invalid/v1"
        assert configs[0].model == "env-model"
        assert configs[0].api_key == "env-key"
        assert default_id == configs[0].id

    def test_a_directory_in_place_of_the_file_falls_back_to_env(self, tmp_path):
        """models.yml が無い状態で docker compose すると、同名のディレクトリが作られる。

        (bind mount のマウント元が存在しないと Docker がディレクトリを作るため)
        その場合でも起動できること。
        """
        (tmp_path / "models.yml").mkdir()
        configs, _ = load_model_configs(_settings(tmp_path, None))

        assert len(configs) == 1
        assert configs[0].model == "env-model"

    def test_empty_file_falls_back_to_env(self, tmp_path):
        configs, _ = load_model_configs(_settings(tmp_path, ""))
        assert configs[0].model == "env-model"

    def test_empty_models_list_falls_back_to_env(self, tmp_path):
        configs, _ = load_model_configs(_settings(tmp_path, "models: []\n"))
        assert configs[0].model == "env-model"

    def test_broken_yaml_falls_back_to_env(self, tmp_path):
        configs, _ = load_model_configs(_settings(tmp_path, "models: [ this is : not : yaml"))
        assert len(configs) == 1
        assert configs[0].model == "env-model"

    def test_file_without_models_list_falls_back_to_env(self, tmp_path):
        configs, _ = load_model_configs(_settings(tmp_path, "default: x\n"))
        assert configs[0].model == "env-model"

    def test_invalid_entry_is_skipped_but_others_survive(self, tmp_path):
        yml = """
models:
  - id: broken
    label: api_base がない
    model: m
  - id: ok
    api_base: http://ok.invalid/v1
    model: ok-model
"""
        configs, default_id = load_model_configs(_settings(tmp_path, yml))
        assert [c.id for c in configs] == ["ok"]
        assert default_id == "ok"

    def test_duplicate_ids_are_skipped(self, tmp_path):
        yml = """
models:
  - id: dup
    api_base: http://a.invalid/v1
    model: a
  - id: dup
    api_base: http://b.invalid/v1
    model: b
"""
        configs, _ = load_model_configs(_settings(tmp_path, yml))
        assert [c.model for c in configs] == ["a"]

    def test_unknown_default_uses_the_first_entry(self, tmp_path):
        yml = """
default: nope
models:
  - id: first
    api_base: http://a.invalid/v1
    model: a
"""
        _, default_id = load_model_configs(_settings(tmp_path, yml))
        assert default_id == "first"


class TestRegistry:
    @pytest.fixture
    def registry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_CLOUD_KEY", "sk-test-123")
        configs, default_id = load_model_configs(_settings(tmp_path, MODELS_YML))
        instance = LlmRegistry(configs, default_id)
        yield instance
        asyncio.run(instance.close())

    def test_returns_a_client_per_model(self, registry):
        assert registry.client("local") is not registry.client("cloud")
        assert registry.client("local").config.model == "local-model.gguf"
        assert registry.client("cloud").config.model == "cloud-model"

    def test_unknown_id_falls_back_to_the_default(self, registry):
        assert registry.resolve_id("nope") == "cloud"
        assert registry.client("nope").config.id == "cloud"

    def test_no_id_uses_the_default(self, registry):
        assert registry.resolve_id(None) == "cloud"

    def test_has(self, registry):
        assert registry.has("local") is True
        assert registry.has("nope") is False

    def test_listed_info_never_contains_the_api_key(self, registry):
        """一覧はフロントエンドに渡るので、キーの値が混ざってはいけない。"""
        infos = registry.list_infos()
        serialized = "".join(info.model_dump_json() for info in infos)

        assert "sk-test-123" not in serialized
        assert [i.id for i in infos] == ["local", "cloud"]
        assert [i.has_api_key for i in infos] == [False, True]
