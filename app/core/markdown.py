"""Render LLM-authored markdown to HTML for display.

Models answer in markdown whether or not you ask them to -- headings, bold
labels, bullet lists, and (for anything plan-shaped) tables. Rendering that
as plain text is what made a study plan come out as a wall of literal
`**Week 1**` and `| Week | Focus |` pipes.

Safety: `html=False` is the whole security model here, and it's a good one.
markdown-it escapes any raw HTML in the source rather than passing it
through, so the only tags that can ever reach the page are the ones
markdown-it itself emits -- p, ul/ol/li, table, strong, em, code, pre, a,
h1-h6, blockquote, hr. A model talked into emitting `<script>` or
`<img onerror=...>` (by a prompt injection buried in a pasted job
description, say) produces escaped text, not markup. That means no separate
sanitizer to keep in step with a changing allow-list. markdown-it's own link
validation additionally rejects `javascript:`, `vbscript:` and `file:` URLs,
leaving them as literal text.

The nonce-based CSP (see core/middleware.py) is the second layer: even an
injected inline script would have no nonce and would never execute.
"""

from __future__ import annotations

from functools import lru_cache

from markdown_it import MarkdownIt
from markupsafe import Markup


@lru_cache(maxsize=2)
def _renderer(emphasis: bool) -> MarkdownIt:
    # CommonMark plus the two extensions models actually reach for. linkify
    # stays off: it rewrites anything that merely looks like a URL, which
    # turns a mention of "app.py" or a bare domain in a code discussion into
    # a link nobody asked for.
    md = (
        MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True})
        .enable("table")
        .enable("strikethrough")
    )
    if not emphasis:
        md = md.disable("emphasis")
    return md


def render_markdown(text: str | None, *, emphasis: bool = True) -> Markup:
    """Markdown -> HTML, safe to drop straight into a template.

    Returns Markup so Jinja doesn't escape the tags we just produced --
    which is only correct because of the `html=False` guarantee above.

    `emphasis=False` turns off `*`/`_` parsing for content where those are
    operators rather than formatting. Math Lab is the case that forced it:
    a step reading "2*x + 3 = 9 ... 2*x = 6" has its two asterisks paired up
    into an <em>, rendering as "2<em>x + 3 = 9 ... 2</em>x = 6" and silently
    deleting the multiplication signs from a worked solution. Lists, line
    breaks, headings and code spans all still work.
    """
    if not text:
        return Markup("")
    return Markup(_renderer(emphasis).render(text))
