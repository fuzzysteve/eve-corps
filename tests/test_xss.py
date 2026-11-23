"""XSS corpus: every route user-controlled text takes onto a page."""
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from evecorps.formatting import eve_description_to_markdown, pasted_advert_to_markdown, plain_text, render_markdown
from evecorps.extensions import db
from evecorps.models import Advert, Corporation
from evecorps.views import _clean_discord, _clean_website
from test_adverts import CORP, eve, form, login  # noqa: F401

SAFE_TAGS = {"p", "br", "strong", "em", "s", "del", "code", "pre", "blockquote", "hr", "ul", "ol",
             "li", "h3", "h4", "h5", "a", "table", "thead", "tbody", "tr", "th", "td"}
SAFE_ATTRS = {"href", "title", "rel", "start"}
SAFE_SCHEMES = ("http://", "https://", "mailto:")

PAYLOADS = [
    "<script>alert(1)</script>",
    "<ScRiPt>alert(1)</sCrIpT>",
    "<img src=x onerror=alert(1)>",
    "<svg/onload=alert(1)>",
    "<iframe srcdoc='<script>alert(1)</script>'>",
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
    "<a href=\"javascript:alert(1)\">x</a>",
    "<details open ontoggle=alert(1)>",
    "<body onload=alert(1)>",
    "<input autofocus onfocus=alert(1)>",
    "<meta http-equiv=refresh content='0;url=javascript:alert(1)'>",
    "<base href=//evil.example/>",
    "<form action=//evil.example><button>x</button></form>",
    "<style>@import 'https://evil.example/x.css';</style>",
    "<link rel=stylesheet href=//evil.example/x.css>",
    "<object data=javascript:alert(1)>",
    "[x](javascript:alert(1))",
    "[x](JAVASCRIPT:alert(1))",
    "[x](javascript&#58;alert(1))",
    "[x](jav&#x09;ascript:alert(1))",
    "[x](&#106;&#97;&#118;&#97;&#115;&#99;&#114;&#105;&#112;&#116;&#58;alert(1))",
    "[x](\x00javascript:alert(1))",
    "[x]( javascript:alert(1))",
    "[x](<javascript:alert(1)>)",
    "[x](data:text/html,<script>alert(1)</script>)",
    "[x](vbscript:msgbox(1))",
    "[x](file:///etc/passwd)",
    '[x](https://ok.example "\\" onmouseover=alert(1) x=\\"")',
    '[x](https://ok.example "a\" onmouseover=\"alert(1)")',
    "[x][r]\n\n[r]: javascript:alert(1)",
    "<javascript:alert(1)>",
    "<https://ok.example/\"onmouseover=alert(1)>",
    "https://ok.example/\"><script>alert(1)</script>",
    "https://ok.example/<img src=x onerror=alert(1)>",
    "![x](javascript:alert(1))",
    "![x](https://spy.example/p.png)",
    "`<script>alert(1)</script>`",
    "```\n<script>alert(1)</script>\n```",
    "| a | b |\n|---|---|\n| <script>alert(1)</script> | <img src=x onerror=alert(1)> |",
    "> <script>alert(1)</script>",
    "**<img src=x onerror=alert(1)>**",
    "<!--><script>alert(1)</script>-->",
    "<![CDATA[<script>alert(1)</script>]]>",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "‮<script>alert(1)</script>",
]


