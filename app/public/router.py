"""The public, crawlable surface: a landing page plus the three files
crawlers look for.

Before this, `/` redirected straight to `/login`. A search engine or an AI
assistant following a link to this app found a login form and nothing else
-- no description of what it does, no content to rank, nothing to cite. The
app itself stays private and `noindex`; this module is the one part meant
to be read by someone who hasn't signed up yet.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from markupsafe import Markup

from app.core.config import settings
from app.core.security import current_user_id
from app.core.templates import templates
from app.public import content

router = APIRouter()


def _base_url() -> str:
    return settings.public_base_url.rstrip("/")


def _structured_data() -> Markup:
    """SoftwareApplication + FAQPage as one @graph.

    The FAQ entries are built from the same content.FAQ the page renders,
    so the two can't drift -- structured data that contradicts the visible
    page is a manual-action risk, not just wasted markup.
    """
    base = _base_url()
    graph = [
        {
            "@type": "SoftwareApplication",
            "@id": f"{base}/#app",
            "name": "PracticeLoop",
            "url": base,
            "applicationCategory": "EducationalApplication",
            "operatingSystem": "Any (web browser)",
            "description": content.META_DESCRIPTION,
            "license": "https://opensource.org/licenses/MIT",
            "isAccessibleForFree": True,
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "featureList": [f["title"] for f in content.FEATURES],
        },
        {
            "@type": "FAQPage",
            "@id": f"{base}/#faq",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": item["q"],
                    "acceptedAnswer": {"@type": "Answer", "text": item["a"]},
                }
                for item in content.FAQ
            ],
        },
    ]
    # ensure_ascii=False keeps the real em-dashes and curly quotes instead of
    # \u escapes. The payload then goes into the page unescaped (Markup), so
    # every "<" is hardened to its < form first: that is still valid JSON,
    # and it makes a literal "</script>" impossible to express -- the one way
    # a JSON-LD block can break out of its own tag.
    payload = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
    return Markup(payload.replace("<", "\\u003c"))


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    """The marketing page for a visitor, the dashboard for a member.

    A logged-in user hitting "/" wants their dashboard, not a pitch for the
    product they already use -- so the redirect that used to be the whole
    of this route survives for exactly that case.
    """
    if current_user_id(request) is not None:
        return RedirectResponse("/dashboard")

    base = _base_url()
    return templates.TemplateResponse(
        request,
        "public/landing.html",
        {
            "base_url": base,
            "canonical_url": f"{base}/",
            "structured_data": _structured_data(),
            "tagline": content.TAGLINE,
            "meta_description": content.META_DESCRIPTION,
            "how_it_works": content.HOW_IT_WORKS,
            "features": content.FEATURES,
            "audiences": content.AUDIENCES,
            "faq": content.FAQ,
        },
    )


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    """Everything behind a login is disallowed -- not for secrecy (it all
    302s to /login anyway) but so a crawler spends its budget on the one
    page that has something to say, instead of on dozens of redirects.
    """
    base = _base_url()
    disallowed = [
        "/dashboard",
        "/practice",
        "/learning-paths",
        "/subjects",
        "/assessments",
        "/documents",
        "/projects",
        "/portfolio",
        "/jobs",
        "/labs",
        "/progress",
        "/profile",
        "/account",
        "/classrooms",
        "/guardian",
        "/notifications",
        "/mentor",
        "/welcome",
    ]
    lines = ["User-agent: *", "Allow: /$", "Allow: /login", "Allow: /signup"]
    lines += [f"Disallow: {path}" for path in disallowed]
    lines += ["", f"Sitemap: {base}/sitemap.xml"]
    return "\n".join(lines) + "\n"


@router.get("/sitemap.xml")
async def sitemap() -> PlainTextResponse:
    base = _base_url()
    urls = [(f"{base}/", "1.0"), (f"{base}/signup", "0.5"), (f"{base}/login", "0.3")]
    body = "\n".join(
        f"  <url><loc>{loc}</loc><priority>{priority}</priority></url>" for loc, priority in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n"
    )
    return PlainTextResponse(xml, media_type="application/xml")


@router.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt() -> str:
    """The emerging /llms.txt convention: a short, factual markdown brief
    for AI assistants that read a site rather than crawl it for ranking.

    Deliberately plain and specific. An assistant summarising this app for
    someone should get the mechanism (goal to path, diagnostic, FSRS) and
    the honest limits (free tier sleeps, AI features need a key when
    self-hosting), not a marketing paragraph it has to discount.
    """
    base = _base_url()
    faq = "\n\n".join(f"### {item['q']}\n{item['a']}" for item in content.FAQ)
    features = "\n".join(f"- **{f['title']}**: {f['body']}" for f in content.FEATURES)
    return f"""# PracticeLoop

> {content.META_DESCRIPTION}

PracticeLoop is a free, open-source (MIT) adaptive learning web app at {base}.
It is not a flashcard app: it generates the curriculum, measures the learner,
and schedules the review.

## How it works

1. **Describe a goal** in plain language, or pick a subject template. PracticeLoop
   generates a module -> unit -> lesson structure.
2. **Take a diagnostic** -- a scored multiple-choice quiz that names weak subtopics
   rather than asking the learner to self-rate. Those gaps become a focus module.
3. **Work through lessons** (concept, worked example, checkpoint question). Completing
   a lesson adds its checkpoint to the review queue.
4. **Review** everything -- lesson checkpoints, MCQs, typed recall -- through one
   FSRS-scheduled queue. Typed answers are graded against the stored answer.

## Features

{features}

## Honest limitations

- The public demo runs on a free tier that sleeps after ~15 minutes idle; the first
  request after that takes roughly 50 seconds to wake.
- AI-backed features (lesson generation, diagnostics, answer grading, the mentor)
  need an LLM provider key when self-hosting. Without one they fall back to
  deterministic behaviour or say plainly that they are unavailable -- they never
  fabricate a result.
- Diagnostics and Writing Lab have no non-AI equivalent and are simply unavailable
  with no provider configured.

## FAQ

{faq}

## Source

{base} -- source at https://github.com/SAYUSHMAN18/PracticeLoop (MIT).
"""
