"""Advert text: safe Markdown rendering, and conversion of EVE in-game descriptions.

Safety comes from three independent layers:
  1. markdown-it with raw HTML off and the image rule disabled, so nothing in
     the source can produce <img>, <script>, <iframe>, etc.
  2. nh3 (ammonia) with a strict allowlist of tags, attributes and URL schemes,
     run on the rendered HTML in case a parser bug lets something through.
  3. The Content-Security-Policy header (see evecorps.__init__), which stops the
     browser loading anything from hosts we haven't approved.
"""

import ast
import html as html_lib
import re
from html.parser import HTMLParser

import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "s", "del", "code", "pre", "blockquote", "hr",
    "ul", "ol", "li", "h3", "h4", "h5", "a",
    "table", "thead", "tbody", "tr", "th", "td",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "ol": {"start"}}
ALLOWED_SCHEMES = {"http", "https", "mailto"}
LINK_REL = "nofollow noopener noreferrer ugc"


def _build_markdown() -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": True, "breaks": True, "typographer": False},
    )
    md.enable(["table", "strikethrough", "linkify"])
    md.disable("image")

    # The page already has h1 (corp title); shift advert headings to h3..h5.
    def heading(renderer, tokens, idx, options, env):
        tok = tokens[idx]
        tok.tag = f"h{min(int(tok.tag[1]) + 2, 5)}"
        return renderer.renderToken(tokens, idx, options, env)

    md.add_render_rule("heading_open", heading)
    md.add_render_rule("heading_close", heading)
    return md


_md = _build_markdown()


def render_markdown(source: str | None) -> Markup:
    """Markdown -> sanitized HTML, safe to output unescaped."""
    html = _md.render(source or "")
    clean = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel=LINK_REL,
        strip_comments=True,
    )
    return Markup(clean)


# --- EVE in-game description -> Markdown -----------------------------------

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|~])")


def _escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def _unwrap_python_literal(raw: str) -> str:
    """Some ESI descriptions are a Python repr: u'...' with \\x and \\u escapes."""
    s = raw.strip()
    if len(s) >= 3 and s[0] == "u" and s[1] in "'\"" and s[-1] == s[1]:
        try:
            value = ast.literal_eval(s)  # only ever evaluates a string literal
        except (ValueError, SyntaxError):
            return raw
        if isinstance(value, str):
            return value
    return raw


class _EveMarkupConverter(HTMLParser):
    """Turns EVE's in-game markup (<font>, <b>, <a href="showinfo:...">, ...) into Markdown.

    Formatting EVE has but Markdown doesn't (colour, size, underline) is dropped;
    the text is kept. In-game-only links become plain text.
    """

    INLINE = {"b": "**", "strong": "**", "i": "*", "em": "*"}
    # Private-use placeholders so opening and closing markers can be told apart
    # when tidying whitespace, then swapped for the real Markdown markers.
    OPEN = {"**": "\ue000", "*": "\ue002"}
    CLOSE = {"**": "\ue001", "*": "\ue003"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open_inline: list[str] = []
        self.link: dict | None = None

    def _emit(self, text: str) -> None:
        if self.link is not None:
            self.link["text"].append(text)
        else:
            self.out.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self._emit("\n")
        elif tag in self.INLINE:
            self.open_inline.append(tag)
            self._emit(self.OPEN[self.INLINE[tag]])
        elif tag == "a":
            self._close_link()  # EVE markup sometimes nests or never closes links
            self.link = {"href": dict(attrs).get("href") or "", "text": []}

    def handle_endtag(self, tag):
        if tag in self.INLINE and tag in self.open_inline:
            # Close anything opened after it too, so markers stay balanced.
            while self.open_inline:
                t = self.open_inline.pop()
                self._emit(self.CLOSE[self.INLINE[t]])
                if t == tag:
                    break
        elif tag == "a":
            self._close_link()

    def handle_data(self, data):
        self._emit(_escape(data))

    def _close_link(self):
        if self.link is None:
            return
        link, self.link = self.link, None
        text = "".join(link["text"]).strip()
        href = link["href"].strip()
        scheme = href.split(":", 1)[0].lower() if ":" in href else ""
        if scheme in ("http", "https") and text:
            self.out.append(f"[{text}](<{href}>)")
        elif scheme in ("http", "https"):
            self.out.append(f"<{href}>")
        elif scheme == "joinchannel" and text:
            self.out.append(f"{text} (in-game channel)")
        else:
            # showinfo:, fitting:, killReport: etc. only work inside the client.
            self.out.append(text)

    def result(self) -> str:
        self._close_link()
        while self.open_inline:
            self.out.append(self.CLOSE[self.INLINE[self.open_inline.pop()]])
        text = "".join(self.out)
        opens, closes = "".join(self.OPEN.values()), "".join(self.CLOSE.values())
        # Markers must hug the text ("** foo **" doesn't render), and empty pairs go.
        for _ in range(3):
            text = re.sub(rf"([{opens}])(\s+)", r"\2\1", text)
            text = re.sub(rf"(\s+)([{closes}])", r"\2\1", text)
            text = re.sub("\ue000\ue001|\ue002\ue003", "", text)
        for marker, ph in self.OPEN.items():
            text = text.replace(ph, marker)
        for marker, ph in self.CLOSE.items():
            text = text.replace(ph, marker)
        lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def eve_description_to_markdown(raw: str | None) -> str:
    if not raw:
        return ""
    parser = _EveMarkupConverter()
    parser.feed(_unwrap_python_literal(raw))
    parser.close()
    return parser.result()


def plain_text(source: str | None, limit: int = 200) -> str:
    """Advert Markdown as a short plain-text excerpt (for link previews)."""
    html = _md.render(source or "")
    text = nh3.clean(html, tags=set())  # drop every tag, keep the text
    # nh3 leaves entities encoded; the result is autoescaped again by Jinja.
    text = re.sub(r"\s+", " ", html_lib.unescape(text)).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return text


_EVE_TAG = re.compile(r"<\s*/?\s*(font|br|b|i|u|a|loc|color|url)\b", re.IGNORECASE)


def pasted_advert_to_markdown(raw: str | None) -> tuple[str, str]:
    """Text pasted from the in-game advert editor -> (markdown, detected format).

    The client may put EVE markup or plain text on the clipboard, so handle both:
    markup goes through the same converter as the in-game description; plain text
    is kept as typed (line breaks are preserved by the renderer).
    """
    text = _unwrap_python_literal((raw or "").replace("\r\n", "\n").replace("\r", "\n"))
    if _EVE_TAG.search(text):
        return eve_description_to_markdown(text), "eve"
    lines = [ln.rstrip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip(), "plain"
