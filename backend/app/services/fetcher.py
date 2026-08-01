"""HTML 取得。

requests を使い、タイムアウト・User-Agent・サイズ上限・文字コード判定を
まとめて面倒を見る。日本語の商品ページは Shift_JIS / EUC-JP が今なお残るため、
デコードは「ヘッダ → meta タグ → 統計的推定 → UTF-8」の順で決定する。
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    HTTPError,
    RequestException,
    TooManyRedirects,
)
from requests.exceptions import Timeout as RequestsTimeout

from app.core.config import Settings
from app.core.errors import FetchError, FetchTimeoutError, InvalidUrlError
from app.core.logging import get_logger, kv

logger = get_logger(__name__)

_ALLOWED_SCHEMES = ("http", "https")

# HTML 冒頭から文字コード宣言を拾うためのパターン (bytes のまま走査する)。
_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-:.]+)""", re.I)
_XML_DECL_RE = re.compile(rb"""<\?xml[^>]+encoding\s*=\s*["']([a-zA-Z0-9_\-:.]+)["']""", re.I)

# requests が Content-Type に charset が無いときに使う既定値。
# これが返ってきた場合は「サーバは何も言っていない」とみなす。
_REQUESTS_DEFAULT_ENCODING = "ISO-8859-1"

# HTML として扱う Content-Type。
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "application/xml", "text/xml")


@dataclass(frozen=True)
class FetchedPage:
    """取得した 1 ページ分の生データ。"""

    url: str          # リダイレクト後の最終 URL
    html: str         # デコード済み HTML
    status_code: int
    encoding: str
    elapsed_ms: float


def validate_url(raw_url: str) -> str:
    """URL の形式を検証して正規化する。

    Raises:
        InvalidUrlError: スキームやホストが不正な場合。
    """
    url = (raw_url or "").strip()
    if not url:
        raise InvalidUrlError("URL が空です。")
    if len(url) > 2048:
        raise InvalidUrlError("URL が長すぎます (2048 文字以内)。")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise InvalidUrlError("URL は http:// または https:// で始まる必要があります。")
    if not parsed.hostname:
        raise InvalidUrlError("URL にホスト名が含まれていません。")
    return url


