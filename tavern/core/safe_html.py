"""A strict HTML cleaner for what screen rules produce (stage 11, ADR 0017).

A rule that formats a status block may write HTML. That text comes from a card, a preset or a file
somebody sent, so it is never put into the page as HTML. `sanitize()` parses it and builds a TREE of
allowed elements only; the page (web/components/Regex.js) builds the elements from the tree, so there
is no `innerHTML` anywhere. Only these things survive:

- a short list of text and table tags (below); tags that are not on it are shown as plain text;
- scripts, styles, frames, objects, forms, svg, math, templates, media: dropped WITH their content;
- attributes: `title`, `style` (only listed properties, no `url()`, no negative sizes, no position),
  `href` on links (http, https, mailto), `src` on pictures (only our own `/assets/…` library),
  numbers on tables, `open` on details, values on progress / meter. No event attributes, no `class`/`id`.

Node: a string (text) or {"t": tag, "a": {attr: value}, "s": {css property: value}, "c": [nodes]}.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

MAX_NODES = 4000
MAX_DEPTH = 30
MAX_ATTR = 600

BLOCK = frozenset("div p h1 h2 h3 h4 h5 h6 ul ol li table thead tbody tfoot tr td th caption blockquote pre "
                  "details summary hr dl dt dd center".split())
TIGHT = frozenset("table thead tbody tfoot tr ul ol dl".split())  # only elements inside: no loose text
INLINE = frozenset("span b strong i em u s del ins small sub sup code kbd mark abbr a br img progress meter "
                   "cite q time var samp".split())
ALLOWED = BLOCK | INLINE
VOID = frozenset({"br", "hr", "img"})
DROP_WITH_CONTENT = frozenset("script style iframe frame frameset object embed applet noscript template title "
                              "head svg math audio video canvas form select textarea option button datalist "
                              "noembed noframes xmp plaintext".split())
DROP_ALONE = frozenset({"link", "meta", "base", "input", "source", "track", "param", "area", "col", "colgroup",
                        "html", "body"})

_ATTRS = {
    "a": {"href"}, "img": {"src", "alt", "width", "height"}, "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"}, "details": {"open"}, "progress": {"value", "max"},
    "meter": {"value", "min", "max", "low", "high", "optimum"}, "time": set(), "abbr": set(),
}
_COMMON = {"title", "style"}
_NUMBER = re.compile(r"-?\d{1,6}(\.\d{1,4})?\Z")

_CSS = frozenset("""
color background background-color background-image font-size font-weight font-style font-family text-align
text-decoration text-transform letter-spacing line-height white-space word-break overflow-wrap padding
padding-top padding-right padding-bottom padding-left margin margin-top margin-right margin-bottom margin-left
border border-top border-right border-bottom border-left border-color border-style border-width border-radius
border-collapse border-spacing table-layout display width height min-width min-height max-width max-height
flex flex-direction flex-wrap flex-grow flex-shrink flex-basis gap row-gap column-gap justify-content
align-items align-self grid-template-columns grid-template-rows grid-column grid-row overflow opacity
vertical-align box-shadow text-shadow list-style list-style-type text-indent
""".split())
_DISPLAY = frozenset("block inline inline-block flex inline-flex grid inline-grid none table table-row "
                     "table-cell list-item".split())
_CSS_VALUE = re.compile(r"[\w\s#%.,()+*/'\"!:-]*\Z")
_CSS_DANGER = re.compile(r"url\s*\(|expression|javascript|behavio|binding|image-set|image\(|@|\\|<|>|src\s*\(|"
                         r"attr\s*\(|env\s*\(|element\s*\(|cross-fade", re.I)
_CSS_NEGATIVE = re.compile(r"(^|[\s(,])-\s*\.?\d")
_CONTROL = re.compile(r"[\x00-\x20\x7f-\x9f​-‏  ﻿]+")


class TooBig(Exception):
    pass


def clean_style(style: str) -> dict[str, str]:
    """`color: red; padding: 4px` -> {"color": "red", "padding": "4px"}, only what is safe."""
    out: dict[str, str] = {}
    for declaration in style.split(";"):
        name, sep, value = declaration.partition(":")
        name, value = name.strip().lower(), value.strip()
        value = re.sub(r"\s*!important\s*$", "", value, flags=re.I)
        if not sep or name not in _CSS or not value or len(value) > 200:
            continue
        if _CSS_DANGER.search(value) or not _CSS_VALUE.match(value) or _CSS_NEGATIVE.search(value):
            continue
        if name == "display" and value.lower() not in _DISPLAY:
            continue
        if name == "background-image" and "gradient" not in value.lower():
            continue  # a picture from an address is a remote resource; a gradient is not
        out[name] = value
    return out


def _link(value: str) -> str | None:
    probe = _CONTROL.sub("", value).lower()
    if probe.startswith(("http://", "https://", "mailto:")):
        return value.strip()[:MAX_ATTR]
    return None


def _picture(value: str) -> str | None:
    probe = value.strip()
    if probe.startswith("/assets/") and ".." not in probe and "\\" not in probe and len(probe) <= MAX_ATTR \
            and not _CONTROL.search(probe):
        return probe
    return None


def _attrs(tag: str, raw: list[tuple[str, str | None]]) -> tuple[dict[str, str], dict[str, str]]:
    allowed = _ATTRS.get(tag, set()) | _COMMON
    attrs: dict[str, str] = {}
    style: dict[str, str] = {}
    for name, value in raw[:40]:
        name, value = name.lower(), value or ""
        if name not in allowed or name.startswith("on"):
            continue
        if name == "style":
            style = clean_style(value)
        elif name == "href":
            link = _link(value)
            if link:
                attrs["href"] = link
        elif name == "src":
            picture = _picture(value)
            if picture:
                attrs["src"] = picture
        elif name in ("colspan", "rowspan", "width", "height", "value", "max", "min", "low", "high", "optimum"):
            if _NUMBER.match(value.strip()):
                number = float(value)
                if name in ("colspan", "rowspan") and not 1 <= number <= 100:
                    continue
                if name in ("width", "height") and not 1 <= number <= 2000:
                    continue
                attrs[name] = value.strip()
        elif name == "open":
            attrs["open"] = "open"
        else:  # title, alt
            attrs[name] = value[:MAX_ATTR]
    return attrs, style


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: list[Any] = []
        self.stack: list[dict[str, Any]] = []
        self.skip: str | None = None
        self.skip_depth = 0
        self.count = 0

    def _children(self) -> list[Any]:
        return self.stack[-1]["c"] if self.stack else self.root

    def _text(self, text: str) -> None:
        if not text:
            return
        children = self._children()
        if children and isinstance(children[-1], str):
            children[-1] += text
        else:
            children.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag == self.skip:
                self.skip_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            if tag not in VOID:
                self.skip, self.skip_depth = tag, 1
            return
        if tag in DROP_ALONE:
            return
        if tag not in ALLOWED or len(self.stack) >= MAX_DEPTH:
            self._text(self.get_starttag_text() or "")
            return
        self.count += 1
        if self.count > MAX_NODES:
            raise TooBig
        a, s = _attrs(tag, attrs)
        node: dict[str, Any] = {"t": tag, "c": []}
        if a:
            node["a"] = a
        if s:
            node["s"] = s
        self._children().append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID and not self.skip:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip:
                self.skip_depth -= 1
                if self.skip_depth == 0:
                    self.skip = None
            return
        if tag in DROP_ALONE or tag in DROP_WITH_CONTENT or tag in VOID:
            return
        if tag not in ALLOWED:
            self._text(f"</{tag}>")
            return
        for depth in range(len(self.stack) - 1, -1, -1):
            if self.stack[depth]["t"] == tag:
                del self.stack[depth:]
                return
        # a closing tag with no opening one: nothing to close

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self._text(data)


def _tidy(children: list[Any], parent_is_block: bool) -> list[Any]:
    """Drop the line breaks between block tags that only come from how the HTML was laid out in the source."""
    for node in children:
        if isinstance(node, dict):
            node["c"] = _tidy(node["c"], node["t"] in BLOCK)
    out: list[Any] = []
    for i, node in enumerate(children):
        if isinstance(node, str):
            before = children[i - 1] if i else None
            after = children[i + 1] if i + 1 < len(children) else None
            starts = (before is None and parent_is_block) or (isinstance(before, dict) and before["t"] in BLOCK)
            ends = (after is None and parent_is_block) or (isinstance(after, dict) and after["t"] in BLOCK)
            if starts:
                node = re.sub(r"\A\s*\n\s*", "", node)
            if ends:
                node = re.sub(r"\s*\n\s*\Z", "", node)
            if not node.strip() and "\n" in node and (starts or ends):
                continue
            if not node:
                continue
        out.append(node)
    return out


def _drop_loose_text(children: list[Any], tight: bool) -> list[Any]:
    out = []
    for node in children:
        if isinstance(node, dict):
            node["c"] = _drop_loose_text(node["c"], node["t"] in TIGHT)
            out.append(node)
        elif not (tight and not node.strip()):
            out.append(node)
    return out


def sanitize(html: str, *, stream: bool = False) -> list[Any]:
    """HTML text -> a tree of allowed nodes. With `stream` a tag still being written at the end is left out."""
    if stream:
        partial = re.search(r"<[A-Za-z/!][^<>]*\Z", html)
        if partial:
            html = html[:partial.start()]
    builder = _Builder()
    try:
        builder.feed(html)
        builder.close()
    except TooBig:
        return [html[:20000]]
    except Exception:  # noqa: BLE001 - a parser failure must never reach the page as markup
        return [html[:20000]]
    return _drop_loose_text(_tidy(builder.root, True), False)


def text_of(nodes: list[Any]) -> str:
    """The visible words of a tree (for searching and tests)."""
    return "".join(n if isinstance(n, str) else text_of(n["c"]) for n in nodes)
