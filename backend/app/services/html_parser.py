"""HTML 解析 — 商品名と本文の抽出。

本文抽出は 3 段構えで、先に試した実装が十分な量のテキストを返せなければ
次の実装にフォールバックする。

1. trafilatura   — 本文抽出の精度が最も高い
2. readability   — trafilatura が取りこぼす商品説明ページに強い
3. BeautifulSoup — 最終手段。ノイズタグを落として素朴に text 化する

いずれも広告 / ヘッダ / フッタ / サイドバー / script / style / コメントを除去する。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment

from app.core.errors import ContentExtractionError
from app.core.logging import get_logger, kv
from app.services import preprocess

logger = get_logger(__name__)

# 本文とみなさないタグ。ここに列挙したものは丸ごと落とす。
_NOISE_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "canvas",
    "form",
    "input",
    "button",
    "select",
    "textarea",
    "nav",
    "header",
    "footer",
    "aside",
    "menu",
    "dialog",
)

# class / id にこれらを含む要素は広告・ナビ等とみなして落とす (BeautifulSoup 経路)。
_NOISE_HINTS = (
    "ad-",
    "-ad",
    "ads",
    "advert",
    "banner",
    "breadcrumb",
    "footer",
    "header",
    "global-nav",
    "gnav",
    "sidebar",
    "side-bar",
    "sidemenu",
    "widget",
    "popup",
    "modal",
    "cookie",
    "social",
    "share",
    "sns",
    "pagination",
    "pager",
    "recommend",
    "ranking",
    "related",
    "review-list",
    "comment",
    "copyright",
    "navigation",
)

# 本文として「十分」とみなす候補文の件数。これだけ取れたら後段の実装は試さない。
#
# 生の文字数で判断すると、しきい値の境目にあるページで問題が起きる。
# 例えば trafilatura が 310 文字を返しているページから 1 文減っただけで
# 276 文字になり、しきい値 300 をまたいで readability に切り替わってしまう。
# 抽出器が変われば候補文は丸ごと入れ替わるので、些細な HTML の変化で
# 結果が別物になっていた。
#
# 実際に欲しいのは「商品説明として使える文が取れたか」なので、そちらで判断する。
# 候補文の件数は文字数よりも境界からの余裕が大きく (13 件 vs しきい値 3 件)、
# ページが多少変わっても抽出器が入れ替わらない。
_ENOUGH_CANDIDATES = 3


@dataclass
class ParsedPage:
    """HTML から取り出した構造化データ。"""

    title: str = ""
    title_source: str = ""       # "og:title" / "title" / "h1" のいずれか
    body: str = ""               # 本文プレーンテキスト
    extractor: str = ""          # 本文抽出に使った実装名
    descriptions: list[str] = field(default_factory=list)  # meta description 等


def _soup(html: str) -> BeautifulSoup:
    """lxml パーサで BeautifulSoup を作る (壊れた HTML にも強い)。"""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml が使えない環境向けの保険
        return BeautifulSoup(html, "html.parser")


def _clean_text(value: str | None) -> str:
    """タイトル等の短いテキストの空白を整える。

    文字は書き換えない。連続する空白類を半角スペース 1 つに畳むだけ。
    """
    if not value:
        return ""
    return " ".join(value.split()).strip()


# --------------------------------------------------------------------------
# 商品名
# --------------------------------------------------------------------------


# schema.org で商品を表す @type。URL 形式 (http://schema.org/Product) でも書けるため、
# 最後のセグメントだけを見て判定する。
_PRODUCT_TYPES = frozenset(
    {"product", "individualproduct", "productmodel", "productgroup", "vehicle", "book"}
)

# JSON-LD の入れ子をたどる深さの上限 (壊れた構造で無限に潜らないための保険)。
_JSONLD_MAX_DEPTH = 8


def _jsonld_type_is_product(value: object) -> bool:
    """``@type`` が商品を表すか。文字列でも配列でも受ける。"""
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, str):
            continue
        name = item.rsplit("/", 1)[-1].rsplit("#", 1)[-1].strip().lower()
        if name in _PRODUCT_TYPES:
            return True
    return False


def _jsonld_name_text(value: object) -> str:
    """``name`` の値を文字列にする (``{"@value": "..."}`` 形式にも対応)。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _jsonld_name_text(value.get("@value"))
    if isinstance(value, list):
        for item in value:
            text = _jsonld_name_text(item)
            if text:
                return text
    return ""


