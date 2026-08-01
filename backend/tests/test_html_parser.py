"""HTML 解析 (商品名 / 本文抽出) のテスト。"""

from __future__ import annotations

import pytest

from app.core.errors import ContentExtractionError
from app.services.html_parser import (
    _soup,
    extract_body,
    extract_descriptions,
    extract_title,
    parse_page,
)
from app.services.preprocess import build_candidates
from tests.conftest import NO_CONTENT_HTML, SAMPLE_HTML


class TestExtractTitle:
    """商品名の優先順位: og:title > title > h1"""

    def test_og_title_is_preferred(self):
        title, source = extract_title(_soup(SAMPLE_HTML))
        assert title == "感謝をコメて 魚沼産コシヒカリ"
        assert source == "og:title"

    def test_falls_back_to_title_tag(self):
        html = "<html><head><title>タイトルタグの商品名</title></head><body><h1>H1の商品名</h1></body></html>"
        title, source = extract_title(_soup(html))
        assert title == "タイトルタグの商品名"
        assert source == "title"

    def test_falls_back_to_h1(self):
        html = "<html><head></head><body><h1>H1の商品名</h1></body></html>"
        title, source = extract_title(_soup(html))
        assert title == "H1の商品名"
        assert source == "h1"

    def test_returns_empty_when_nothing_found(self):
        title, source = extract_title(_soup("<html><body><p>本文だけ</p></body></html>"))
        assert title == ""
        assert source == ""

    def test_whitespace_is_normalized_without_rewriting(self):
        html = '<html><head><meta property="og:title" content="  魚沼産   コシヒカリ  "></head><body></body></html>'
        title, _ = extract_title(_soup(html))
        assert title == "魚沼産 コシヒカリ"


class TestJsonLdProductName:
    """構造化データ (schema.org Product) からの商品名取得。"""

    @staticmethod
    def _page(jsonld: str) -> str:
        return (
            "<html><head>"
            '<meta property="og:title" content="商品名のギフト通販 - 贈り物の専門店 サンプル">'
            "<title>商品名のギフト通販 - 贈り物の専門店 サンプル</title>"
            f'<script type="application/ld+json">{jsonld}</script>'
            "</head><body><h1></h1><h1>H1の商品名</h1></body></html>"
        )

    def test_product_name_wins_over_og_title(self):
        html = self._page('{"@type":"Product","name":"NISHIKIYA KITCHEN / カレー6個ギフト"}')
        title, source = extract_title(_soup(html))
        assert title == "NISHIKIYA KITCHEN / カレー6個ギフト"
        assert source == "json-ld"

    def test_finds_product_inside_graph(self):
        html = self._page(
            '{"@context":"https://schema.org","@graph":['
            '{"@type":"BreadcrumbList","name":"パンくず"},'
            '{"@type":"Product","name":"グラフ内の商品名"}]}'
        )
        assert extract_title(_soup(html))[0] == "グラフ内の商品名"

    def test_finds_product_in_a_top_level_array(self):
        html = self._page(
            '[{"@type":"WebPage","name":"ページ名"},{"@type":"Product","name":"配列内の商品名"}]'
        )
        assert extract_title(_soup(html))[0] == "配列内の商品名"

    def test_accepts_schema_org_url_type(self):
        html = self._page('{"@type":"http://schema.org/Product","name":"URL型の商品名"}')
        assert extract_title(_soup(html))[0] == "URL型の商品名"

    def test_accepts_type_array(self):
        html = self._page('{"@type":["Thing","Product"],"name":"配列型の商品名"}')
        assert extract_title(_soup(html))[0] == "配列型の商品名"

    def test_ignores_names_of_non_product_types(self):
        """Brand や Offer の name を拾わない。"""
        html = self._page(
            '{"@type":"Product","brand":{"@type":"Brand","name":"ブランド名"},'
            '"offers":{"@type":"Offer","name":"オファー名"},"name":"本当の商品名"}'
        )
        assert extract_title(_soup(html))[0] == "本当の商品名"

    def test_falls_back_when_no_product_node(self):
        html = self._page('{"@type":"BreadcrumbList","name":"パンくず"}')
        title, source = extract_title(_soup(html))
        assert source == "og:title"
        assert title == "商品名のギフト通販 - 贈り物の専門店 サンプル"

    def test_broken_json_is_ignored(self):
        """壊れた JSON-LD を置くサイトでも落ちず、従来の優先順位に戻る。"""
        title, source = extract_title(_soup(self._page("{ this is not json")))
        assert source == "og:title"

    def test_empty_product_name_falls_back(self):
        html = self._page('{"@type":"Product","name":"   "}')
        assert extract_title(_soup(html))[1] == "og:title"

    def test_whitespace_is_normalized(self):
        html = self._page('{"@type":"Product","name":"  商品名　に　空白  "}')
        assert extract_title(_soup(html))[0] == "商品名 に 空白"

    def test_pages_without_jsonld_keep_the_original_priority(self):
        """構造化データが無いサイトの挙動は変わらない。"""
        title, source = extract_title(_soup(SAMPLE_HTML))
        assert source == "og:title"
        assert title == "感謝をコメて 魚沼産コシヒカリ"