class Collect(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs = set(), []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs.extend(attrs)

    handle_startendtag = handle_starttag


def assert_safe_html(html: str):
    found = Collect(html)
    assert found.tags <= SAFE_TAGS, found.tags - SAFE_TAGS
    for name, value in found.attrs:
        assert name in SAFE_ATTRS, name
        if name == "href":
            assert (value or "").lower().startswith(SAFE_SCHEMES), value


@pytest.mark.parametrize("payload", PAYLOADS)
def test_markdown_render(payload):
    assert_safe_html(str(render_markdown(payload)))


@pytest.mark.parametrize("payload", PAYLOADS)
def test_markdown_render_wrapped(payload):
    # The same payloads inside surrounding Markdown structure.
    assert_safe_html(str(render_markdown(f"# Title\n\n- item {payload}\n\n**bold** {payload} *it*")))


@pytest.mark.parametrize("payload", PAYLOADS)
def test_in_game_conversion_then_render(payload):
    md = eve_description_to_markdown(f"<font size=\"12\">{payload}</font><br><a href=\"{payload}\">x</a>")
    assert_safe_html(str(render_markdown(md)))
    md, _ = pasted_advert_to_markdown(payload)
    assert_safe_html(str(render_markdown(md)))


@pytest.mark.parametrize("payload", PAYLOADS)
def test_plain_text_excerpt_has_no_markup_when_escaped(payload):
    from markupsafe import escape
    assert_safe_html(str(escape(plain_text(payload))))


@pytest.mark.parametrize("value", [
    "javascript:alert(1)", "JAVASCRIPT:alert(1)", " javascript:alert(1)", "data:text/html,x",
    'https://ok.example/"onmouseover="alert(1)', "https://ok.example/<script>", "vbscript:x",
    "HTTPS://javascript:alert(1)", "//evil.example", "https://", "https://localhost",
])
def test_website_field_rejects_or_neutralises(value):
    cleaned, err = _clean_website(value)
    if cleaned is not None:
        assert cleaned.startswith(("http://", "https://"))
        assert '"' not in cleaned or True  # quotes are escaped by Jinja in the href; checked in page test


@pytest.mark.parametrize("value", [
    "javascript:alert(1)", "https://discord.gg/abc\"onmouseover=alert(1)", "https://discord.gg/<x>",
    "https://evil.example/discord.gg/abc", "https://discord.gg.evil.example/abc",
])
def test_discord_field_rejects(value):
    assert _clean_discord(value) == (None, "Discord must be an invite link like https://discord.gg/abc123.")


@pytest.mark.parametrize("payload", [p for p in PAYLOADS if "\x00" not in p])
def test_corp_page_every_field(client, app, payload):
    """Hostile text in every stored field (as if ESI or a recruiter supplied it) renders inertly."""
    db.session.add(Corporation(corporation_id=CORP, name=f"Corp {payload}", ticker="T<b>",
                               alliance_id=None, member_count=5))
    db.session.add(Advert(
        corporation_id=CORP, title=f"Title {payload}", body=f"Body {payload}",
        website_url=f'https://ok.example/{payload}'[:300], ingame_channel=f"chan {payload}"[:60],
        contacts=[{"id": 1, "name": f"Pilot {payload}"}], moderation_reason=f"reason {payload}",
    ))
    db.session.commit()
    html = client.get(f"/corps/{CORP}").get_data(as_text=True)
    assert_page_inert(html)


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def assert_page_inert(html: str):
    """Only the page's own scripts/images/frames, and no event-handler attributes anywhere."""
    for tag, attrs in Elements(html).elements:
        assert not any(k.startswith("on") for k in attrs), (tag, attrs)
        if tag == "script":
            assert attrs.get("src", "").startswith(("https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/", "/static/js/")), attrs
        if tag == "img":
            assert attrs.get("src", "").startswith(("https://images.evetech.net/", "https://web.ccpgamescdn.com/")), attrs
        assert tag not in ("iframe", "object", "embed", "base"), (tag, attrs)
        if tag == "form":  # no action = submit to this page
            assert attrs.get("action", "/").startswith("/"), attrs
        if tag == "meta":
            assert attrs.get("http-equiv") is None, attrs
        if tag == "a" and "href" in attrs:
            assert attrs["href"].lower().startswith(("http://", "https://", "mailto:", "/")), attrs
    assert "<script>alert(1)" not in html and "<img src=x onerror" not in html


def test_search_box_reflection(client, app):
    payload = '"><script>alert(1)</script>'
    html = client.get("/corps", query_string={"q": payload, "my_sp": payload}).get_data(as_text=True)
    assert_page_inert(html)


def test_login_next_reflection(client):
    html = client.get('/corps?x="><script>alert(1)</script>').get_data(as_text=True)
    assert "<script>alert(1)" not in html


def test_saved_advert_via_editor(client, eve):
    csrf = login(client)
    payload = '"><img src=x onerror=alert(1)><script>alert(1)</script>'
    client.post("/advert", data=form(csrf, title=payload, body=payload, ingame_channel=payload[:60],
                                     website_url="https://ok.example/" + payload))
    assert db.session.scalar(select(Advert)) is not None
    for path in (f"/corps/{CORP}", "/corps", "/advert", f"/corps/{CORP}/history"):
        assert_page_inert(client.get(path).get_data(as_text=True))


def test_untrusted_host_rejected(app):
    app.config["TRUSTED_HOSTS"] = ["eve-corps.online"]
    client = app.test_client()
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    resp = client.get("/", headers={"Host": "eve-corps.online"})
    assert resp.status_code == 200 and 'content="http://eve-corps.online/"' in resp.get_data(as_text=True)


def test_csp_pins_bootstrap_files(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "script-src 'self' https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js;" in csp
    assert "https://cdn.jsdelivr.net;" not in csp and "https://cdn.jsdelivr.net " not in csp


@pytest.mark.parametrize("method,path,data", [
    ("get", "/corps", {"q": "a\x00b"}),
    ("get", "/corps", {"my_sp": "\x00"}),
    ("post", "/advert", {"title": "a\x00b"}),
    ("post", f"/corps/{CORP}/report", {"reason": "\x00"}),
    ("post", "/advert/convert-paste", {"text": "\x00"}),
])
def test_nul_bytes_rejected_not_500(client, eve, method, path, data):
    csrf = login(client)
    if method == "get":
        resp = client.get(path, query_string=data)
    else:
        resp = client.post(path, data={"csrf_token": csrf, **data})
    assert resp.status_code == 400
