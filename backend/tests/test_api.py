"""REST API のテスト。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.errors import LlmError
from app.main import app
from app.services.pipeline import ExtractionPipeline
from tests.conftest import SAMPLE_HTML, FakeFetcher, FakeLlm, make_registry

URL = "https://example.com/item/1"


@pytest.fixture
def client(settings, store):
    """テストダブルを差し込んだ API クライアント。"""
    with TestClient(app) as test_client:
        # lifespan で組み立てられた本物の依存を、テスト用に差し替える。
        test_client.app.state.settings = settings
        test_client.app.state.store = store
        registry = make_registry(FakeLlm())
        test_client.app.state.registry = registry
        test_client.app.state.pipeline = ExtractionPipeline(
            settings, FakeFetcher({URL: SAMPLE_HTML}), registry, store
        )
        yield test_client


class TestHealth:
    def test_returns_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["llm_model"] == "test-model"

    def test_exposes_the_url_limit(self, client):
        assert client.get("/api/health").json()["max_urls_per_request"] == 20

    def test_available_without_api_prefix_for_container_healthcheck(self, client):
        assert client.get("/health").status_code == 200


class TestLlmPing:
    def test_reports_reachability(self, client):
        response = client.get("/api/llm/ping")
        assert response.status_code == 200
        assert response.json()["reachable"] is True


class TestExtract:
    def test_returns_result_per_url(self, client):
        response = client.post("/api/extract", json={"urls": [URL]})

        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 1

        item = body[0]
        assert item["url"] == URL
        assert item["title"] == "感謝をコメて 魚沼産コシヒカリ"
        assert item["line2"] == "新潟県魚沼産コシヒカリを使用。"
        assert item["line3"] == "米の宝石と呼ばれるブレンド米です。"
        assert item["error"] is None

    def test_response_contains_required_fields(self, client):
        item = client.post("/api/extract", json={"urls": [URL]}).json()[0]
        assert {"url", "title", "line2", "line3"} <= set(item)

    def test_returns_the_candidate_sentences(self, client):
        """画面で「どれが選択肢だったか」を出せるよう、候補文も返す。"""
        meta = client.post("/api/extract", json={"urls": [URL]}).json()[0]["meta"]

        assert len(meta["candidates"]) == meta["candidate_count"]
        assert "新潟県魚沼産コシヒカリを使用。" in meta["candidates"]

    def test_failed_urls_have_no_candidates(self, client):
        meta = client.post("/api/extract", json={"urls": ["ftp://x.com"]}).json()[0]["meta"]
        assert meta["candidates"] == []

    def test_multiple_urls_keep_input_order(self, client):
        urls = [URL, "https://example.com/missing", URL]
        body = client.post("/api/extract", json={"urls": urls}).json()

        assert [item["url"] for item in body] == urls
        assert body[0]["error"] is None
        assert body[1]["error"] is not None
        assert body[2]["error"] is None

    def test_blank_lines_are_ignored(self, client):
        body = client.post("/api/extract", json={"urls": ["  ", URL, ""]}).json()
        assert len(body) == 1
        assert body[0]["url"] == URL

    def test_persona_prompt_is_accepted(self, client):
        response = client.post(
            "/api/extract", json={"urls": [URL], "persona_prompt": "明るい感じ"}
        )
        assert response.status_code == 200
        assert response.json()[0]["meta"]["persona_applied"] is True

    def test_empty_persona_prompt_means_no_persona(self, client):
        """空文字を送ると、保存済みの観点を使わずに抽出する。"""
        client.put("/api/personas/active", json={"prompt": "保存された観点"})

        item = client.post("/api/extract", json={"urls": [URL], "persona_prompt": ""}).json()[0]
        assert item["meta"]["persona_applied"] is False

    def test_omitted_persona_prompt_falls_back_to_the_saved_one(self, client):
        client.put("/api/personas/active", json={"prompt": "保存された観点"})

        item = client.post("/api/extract", json={"urls": [URL]}).json()[0]
        assert item["meta"]["persona_applied"] is True

    def test_invalid_url_returns_per_url_error_not_500(self, client):
        item = client.post("/api/extract", json={"urls": ["ftp://example.com"]}).json()[0]
        assert item["error_code"] == "invalid_url"
        assert item["title"] == ""

    def test_llm_failure_returns_per_url_error(self, client, settings, store):
        client.app.state.pipeline = ExtractionPipeline(
            settings,
            FakeFetcher({URL: SAMPLE_HTML}),
            make_registry(FakeLlm(error=LlmError("LLM に接続できませんでした。"))),
            store,
        )
        item = client.post("/api/extract", json={"urls": [URL]}).json()[0]
        assert item["error_code"] == "llm_error"

    def test_empty_url_list_is_rejected(self, client):
        assert client.post("/api/extract", json={"urls": []}).status_code == 422

    def test_all_blank_urls_are_rejected(self, client):
        assert client.post("/api/extract", json={"urls": ["  ", ""]}).status_code == 422

    def test_missing_field_is_rejected(self, client):
        response = client.post("/api/extract", json={})
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_too_many_urls_are_rejected(self, client, settings):
        urls = [f"https://example.com/item/{i}" for i in range(settings.max_urls_per_request + 1)]
        response = client.post("/api/extract", json={"urls": urls})
        assert response.status_code == 400
        assert "件までです" in response.json()["detail"]

    def test_request_id_header_is_returned(self, client):
        response = client.get("/api/health")
        assert response.headers.get("X-Request-ID")


class TestPersonaApi:
    def test_starts_empty(self, client):
        body = client.get("/api/personas").json()
        assert body["presets"] == []
        assert body["active_prompt"] == ""
        assert body["max_presets"] == 5

    def test_creates_and_lists_a_preset(self, client):
        created = client.post(
            "/api/personas", json={"name": "明るい", "prompt": "明るく前向きな文を選ぶ"}
        )
        assert created.status_code == 201
        assert created.json()["name"] == "明るい"

        presets = client.get("/api/personas").json()["presets"]
        assert [p["name"] for p in presets] == ["明るい"]

    def test_rejects_more_than_five_presets(self, client):
        for i in range(5):
            assert (
                client.post("/api/personas", json={"name": f"p{i}", "prompt": f"観点{i}"}).status_code
                == 201
            )
        response = client.post("/api/personas", json={"name": "over", "prompt": "溢れる"})
        assert response.status_code == 400
        assert "5 件までです" in response.json()["detail"]

    def test_rejects_blank_name_or_prompt(self, client):
        assert client.post("/api/personas", json={"name": "  ", "prompt": "x"}).status_code == 422
        assert client.post("/api/personas", json={"name": "x", "prompt": "  "}).status_code == 422

    def test_deletes_a_preset(self, client):
        preset_id = client.post(
            "/api/personas", json={"name": "消す", "prompt": "削除される観点"}
        ).json()["id"]

        assert client.delete(f"/api/personas/{preset_id}").status_code == 204
        assert client.get("/api/personas").json()["presets"] == []

    def test_deleting_an_unknown_preset_returns_404(self, client):
        assert client.delete("/api/personas/nope").status_code == 404

    def test_sets_the_active_prompt_without_saving_a_preset(self, client):
        response = client.put("/api/personas/active", json={"prompt": "その場の観点"})
        assert response.status_code == 200
        body = response.json()
        assert body["active_prompt"] == "その場の観点"
        assert body["presets"] == []

    def test_clears_the_active_prompt(self, client):
        client.put("/api/personas/active", json={"prompt": "観点"})
        assert client.put("/api/personas/active", json={"prompt": ""}).json()["active_prompt"] == ""


class TestExampleApi:
    def test_builtin_example_is_listed(self, client):
        body = client.get("/api/examples").json()
        assert body["max_examples"] == 10
        assert any(e["builtin"] for e in body["examples"])

    def test_adds_an_example(self, client):
        response = client.post(
            "/api/examples",
            json={
                "title": "テスト商品",
                "line2": "すばらしい商品です。",
                "source_url": URL,
            },
        )
        assert response.status_code == 201
        assert response.json()["builtin"] is False

        examples = client.get("/api/examples").json()["examples"]
        assert examples[0]["title"] == "テスト商品"

    def test_keeps_only_the_most_recent_ten(self, client):
        for i in range(13):
            client.post(
                "/api/examples",
                json={"title": f"商品{i}", "line2": f"説明文{i}です。"},
            )

        saved = [e for e in client.get("/api/examples").json()["examples"] if not e["builtin"]]
        assert len(saved) == 10
        assert saved[0]["title"] == "商品12"
        assert all(e["title"] != "商品0" for e in saved)

    def test_rejects_empty_catchphrase(self, client):
        response = client.post("/api/examples", json={"title": "商品", "line2": "  "})
        assert response.status_code == 422

    def test_deletes_an_example(self, client):
        example_id = client.post(
            "/api/examples", json={"title": "消す商品", "line2": "消える説明文です。"}
        ).json()["id"]

        assert client.delete(f"/api/examples/{example_id}").status_code == 204
        saved = [e for e in client.get("/api/examples").json()["examples"] if not e["builtin"]]
        assert saved == []

    def test_builtin_examples_cannot_be_deleted(self, client):
        builtin = [e for e in client.get("/api/examples").json()["examples"] if e["builtin"]][0]
        assert client.delete(f"/api/examples/{builtin['id']}").status_code == 404