def _find_product_name(node: object, depth: int = 0) -> str:
    """JSON-LD のノードを再帰的にたどり、商品の ``name`` を探す。

    ``@type`` が商品のときだけ ``name`` を採用するので、Brand や Offer の
    ``name`` を拾ってしまうことはない。
    """
    if depth > _JSONLD_MAX_DEPTH:
        return ""

    if isinstance(node, list):
        for item in node:
            found = _find_product_name(item, depth + 1)
            if found:
                return found
        return ""

    if not isinstance(node, dict):
        return ""

    if _jsonld_type_is_product(node.get("@type")):
        name = _clean_text(_jsonld_name_text(node.get("name")))
        if name:
            return name

    # @graph などの入れ子を掘る。
    for value in node.values():
        if isinstance(value, (dict, list)):
            found = _find_product_name(value, depth + 1)
            if found:
                return found
    return ""


def extract_jsonld_product_name(soup: BeautifulSoup) -> str:
    """構造化データ (JSON-LD) から商品名を取り出す。

    EC サイトの ``og:title`` や ``<title>`` は検索対策で
    「商品名 + キャッチ + サイト名」になっていることが多いのに対し、
    schema.org の ``Product.name`` には**商品名そのもの**が入る。
    Google の商品リッチリザルト対応で広く実装されているため、
    取れる場合はこれが最も確実。

    壊れた JSON-LD を置いているサイトも珍しくないので、
    パースに失敗したブロックは黙って読み飛ばす。

    Returns:
        商品名。見つからなければ空文字。
    """
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.debug("failed to parse JSON-LD block", exc_info=True)
            continue
        name = _find_product_name(data)
        if name:
            return name
    return ""


def extract_title(soup: BeautifulSoup) -> tuple[str, str]:
    """商品名を取り出す。

    優先順位:
        0. JSON-LD の schema.org ``Product.name`` (構造化データ)
        1. <meta property="og:title">
        2. <title>
        3. <h1>

    0 を先頭に置いているのは、``og:title`` / ``<title>`` に
    「〜のギフト通販 - 贈り物、ギフトの専門店 ○○」のような検索対策文字列を
    入れているサイトが多く、そこには商品名そのものが入っていないため。
    構造化データが無いサイトでは 1〜3 の従来どおりの順序で決まる。

    Returns:
        (商品名, 採用元) — 見つからなければ ("", "")
    """
    jsonld_name = extract_jsonld_product_name(soup)
    if jsonld_name:
        return jsonld_name, "json-ld"

    og = soup.find("meta", attrs={"property": "og:title"})
    if og is not None:
        value = _clean_text(og.get("content"))
        if value:
            return value, "og:title"

    # property ではなく name="og:title" と書くサイトも実在するので拾っておく。
    og_name = soup.find("meta", attrs={"name": "og:title"})
    if og_name is not None:
        value = _clean_text(og_name.get("content"))
        if value:
            return value, "og:title"

    if soup.title is not None:
        value = _clean_text(soup.title.get_text())
        if value:
            return value, "title"

    h1 = soup.find("h1")
    if h1 is not None:
        value = _clean_text(h1.get_text(separator=" "))
        if value:
            return value, "h1"

    return "", ""