class TestExtractDescriptions:
    def test_collects_meta_description(self):
        descriptions = extract_descriptions(_soup(SAMPLE_HTML))
        assert "新潟県魚沼産コシヒカリを使用。米の宝石と呼ばれるブレンド米です。" in descriptions


class TestDeterminism:
    """同じ HTML なら何度呼んでも同じ結果になること。

    trafilatura の ``deduplicate=True`` はプロセス共有の LRU キャッシュを使い、
    「前回の呼び出しでも見た文」を落としてしまう。そのため同じページを 2 回
    抽出すると本文が短くなり、抽出器の切り替わりを誘発して候補文が
    丸ごと入れ替わる問題があった。
    """

    # trafilatura が本文として認識できる程度の分量を持たせる
    RICH_HTML = (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        "<title>魚沼産コシヒカリ</title></head><body><main><article>"
        "<h1>感謝をコメて 魚沼産コシヒカリ</h1>"
        "<p>新潟県魚沼産コシヒカリを使用しています。米どころとして知られる魚沼地域は、"
        "昼夜の寒暖差が大きく、雪解け水が豊富なことで知られています。</p>"
        "<p>米の宝石と呼ばれる、香りと甘みのバランスに優れたお米です。"
        "炊き上がりの艶と立ちのぼる香りは、それだけでごちそうになります。</p>"
        "<p>粒立ちがよく、冷めてももちもちとした食感が長く続きます。"
        "お弁当やおにぎりにしても硬くなりにくく、一日を通しておいしくいただけます。</p>"
        "<p>契約農家が丹精込めて育てたお米を、ご注文後に精米してお届けします。"
        "精米したてのお米は風味が格段に違います。</p>"
        "<p>贈り物にも喜ばれる化粧箱入りでご用意しました。"
        "内祝いや御中元など、さまざまな場面でお使いいただけます。</p>"
        "</article></main></body></html>"
    )

    def test_extract_body_is_stable_across_calls(self):
        results = [extract_body(self.RICH_HTML) for _ in range(5)]
        assert len({r for r in results}) == 1, f"呼び出しごとに結果が変わる: {results}"

    def test_parse_page_is_stable_across_calls(self):
        pages = [parse_page(self.RICH_HTML) for _ in range(5)]
        assert len({(p.title, p.extractor, p.body) for p in pages}) == 1

    def test_candidates_are_stable_across_calls(self):
        """利用者から見える最終的な候補文が毎回同じであること。"""
        sets = []
        for _ in range(5):
            parsed = parse_page(self.RICH_HTML)
            sets.append(
                tuple(build_candidates(body=parsed.body, descriptions=parsed.descriptions))
            )
        assert len(set(sets)) == 1, f"候補文が毎回変わる: {[len(s) for s in sets]}"
        assert sets[0], "候補文が空"


class TestParsePage:
    def test_extracts_body_and_drops_noise(self):
        parsed = parse_page(SAMPLE_HTML, url="https://example.com/item/1")

        assert parsed.title == "感謝をコメて 魚沼産コシヒカリ"
        assert parsed.title_source == "og:title"
        assert parsed.extractor in ("trafilatura", "readability", "beautifulsoup")

        # 本文は残っている
        assert "米の宝石と呼ばれるブレンド米です。" in parsed.body
        assert "粒立ちがよく" in parsed.body

        # script / style / コメントは除去されている
        assert "var tracking" not in parsed.body
        assert "display: none" not in parsed.body
        assert "ここはコメントなので" not in parsed.body

    def test_raises_when_page_has_no_text(self):
        with pytest.raises(ContentExtractionError):
            parse_page("<html><head></head><body><div></div></body></html>")

    def test_page_without_product_text_yields_no_candidates(self):
        """本文らしきものが無いページは、前処理後に候補文が 0 件になる。"""
        parsed = parse_page(NO_CONTENT_HTML, url="https://example.com/404")
        candidates = build_candidates(
            body=parsed.body, descriptions=parsed.descriptions
        )
        assert candidates == []
