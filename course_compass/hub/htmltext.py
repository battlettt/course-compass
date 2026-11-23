"""HTML from LEARN (announcements, instructions, content pages): an allowlist sanitizer and a text extractor.

Announcement HTML is written by instructors and comes from another site, so it
is treated as untrusted. `sanitize` keeps only formatting tags and safe links;
the web UI also sends a strict Content-Security-Policy, so even a sanitizer
bug could not run script.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

ALLOWED_TAGS = {
    "a", "b", "strong", "i", "em", "u", "s", "p", "br", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "code", "pre", "table", "thead", "tbody", "tr", "th", "td", "span", "div", "hr", "sub", "sup",
}
VOID_TAGS = {"br", "hr"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math", "head",
                "title", "form", "select", "textarea", "button"}
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "hr",
              "table", "ul", "ol", "section", "article"}
HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 4, "h6": 4}


def safe_url(raw: str, base: str = "") -> str | None:
    """Absolute http(s) or mailto URL, or None for anything else (javascript:, data:, ...)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    url = urljoin(base + "/", raw) if base and not urlparse(raw).scheme else raw
    scheme = urlparse(url).scheme.lower()
    if scheme in ("http", "https", "mailto"):
        return url
    return None


class _Sanitizer(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: list[str] = []
        self.open: list[str] = []
        self.drop_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            self.drop_depth += 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        if tag == "a":
            href = safe_url(dict(attrs).get("href", ""), self.base)
            if href:
                self.out.append(f'<a href="{html.escape(href, quote=True)}" rel="noopener noreferrer" '
                                'target="_blank">')
            else:
                self.out.append("<a>")
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in VOID_TAGS and not self.drop_depth:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.drop_depth = max(0, self.drop_depth - 1)
            return
        if self.drop_depth or tag not in self.open:
            return
        while self.open:
            top = self.open.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        if not self.drop_depth:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def sanitize(raw_html: str, base: str = "") -> str:
    parser = _Sanitizer(base)
    parser.feed(raw_html or "")
    parser.close()
    return parser.result()


class _Text(HTMLParser):
    def __init__(self, markdown_headings: bool):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.drop_depth = 0
        self.md = markdown_headings
        self.in_li = False

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            self.drop_depth += 1
        elif not self.drop_depth:
            if tag in BLOCK_TAGS:
                self.parts.append("\n")
            if self.md and tag in HEADING_TAGS:
                self.parts.append("#" * HEADING_TAGS[tag] + " ")
            if tag == "li":
                self.parts.append("- ")
            if tag in ("td", "th"):
                self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.drop_depth = max(0, self.drop_depth - 1)
        elif not self.drop_depth and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.drop_depth:
            self.parts.append(data)


def to_text(raw_html: str, markdown_headings: bool = False) -> str:
    """Readable plain text (or light markdown with # headings) from HTML."""
    parser = _Text(markdown_headings)
    parser.feed(raw_html or "")
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out, blank = [], False
    for line in lines:
        if not line:
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()
