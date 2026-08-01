"""ロギング設定。

要件で求められている
  - アクセスログ
  - エラーログ
  - LLM 応答時間
  - HTML 取得時間
をすべて 1 つのフォーマットで出力する。行頭にリクエスト ID が付くので、
同時実行しても 1 リクエスト分のログを追える。
"""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid
from typing import Any

# リクエスト単位の相関 ID。ミドルウェアが設定し、全ロガーが参照する。
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """すべての LogRecord に ``request_id`` を注入する。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.request_id = request_id_var.get()
        return True


_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO") -> None:
    """ルートロガーと uvicorn のロガーを統一フォーマットに揃える。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn / gunicorn 系のロガーを root に委譲させ、二重出力を防ぐ。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    # 依存ライブラリの冗長ログを抑制する。
    for name in ("urllib3", "trafilatura", "httpx", "httpcore", "openai", "charset_normalizer"):
        logging.getLogger(name).setLevel(logging.WARNING)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def kv(**fields: Any) -> str:
    """``key=value`` 形式でログ用の文字列を組み立てる。

    値に空白が含まれる場合のみ引用符で囲む。grep しやすさを優先した簡易実装。
    """
    parts = []
    for key, value in fields.items():
        if value is None:
            text = "-"
        elif isinstance(value, float):
            text = f"{value:.1f}"
        else:
            text = str(value)
        if any(c.isspace() for c in text):
            text = '"' + text.replace('"', "'") + '"'
        parts.append(f"{key}={text}")
    return " ".join(parts)
