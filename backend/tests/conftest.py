"""テスト共通のフィクスチャとテストダブル。"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# `app` パッケージをインポートできるようにする (backend/ をパスに追加)。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.schemas import Example  # noqa: E402
from app.services.fetcher import FetchedPage  # noqa: E402
from app.services.llm import LlmSelection  # noqa: E402
from app.services.model_registry import LlmRegistry, ModelConfig  # noqa: E402
from app.services.store import SettingsStore  # noqa: E402

SAMPLE_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>感謝をコメて 魚沼産コシヒカリ | サンプル通販</title>
  <meta property="og:title" content="感謝をコメて 魚沼産コシヒカリ">
  <meta name="description" content="新潟県魚沼産コシヒカリを使用。米の宝石と呼ばれるブレンド米です。">
  <script>var tracking = {id: 1};</script>
  <style>.ad { display: none; }</style>
  <!-- ここはコメントなので本文に含めない -->
</head>
<body>
  <header class="global-header"><nav>トップページ ログイン カートを見る</nav></header>
  <div class="sidebar"><p>人気ランキングはこちら</p></div>
  <div class="ad-banner"><p>期間限定クーポン配布中</p></div>
  <main>
    <h1>感謝をコメて 魚沼産コシヒカリ</h1>
    <div class="item-description">
      <p>新潟県魚沼産コシヒカリを使用。</p>
      <p>米の宝石と呼ばれるブレンド米です。</p>
      <p>粒立ちがよく、冷めてももちもちとした食感が続きます。</p>
      <p>契約農家が丹精込めて育てたお米を、注文後に精米してお届けします。</p>
      <p>ギフトにも喜ばれる化粧箱入りでご用意しました。</p>
    </div>
  </main>
  <footer><p>Copyright 2026 サンプル通販 All rights reserved.</p></footer>
</body>
</html>
"""

NO_CONTENT_HTML = """<!DOCTYPE html>
<html lang="ja"><head><title>404</title></head>
<body><nav>ログイン</nav></body></html>
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """テスト用の設定 (ネットワークには一切出ない)。"""
    return Settings(
        LLM_API_BASE="http://llm.invalid/v1",
        LLM_MODEL="test-model",
        LLM_API_KEY="",
        HTTP_ALLOW_PRIVATE_HOSTS=True,
        LLM_CONCURRENCY=2,
        FETCH_CONCURRENCY=2,
        DATA_DIR=str(tmp_path / "data"),
        # 定義ファイルは使わず、環境変数から 1 件だけ組み立てさせる。
        MODELS_FILE=str(tmp_path / "no-such-models.yml"),
    )


@pytest.fixture
def store(settings: Settings) -> SettingsStore:
    """空の状態から始まる保存領域。"""
    instance = SettingsStore(settings)
    instance.load()
    return instance


def make_registry(llm) -> LlmRegistry:
    """テストダブルの LLM を 1 件だけ持つレジストリを作る。"""
    registry = LlmRegistry.__new__(LlmRegistry)
    config = ModelConfig(
        id="test",
        label="テストモデル",
        api_base="http://llm.invalid/v1",
        model="test-model",
        api_key="",
        timeout=30.0,
        concurrency=2,
        temperature=0.0,
        max_tokens=1024,
    )
    llm.config = config
    registry._configs = {config.id: config}
    registry._order = [config.id]
    registry._default_id = config.id
    registry._clients = {config.id: llm}
    return registry


@dataclass
class FakeFetcher:
    """URL → HTML の辞書を返すだけの取得クライアント。"""

    pages: dict[str, str]
    error: Exception | None = None

    def fetch(self, url: str) -> FetchedPage:
        if self.error is not None:
            raise self.error
        html = self.pages.get(url)
        if html is None:
            from app.core.errors import FetchError

            raise FetchError("HTML の取得に失敗しました (HTTP 404)。")
        return FetchedPage(
            url=url, html=html, status_code=200, encoding="utf-8", elapsed_ms=12.3
        )

    def close(self) -> None:  # pragma: no cover - インタフェース互換のため
        pass


@dataclass
class FakeLlm:
    """固定の選択結果を返す LLM クライアント。"""

    title: str = "感謝をコメて 魚沼産コシヒカリ"
    line2: str = "新潟県魚沼産コシヒカリを使用。"
    line3: str = "米の宝石と呼ばれるブレンド米です。"
    error: Exception | None = None
    calls: list[dict] = field(default_factory=list)
    config: object = None  # make_registry が ModelConfig を差し込む

    async def select(
        self,
        *,
        product_name: str,
        body_for_prompt: str,
        persona_prompt: str | None = None,
        examples: list[Example] | None = None,
    ) -> LlmSelection:
        self.calls.append(
            {
                "product_name": product_name,
                "body": body_for_prompt,
                "persona_prompt": persona_prompt,
                "examples": list(examples or []),
            }
        )
        if self.error is not None:
            raise self.error
        return LlmSelection(
            title=self.title,
            line2=self.line2,
            line3=self.line3,
            raw="{}",
            elapsed_ms=45.6,
        )

    async def ping(self) -> tuple[bool, float | None, str | None]:
        return True, 1.0, None

    async def close(self) -> None:  # pragma: no cover
        pass
