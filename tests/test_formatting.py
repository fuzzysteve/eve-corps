from html.parser import HTMLParser

import pytest

from evecorps.formatting import eve_description_to_markdown as convert
from evecorps.formatting import render_markdown as render


# --- Nothing can be included or executed ---------------------------------

class _Tags(HTMLParser):
    """Collect the real tags and attributes in rendered output (escaped text doesn't count)."""

    def __init__(self, html):
        super().__init__()
        self.tags, self.attrs = set(), {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for k, v in attrs:
            self.attrs.setdefault(k, []).append(v or "")


SAFE_TAGS = {"p", "br", "strong", "em", "s", "code", "pre", "blockquote", "hr", "ul", "ol",
             "li", "h3", "h4", "h5", "a", "table", "thead", "tbody", "tr", "th", "td"}

@pytest.mark.parametrize("src", [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<iframe src='https://evil.example'></iframe>",
    "<style>body{display:none}</style>",
    "<object data='x'></object><embed src='x'>",
    "<a href='https://ok.example' onclick='alert(1)'>x</a>",
    "<svg onload=alert(1)>",
    "<!-- <script>alert(1)</script> -->",
])
def test_raw_html_is_escaped_not_rendered(src):
    found = _Tags(str(render(src)))
    assert found.tags <= SAFE_TAGS
    assert set(found.attrs) <= {"href", "rel"}


@pytest.mark.parametrize("src", [
    "![pixel](https://spy.example/p.png)",
    '![pixel](https://spy.example/p.png "t")',
    "![pixel][ref]\n\n[ref]: https://spy.example/p.png",
    "<https://spy.example/p.png>",
])
def test_no_images(src):
    assert "img" not in _Tags(str(render(src))).tags


@pytest.mark.parametrize("src", [
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "[x](vbscript:msgbox)",
    "[x](showinfo:1377//123)",
    "<javascript:alert(1)>",
])
def test_unsafe_link_schemes_dropped(src):
    hrefs = _Tags(str(render(src))).attrs.get("href", [])
    assert all(h.lower().startswith(("http://", "https://", "mailto:")) for h in hrefs)


def test_links_get_rel_and_bare_urls_link():
    html = str(render("Join https://discord.gg/abc or [site](https://corp.example)"))
    assert html.count('rel="nofollow noopener noreferrer ugc"') == 2
    assert 'href="https://discord.gg/abc"' in html
    assert 'href="https://corp.example"' in html


# --- Formatting that should work -----------------------------------------

def test_basic_formatting():
    html = str(render("**b** *i* ~~s~~ `c`\n\n- one\n- two\n\n> q\n\n---"))
    for tag in ("<strong>b</strong>", "<em>i</em>", "<s>s</s>", "<code>c</code>", "<ul>", "<li>one</li>", "<blockquote>", "<hr"):
        assert tag in html, tag


def test_headings_shifted_below_page_title():
    html = str(render("# One\n## Two\n### Three\n#### Four"))
    assert "<h1" not in html and "<h2" not in html
    assert "<h3>One</h3>" in html and "<h4>Two</h4>" in html and html.count("<h5>") == 2


def test_tables():
    html = str(render("| a | b |\n|---|--:|\n| 1 | 2 |"))
    assert "<table>" in html and "<th>a</th>" in html and "<td>1</td>" in html
    assert "style=" not in html  # alignment styles stripped


def test_plain_text_adverts_keep_line_breaks():
    html = str(render("Line one\nLine two\n\nNew para"))
    assert "Line one<br>" in html and "<p>New para</p>" in html


def test_empty():
    assert str(render(None)) == "" and str(render("")) == ""


# --- In-game description conversion ---------------------------------------

def test_font_and_loc_dropped_text_kept():
    raw = '<font size="12" color="#bfffffff">Hello</font><loc> world</loc>'
    assert convert(raw) == "Hello world"


def test_bold_italic_and_breaks():
    assert convert("<b>Bold</b> and <i>it</i><br>next<br><br><br><br>para") == "**Bold** and *it*\nnext\n\npara"


def test_markers_hug_text_and_empty_pairs_dropped():
    assert convert("<b> spaced </b>x<b></b><i> </i>") == "**spaced** x"


def test_unclosed_and_misnested_tags():
    assert convert("<b>open <i>both") == "**open *both***"
    assert convert('<loc><a href="http://y.example/v">Video </loc>?</a>') == "[Video ?](<http://y.example/v>)"


def test_link_kinds():
    raw = ('<a href="https://discord.gg/x">Discord</a> '
           '<a href="showinfo:1377//2114808750">Some Pilot</a> '
           '<a href="joinChannel:-123//None//None">Pub Chat</a> '
           '<a href="fitting:587:2048;1::">Rifter fit</a> '
           '<a href="javascript:alert(1)">bad</a>')
    assert convert(raw) == "[Discord](<https://discord.gg/x>) Some Pilot Pub Chat (in-game channel) Rifter fit bad"


def test_python_repr_descriptions_unwrapped():
    raw = "u'We don\\'t \\xb4 \\u2022 <b>x</b>'"
    assert convert(raw) == "We don't ´ • **x**"


def test_markdown_special_chars_escaped():
    md = convert("5*3 = [15] _x_ <not a tag")
    assert "\\*" in md and "\\[" in md and "\\_" in md
    assert "<em>" not in str(render(md))


def test_entities_decoded():
    assert convert("Diplo &amp; friends &lt;3") == "Diplo & friends \\<3"


def test_converted_output_renders_safely():
    raw = '<font>Hi</font><br><a href="https://ok.example">site</a><br><b>Bold</b>'
    html = str(render(convert(raw)))
    assert "<strong>Bold</strong>" in html and 'href="https://ok.example"' in html


def test_empty_description():
    assert convert(None) == "" and convert("") == ""


# --- Pasted in-game advert --------------------------------------------------

from evecorps.formatting import pasted_advert_to_markdown as paste  # noqa: E402


def test_paste_with_eve_markup_is_converted():
    md, fmt = paste('<font size="12" color="#bfffffff">Hi <b>pilots</b></font><br>'
                    '<a href="showinfo:2//1">Our Corp</a> <a href="https://discord.gg/x">Discord</a>')
    assert fmt == "eve"
    assert md == "Hi **pilots**\nOur Corp [Discord](<https://discord.gg/x>)"


def test_paste_plain_text_kept_as_typed():
    md, fmt = paste("We fly stuff\r\nEvery day   \r\n\r\n\r\n\r\nJoin us")
    assert fmt == "plain"
    assert md == "We fly stuff\nEvery day\n\nJoin us"


def test_paste_python_repr_and_empty():
    assert paste("u'<b>x</b> \\u2022'") == ("**x** •", "eve")
    assert paste("") == ("", "plain")
    assert paste(None) == ("", "plain")


def test_pasted_html_still_rendered_safely():
    md, fmt = paste("<script>alert(1)</script><img src=x onerror=alert(1)>")
    assert fmt == "plain"
    html = str(render(md))
    assert "<script" not in html and "<img" not in html
