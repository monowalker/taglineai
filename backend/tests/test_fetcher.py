"""HTML 取得まわり (URL 検証 / 文字コード判定) のテスト。

ネットワークには出ない範囲を検証する。
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidUrlError
from app.services.fetcher import decode_html, validate_url


class TestValidateUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/item/1",
            "http://example.com",
            "https://example.co.jp/shop/item?id=1&color=red",
        ],
    )
    def test_accepts_http_and_https(self, url):
        assert validate_url(url) == url

    def test_trims_surrounding_whitespace(self):
        assert validate_url("  https://example.com/item  ") == "https://example.com/item"

    @pytest.mark.parametrize(
        "url",
        ["", "   ", "example.com", "ftp://example.com", "javascript:alert(1)", "file:///etc/passwd"],
    )
    def test_rejects_invalid_urls(self, url):
        with pytest.raises(InvalidUrlError):
            validate_url(url)

    def test_rejects_too_long_url(self):
        with pytest.raises(InvalidUrlError):
            validate_url("https://example.com/" + "a" * 2100)

    def test_rejects_url_without_host(self):
        with pytest.raises(InvalidUrlError):
            validate_url("http:///path")


class TestDecodeHtml:
    def test_uses_header_charset(self):
        content = "商品説明です".encode("cp932")
        text, encoding = decode_html(content, "Shift_JIS")
        assert text == "商品説明です"
        assert encoding == "cp932"

    def test_uses_meta_charset_when_header_missing(self):
        html = '<html><head><meta charset="euc-jp"></head><body>商品説明です</body></html>'
        text, encoding = decode_html(html.encode("euc_jp"), None)
        assert "商品説明です" in text
        assert encoding == "euc_jp"

    def test_ignores_requests_default_iso_fallback(self):
        """charset 未指定時の ISO-8859-1 は信用せず、実体から判定する。"""
        html = '<html><head><meta charset="utf-8"></head><body>商品説明です</body></html>'
        text, encoding = decode_html(html.encode("utf-8"), "ISO-8859-1")
        assert "商品説明です" in text
        assert encoding == "utf-8"

    def test_falls_back_to_utf8(self):
        text, encoding = decode_html("商品説明です".encode("utf-8"), None)
        assert text == "商品説明です"
        assert encoding in ("utf-8", "utf_8")

    def test_never_raises_on_broken_bytes(self):
        text, _ = decode_html(b"\xff\xfe\x00broken", None)
        assert isinstance(text, str)
