"""API のリクエスト / レスポンススキーマ。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 原文一致検証の結果。運用時の切り分けと UI のバッジ表示に使う。
MatchMethod = Literal["exact", "contained", "fuzzy", "nearest", "unverified", "none"]


# ---------------------------------------------------------------------------
# 抽出
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    """POST /api/extract の入力。"""

    urls: list[str] = Field(
        ...,
        min_length=1,
        description="対象となる商品ページの URL 一覧。",
        examples=[["https://example.com/item/1", "https://example.com/item/2"]],
    )
    model_id: str | None = Field(
        default=None,
        description=(
            "使用するモデルの id (models.yml の id)。"
            "未指定なら現在選択されているモデルを使う。不明な id は既定のモデルにフォールバックする。"
        ),
    )
    persona_prompt: str | None = Field(
        default=None,
        description=(
            "文を選ぶ際の観点 (例: 明るい感じ / お年寄り向け)。"
            "未指定 (null または省略) なら保存されている現在の観点を使い、"
            "空文字なら観点なしで抽出する。"
        ),
    )

    @field_validator("urls", mode="after")
    @classmethod
    def _strip_and_drop_blanks(cls, urls: list[str]) -> list[str]:
        """前後の空白を除去し、空行を捨てる。

        重複はここでは落とさない。呼び出し側が投げた URL と結果の対応が
        崩れると画面表示がずれるため、順序と件数はそのまま保つ。
        """
        cleaned = [u.strip() for u in urls]
        cleaned = [u for u in cleaned if u]
        if not cleaned:
            raise ValueError("URL を 1 件以上指定してください。")
        return cleaned

    @field_validator("persona_prompt", mode="after")
    @classmethod
    def _strip_persona(cls, value: str | None) -> str | None:
        """空文字は None に潰さない。

        「保存済みの観点を使う」(None) と「観点なしで抽出する」("") を
        呼び出し側が区別できるようにするため。
        """
        if value is None:
            return None
        return value.strip()


class ExtractResultMeta(BaseModel):
    """1 件あたりの処理メタ情報 (デバッグ・運用向け)。"""

    fetch_ms: float | None = Field(default=None, description="HTML 取得にかかったミリ秒。")
    extract_ms: float | None = Field(default=None, description="本文抽出にかかったミリ秒。")
    llm_ms: float | None = Field(default=None, description="LLM 応答にかかったミリ秒。")
    total_ms: float | None = Field(default=None, description="1 URL の総処理ミリ秒。")
    http_status: int | None = Field(default=None, description="HTML 取得時の HTTP ステータス。")
    extractor: str | None = Field(default=None, description="本文抽出に成功した実装名。")
    candidate_count: int | None = Field(default=None, description="LLM に渡した候補文の件数。")
    candidates: list[str] = Field(
        default_factory=list,
        description="LLM に渡した候補文そのもの (画面で「どれが選択肢だったか」を確認するため)。",
    )
    title_source: str | None = Field(
        default=None, description="商品名の採用元 (og:title / title / h1)。"
    )
    line2_match: MatchMethod | None = Field(default=None, description="line2 の原文一致方式。")
    line3_match: MatchMethod | None = Field(default=None, description="line3 の原文一致方式。")
    persona_applied: bool = Field(
        default=False, description="観点 (ペルソナ) をプロンプトに適用したか。"
    )
    example_count: int | None = Field(
        default=None, description="プロンプトに含めたお手本の件数。"
    )
    llm_model: str | None = Field(default=None, description="使用した LLM モデル名。")
    llm_model_id: str | None = Field(default=None, description="使用したモデル定義の id。")
    llm_model_label: str | None = Field(default=None, description="使用したモデルの表示名。")


class ExtractResult(BaseModel):
    """1 URL 分の抽出結果。

    仕様どおり ``url`` / ``title`` / ``line2`` / ``line3`` を必ず含む。
    失敗時は ``error`` にユーザ向けメッセージが入り、他フィールドは空文字になる。
    """

    url: str = Field(description="入力された URL。")
    title: str = Field(default="", description="商品名 (og:title > title > h1 の優先順)。")
    line2: str = Field(default="", description="商品の魅力を表す原文そのままの一文。")
    line3: str = Field(default="", description="商品の特徴を表す原文そのままの一文。")
    error: str | None = Field(default=None, description="失敗時のメッセージ。成功時は null。")
    error_code: str | None = Field(default=None, description="失敗時のエラー種別コード。")
    meta: ExtractResultMeta = Field(default_factory=ExtractResultMeta)


# ---------------------------------------------------------------------------
# ペルソナ (観点) プリセット
# ---------------------------------------------------------------------------


class PersonaPreset(BaseModel):
    """保存されたプロンプトのプリセット 1 件。"""

    id: str
    name: str = Field(description="一覧に表示する名前。")
    prompt: str = Field(description="LLM に渡す観点の本文。")
    created_at: str = Field(description="作成日時 (ISO 8601)。")


class PersonaPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    prompt: str = Field(min_length=1)

    @field_validator("name", "prompt", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("空白のみの値は保存できません。")
        return cleaned


class ActivePromptUpdate(BaseModel):
    """現在使用中の観点 (保存せずに使う自由入力も含む)。"""

    prompt: str = Field(default="")

    @field_validator("prompt", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class PersonaState(BaseModel):
    """GET /api/personas のレスポンス。"""

    presets: list[PersonaPreset]
    active_prompt: str = Field(description="現在使用中の観点 (空文字なら未指定)。")
    max_presets: int


# ---------------------------------------------------------------------------
# お手本 (👍 で貯まる few-shot 例)
# ---------------------------------------------------------------------------


class Example(BaseModel):
    """お手本 1 件。"""

    id: str
    title: str
    line2: str = ""
    line3: str = ""
    source_url: str | None = None
    builtin: bool = Field(
        default=False, description="最初から用意されているお手本 (削除不可)。"
    )
    created_at: str


class ExampleCreate(BaseModel):
    title: str = Field(min_length=1)
    line2: str = ""
    line3: str = ""
    source_url: str | None = None

    @field_validator("title", "line2", "line3", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _require_a_line(self) -> "ExampleCreate":
        # line2 / line3 のどちらかは必要 (両方空のお手本は意味がない)。
        # field_validator ではなく model_validator にしているのは、
        # 省略されたフィールドの既定値に対しても必ず走らせるため。
        if not self.line2 and not self.line3:
            raise ValueError("line2 または line3 のいずれかを指定してください。")
        return self


class ExampleState(BaseModel):
    """GET /api/examples のレスポンス。"""

    examples: list[Example]
    max_examples: int


# ---------------------------------------------------------------------------
# モデル切り替え
# ---------------------------------------------------------------------------


class ModelInfo(BaseModel):
    """選択できる LLM 1 件。

    **API キーそのものは含めない。** 設定されているかどうかだけを返す。
    """

    id: str
    label: str = Field(description="プルダウンに表示する名前。")
    api_base: str
    model: str = Field(description="サーバに送るモデル名。")
    has_api_key: bool = Field(description="API キーが設定されているか。")
    missing_env_key: bool = Field(
        default=False,
        description="api_key が参照している環境変数が未設定であることを示す。",
    )
    concurrency: int


class ModelState(BaseModel):
    """GET /api/models のレスポンス。"""

    models: list[ModelInfo]
    selected_id: str = Field(description="現在選択されているモデルの id。")


class SelectedModelUpdate(BaseModel):
    model_id: str = Field(min_length=1)

    @field_validator("model_id", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("model_id を指定してください。")
        return cleaned


# ---------------------------------------------------------------------------
# システム
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    llm_api_base: str
    llm_model: str
    max_urls_per_request: int


class LlmPingResponse(BaseModel):
    """ローカル LLM への疎通確認結果。"""

    reachable: bool
    llm_api_base: str
    llm_model: str
    latency_ms: float | None = None
    detail: str | None = None