def _resolve_addresses(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise FetchError(f"ホスト名を解決できませんでした: {hostname}") from exc
    return [info[4][0] for info in infos]


def _assert_public_host(url: str) -> None:
    """プライベート / ループバック / リンクローカル宛を拒否する (SSRF 対策)。"""
    hostname = urlparse(url).hostname
    if not hostname:
        raise InvalidUrlError("URL にホスト名が含まれていません。")

    for address in _resolve_addresses(hostname):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise InvalidUrlError(
                "内部ネットワーク宛の URL は許可されていません "
                "(HTTP_ALLOW_PRIVATE_HOSTS=true で許可できます)。"
            )


def _sniff_encoding_from_bytes(head: bytes) -> str | None:
    """HTML 冒頭のバイト列から文字コード宣言を拾う。"""
    for pattern in (_META_CHARSET_RE, _XML_DECL_RE):
        match = pattern.search(head)
        if match:
            try:
                name = match.group(1).decode("ascii", errors="ignore").strip().lower()
            except Exception:  # pragma: no cover - decode("ascii") は実質失敗しない
                continue
            if name:
                return name
    return None


def _normalize_encoding(name: str | None) -> str | None:
    """別名を Python の codec 名に寄せる。"""
    if not name:
        return None
    key = name.strip().strip("\"'").lower()
    aliases = {
        "shift-jis": "cp932",
        "shift_jis": "cp932",
        "sjis": "cp932",
        "x-sjis": "cp932",
        "windows-31j": "cp932",
        "ms932": "cp932",
        "euc-jp": "euc_jp",
        "eucjp": "euc_jp",
        "x-euc-jp": "euc_jp",
        "iso-2022-jp": "iso2022_jp",
        "utf8": "utf-8",
        "utf-8": "utf-8",
        "ascii": "utf-8",       # ASCII は UTF-8 の部分集合。広く取っておく。
        "us-ascii": "utf-8",
    }
    return aliases.get(key, key)


def decode_html(content: bytes, header_encoding: str | None) -> tuple[str, str]:
    """バイト列を HTML 文字列にデコードする。

    Returns:
        (デコード済み文字列, 実際に使用した encoding 名)
    """
    candidates: list[str] = []

    # 1. Content-Type ヘッダの charset (requests の既定フォールバックは信用しない)
    if header_encoding and header_encoding.upper() != _REQUESTS_DEFAULT_ENCODING:
        normalized = _normalize_encoding(header_encoding)
        if normalized:
            candidates.append(normalized)

    # 2. HTML 内の宣言 (冒頭 4KB を見れば足りる)
    sniffed = _normalize_encoding(_sniff_encoding_from_bytes(content[:4096]))
    if sniffed:
        candidates.append(sniffed)

    # 3. 統計的推定
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(content[:200_000]).best()
        if best is not None and best.encoding:
            detected = _normalize_encoding(best.encoding)
            if detected:
                candidates.append(detected)
    except Exception:  # pragma: no cover - 推定に失敗しても致命的ではない
        logger.debug("charset detection failed", exc_info=True)

    # 4. 最終フォールバック
    candidates.append("utf-8")

    seen: set[str] = set()
    for encoding in candidates:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            return content.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue

    # ここに来るのは全滅した場合のみ。文字化けしてでも本文抽出は試みる。
    return content.decode("utf-8", errors="replace"), "utf-8(replace)"


class HtmlFetcher:
    """HTTP セッションを再利用する HTML 取得クライアント。

    ``fetch()`` は ``asyncio.to_thread`` から複数スレッドで同時に呼ばれる。
    ``requests.Session`` はスレッドセーフではない (cookie jar などの内部状態を
    共有する) ため、**スレッドごとに 1 つ**持たせる。
    こうすればコネクションの再利用は保ったまま、競合を避けられる。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()

    @property
    def _session(self) -> requests.Session:
        """呼び出し元スレッド専用のセッション (無ければ作る)。"""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.max_redirects = self._settings.http_max_redirects
            session.headers.update(
                {
                    "User-Agent": self._settings.http_user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                    "Cache-Control": "no-cache",
                }
            )
            self._local.session = session
            # 終了時にまとめて閉じられるよう控えておく。
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def close(self) -> None:
        """作成した全スレッド分のセッションを閉じる。"""
        with self._sessions_lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.close()

    def fetch(self, raw_url: str) -> FetchedPage:
        """URL から HTML を取得する。

        Raises:
            InvalidUrlError:    URL 形式・宛先が不正。
            FetchTimeoutError:  タイムアウト。
            FetchError:         その他の取得失敗。
        """
        url = validate_url(raw_url)
        if not self._settings.http_allow_private_hosts:
            _assert_public_host(url)

        started = time.perf_counter()
        try:
            response = self._session.get(
                url,
                timeout=self._settings.http_timeout,
                allow_redirects=True,
                stream=True,
            )
            try:
                response.raise_for_status()
                content = self._read_capped(response)
                header_encoding = self._header_encoding(response)
            finally:
                response.close()
        except RequestsTimeout as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.warning(
                "html fetch timeout %s",
                kv(url=url, timeout_s=self._settings.http_timeout, elapsed_ms=elapsed_ms),
            )
            raise FetchTimeoutError(
                f"HTML の取得がタイムアウトしました ({self._settings.http_timeout:.0f} 秒)。"
            ) from exc
        except TooManyRedirects as exc:
            raise FetchError("リダイレクトが多すぎます。") from exc
        except HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            raise FetchError(f"HTML の取得に失敗しました (HTTP {status})。") from exc
        except RequestsConnectionError as exc:
            raise FetchError("サイトに接続できませんでした。") from exc
        except RequestException as exc:
            raise FetchError(f"HTML の取得に失敗しました: {type(exc).__name__}") from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        self._assert_html(response)

        html, encoding = decode_html(content, header_encoding)
        if not html.strip():
            raise FetchError("取得した HTML が空でした。")

        # 要件: HTML 取得時間をログに出す。
        logger.info(
            "html fetched %s",
            kv(
                url=response.url,
                status=response.status_code,
                bytes=len(content),
                encoding=encoding,
                fetch_ms=elapsed_ms,
            ),
        )
        return FetchedPage(
            url=str(response.url),
            html=html,
            status_code=response.status_code,
            encoding=encoding,
            elapsed_ms=elapsed_ms,
        )

    # ---- 内部ヘルパ ----

    def _read_capped(self, response: requests.Response) -> bytes:
        """サイズ上限を超えたら打ち切って読む。

        Content-Length を信用せず、実バイト数で判定する。上限に達した時点で
        エラーにせず打ち切るのは、巨大ページでも冒頭に本文があることが多く、
        「取得できない」より「途中まででも解析する」ほうが有用なため。
        """
        limit = self._settings.http_max_bytes
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                logger.warning(
                    "html truncated at size limit %s", kv(url=response.url, limit=limit)
                )
                break
        return b"".join(chunks)

    @staticmethod
    def _header_encoding(response: requests.Response) -> str | None:
        content_type = response.headers.get("Content-Type", "")
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part.split("=", 1)[1].strip()
        return None

    @staticmethod
    def _assert_html(response: requests.Response) -> None:
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        # Content-Type を返さないサーバもあるため、空なら通す。
        if content_type and not any(content_type.startswith(t) for t in _HTML_CONTENT_TYPES):
            raise FetchError(f"HTML ではないコンテンツです (Content-Type: {content_type})。")