def extract_descriptions(soup: BeautifulSoup) -> list[str]:
    """meta description / og:description を候補文として集める。

    これらも「HTML 内に存在する文章」なので、キャッチフレーズの正当な候補になる。
    商品ページでは本文より要点が凝縮されていることが多い。
    """
    found: list[str] = []
    selectors = (
        {"property": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
    )
    for attrs in selectors:
        for tag in soup.find_all("meta", attrs=attrs):
            value = _clean_text(tag.get("content"))
            if value and value not in found:
                found.append(value)
    return found


# --------------------------------------------------------------------------
# 本文
# --------------------------------------------------------------------------


def _extract_with_trafilatura(html: str, url: str | None) -> str:
    try:
        import trafilatura
    except ImportError:  # pragma: no cover
        return ""
    try:
        result = trafilatura.extract(
            html,
            url=url,
            output_format="txt",
            include_comments=False,
            include_tables=True,
            include_images=False,
            include_links=False,
            favor_recall=True,       # 商品説明は短文が多く、取りこぼしを避けたい
            no_fallback=False,
            # deduplicate は**プロセス全体で共有される LRU キャッシュ**を使い、
            # 「以前の呼び出しでも見た文」を落としてしまう。そのため同じ HTML でも
            # 2 回目以降は結果が変わり (例: 622字 → 0字 → 370字)、
            # 抽出器の切り替わりを誘発して候補文が丸ごと入れ替わっていた。
            # ページ内の重複は preprocess.dedupe() で別途落としているので不要。
            deduplicate=False,
        )
    except Exception:
        logger.warning("trafilatura extraction failed", exc_info=True)
        return ""
    return result or ""


def _extract_with_readability(html: str) -> str:
    try:
        from readability import Document
    except ImportError:  # pragma: no cover
        return ""
    try:
        summary_html = Document(html).summary(html_partial=True)
    except Exception:
        logger.warning("readability extraction failed", exc_info=True)
        return ""
    if not summary_html:
        return ""
    return _soup_to_text(_soup(summary_html))


def _extract_with_soup(html: str) -> str:
    soup = _soup(html)
    _strip_noise(soup)
    return _soup_to_text(soup)


def _is_detached(tag) -> bool:
    """既に decompose 済み / ツリーから外れたタグかどうか。

    BeautifulSoup は decompose した Tag の ``attrs`` を None にするため、
    そのまま属性アクセスすると AttributeError になる。
    """
    return bool(getattr(tag, "decomposed", False)) or getattr(tag, "attrs", None) is None


def _strip_noise(soup: BeautifulSoup) -> None:
    """広告・ナビ・スクリプト・コメント等を DOM から取り除く。"""
    for tag_name in _NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            if not _is_detached(tag):
                tag.decompose()

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for tag in soup.find_all(attrs={"role": ["navigation", "banner", "contentinfo", "search"]}):
        if not _is_detached(tag):
            tag.decompose()

    # find_all() の結果は先に確定するため、親を decompose すると
    # リストに残った子孫は「切り離し済み」になる。触る前に必ず確認する。
    for tag in soup.find_all(True):
        if _is_detached(tag):
            continue
        class_names = tag.get("class") or []
        if isinstance(class_names, str):  # class が 1 つだけのとき文字列で返る実装への保険
            class_names = [class_names]
        identifier = " ".join([" ".join(class_names), tag.get("id") or ""]).strip().lower()
        if not identifier:
            continue
        if any(hint in identifier for hint in _NOISE_HINTS):
            tag.decompose()


def _soup_to_text(soup: BeautifulSoup) -> str:
    """DOM をプレーンテキストにする (HTML タグ除去)。

    ブロック要素の境目で改行を入れ、後段の文分割が文をまたがないようにする。
    """
    for br in soup.find_all("br"):
        br.replace_with("\n")

    block_tags = (
        "p", "div", "li", "tr", "td", "th", "section", "article",
        "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "blockquote",
    )
    for tag in soup.find_all(block_tags):
        tag.append("\n")

    return soup.get_text(separator=" ")


def _usable_candidate_count(text: str) -> int:
    """その本文から、商品説明として使える文が何件取れるかを数える。

    ここでの件数は**どの抽出器を採用するかを決めるための目安**であり、
    実際に LLM へ渡す候補文はパイプライン側が設定値
    (MIN_SENTENCE_CHARS など) を使って作り直す。
    """
    if not text:
        return 0
    return len(preprocess.build_candidates(body=preprocess.clean_text(text)))


def extract_body(html: str, url: str | None = None) -> tuple[str, str]:
    """本文を抽出する。

    精度の高い順に試し、**商品説明として使える文が十分に取れた最初の実装**を
    採用する。生の文字数ではなく候補文の件数で判断するのは、文字数だと
    しきい値の境目でわずかな HTML の差により抽出器が入れ替わってしまうため
    (``_ENOUGH_CANDIDATES`` のコメント参照)。

    Returns:
        (本文テキスト, 使用した実装名)

    Raises:
        ContentExtractionError: どの実装でも本文が得られなかった場合。
    """
    # (実装名, 本文, 候補文の件数)
    attempts: list[tuple[str, str, int]] = []

    for name, extract in (
        ("trafilatura", lambda: _extract_with_trafilatura(html, url)),
        ("readability", lambda: _extract_with_readability(html)),
        ("beautifulsoup", lambda: _extract_with_soup(html)),
    ):
        text = (extract() or "").strip()
        count = _usable_candidate_count(text)
        attempts.append((name, text, count))
        if count >= _ENOUGH_CANDIDATES:
            logger.debug(
                "body extracted %s", kv(extractor=name, chars=len(text), candidates=count)
            )
            return text, name

    # どれも十分な候補文を出せなかった場合は、最も多く取れたものを採用する。
    # 件数が同じなら本文が長いほう、それも同じなら優先順位が先のものが残る
    # (max は最初に現れた最大値を返すため)。
    best_name, best_text, best_count = max(
        attempts, key=lambda item: (item[2], len(item[1]))
    )
    if not best_text:
        raise ContentExtractionError("ページから本文を抽出できませんでした。")

    logger.info(
        "no extractor produced enough candidates; using the best available %s",
        kv(extractor=best_name, chars=len(best_text), candidates=best_count),
    )
    return best_text, best_name


def parse_page(html: str, url: str | None = None) -> ParsedPage:
    """HTML を解析して商品名・本文・description をまとめて返す。"""
    soup = _soup(html)
    title, title_source = extract_title(soup)
    descriptions = extract_descriptions(soup)

    try:
        body, extractor = extract_body(html, url)
    except ContentExtractionError:
        # 本文が取れなくても description があれば処理を続けられる。
        if not descriptions:
            raise
        body, extractor = "", "none"

    return ParsedPage(
        title=title,
        title_source=title_source,
        body=body,
        extractor=extractor,
        descriptions=descriptions,
    )
