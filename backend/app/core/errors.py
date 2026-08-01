"""ドメイン例外。

URL 1 件の処理は「失敗しても他の URL を巻き込まない」ことが要件なので、
失敗理由を型で表現し、パイプライン側でユーザ向けメッセージに変換する。
"""

from __future__ import annotations


class ExtractionError(Exception):
    """URL 1 件の処理失敗を表す基底例外。

    Attributes:
        code:    機械可読なエラー種別。
        message: 画面にそのまま出せる日本語メッセージ。
    """

    code = "extraction_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidUrlError(ExtractionError):
    """URL の形式が不正、または許可されないスキーム/ホスト。"""

    code = "invalid_url"


class FetchTimeoutError(ExtractionError):
    """HTML 取得がタイムアウトした。"""

    code = "fetch_timeout"


class FetchError(ExtractionError):
    """HTML 取得に失敗した (DNS/接続/ステータス異常/巨大ページ等)。"""

    code = "fetch_error"


class ContentExtractionError(ExtractionError):
    """HTML から本文を取り出せなかった。"""

    code = "content_extraction_error"


class LlmError(ExtractionError):
    """LLM への接続・応答に失敗した。"""

    code = "llm_error"


class LlmResponseParseError(ExtractionError):
    """LLM 応答を JSON として解釈できなかった。"""

    code = "llm_parse_error"

