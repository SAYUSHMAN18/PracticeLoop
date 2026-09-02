"""The public landing page's content, defined once in Python.

The FAQ in particular lives here rather than inlined in the template
because it has to appear in two places that must never drift apart: the
rendered `<section id="faq">` a person reads, and the FAQPage JSON-LD a
search or generative engine parses. A question answered one way in the
markup and another way in the structured data is worse for both than not
marking it up at all.

Answers are written to stand alone. A generative engine lifts a single
answer out of its page context, so each one names the product and gives a
specific, checkable fact instead of a superlative.
"""

from __future__ import annotations

TAGLINE = "Learn it, practice it, prove it — all in one loop."

META_DESCRIPTION = (
    "PracticeLoop turns a learning goal into a structured path of lessons, measures what you "
    "actually know with a real diagnostic, and schedules review with the FSRS spaced-repetition "
    "algorithm. Free and open source."
)

HOW_IT_WORKS = [
    {
        "title": "Describe your goal",
        "body": (
            "Type what you're working toward — “prepare for my data structures final”, "
            "“get job-ready in Python” — or pick a subject template. You get back a real "
            "module → unit → lesson structure, not an empty deck to fill in yourself."
        ),
    },
    {
        "title": "Find out what you actually know",
        "body": (
            "Take a diagnostic on any topic: a scored multiple-choice quiz that names the "
            "subtopics you're weak in, instead of asking you to self-rate “beginner” or "
            "“intermediate”. One click turns those gaps into lessons at the top of your path."
        ),
    },
    {
        "title": "Work through lessons that stick",
        "body": (
            "Every lesson has a concept explanation, a worked example, and a checkpoint question. "
            "Marking it complete drops that checkpoint into your review queue — so the path "
            "reinforces itself instead of being a checklist you tick once and forget."
        ),
    },
    {
        "title": "Review on the day you'd forget",
        "body": (
            "One queue schedules everything — lesson checkpoints, multiple-choice checks, and "
            "typed recall — with FSRS, the algorithm family behind modern Anki. Typed answers are "
            "graded against the real answer, not by asking how you think you did."
        ),
    },
]

FEATURES = [
    {
        "icon": "🧭",
        "title": "Goal-to-path learning",
        "body": "A structured curriculum from one sentence, or from a ready-made subject template.",
    },
    {
        "icon": "🔁",
        "title": "FSRS spaced repetition",
        "body": "One honest answer to “what's due today” across every practice format you use.",
    },
    {
        "icon": "📊",
        "title": "Real diagnostics",
        "body": "A scored quiz that names your weak subtopics and builds a focus module from them.",
    },
    {
        "icon": "✦",
        "title": "Loop Mentor",
        "body": "An AI tutor that knows which lesson you're on — hints first, never just the answer.",
    },
    {
        "icon": "🧮",
        "title": "Math & Writing labs",
        "body": "Equations solved and verified symbolically; essays scored on clarity and structure.",
    },
    {
        "icon": "🛠️",
        "title": "Projects & portfolio",
        "body": "Build something, submit it for feedback, and collect it into a shareable portfolio.",
    },
    {
        "icon": "🎯",
        "title": "Career tools",
        "body": "Skill-gap analysis against a real job description, plus resume tailoring and a tracker.",
    },
    {
        "icon": "👥",
        "title": "Classrooms & guardians",
        "body": "Teacher rosters with assignments, and consent-gated progress summaries for parents.",
    },
]

AUDIENCES = [
    {
        "title": "Students",
        "body": "Exam prep with a plan you didn't have to build, and review timed so you keep it.",
    },
    {
        "title": "Self-taught developers",
        "body": "Turn a vague “learn backend” into a real syllabus, then prove the gaps are closed.",
    },
    {
        "title": "Job seekers",
        "body": "Diff a job description against what you've actually recalled, not just what's on your CV.",
    },
    {
        "title": "Teachers & parents",
        "body": "A roster and assignments, or a summary view of a learner's streak and progress.",
    },
]

FAQ = [
    {
        "q": "What is PracticeLoop?",
        "a": (
            "PracticeLoop is a free, open-source adaptive learning platform. It turns a learning "
            "goal into a structured path of modules, units and lessons, measures what you know "
            "with a scored diagnostic, and schedules review using the FSRS spaced-repetition "
            "algorithm so you revisit material just before you would forget it."
        ),
    },
    {
        "q": "Is PracticeLoop free?",
        "a": (
            "Yes. PracticeLoop is free to use and the source code is MIT-licensed on GitHub, so "
            "you can also run your own copy. There is no paid tier and no credit card required "
            "to sign up."
        ),
    },
    {
        "q": "How is PracticeLoop different from Anki or Quizlet?",
        "a": (
            "Anki and Quizlet start from cards you have already made. PracticeLoop starts from a "
            "goal: it generates the lesson structure and the content, measures your level with a "
            "diagnostic instead of asking you to self-assess, and feeds those lessons into one "
            "spaced-repetition queue automatically. Typed answers are graded against the stored "
            "answer rather than by self-rating."
        ),
    },
    {
        "q": "What spaced repetition algorithm does PracticeLoop use?",
        "a": (
            "PracticeLoop uses FSRS (Free Spaced Repetition Scheduler), the algorithm family used "
            "by modern Anki. Lesson checkpoints, multiple-choice questions and typed recall "
            "answers all schedule through the same FSRS engine, so there is a single due queue "
            "rather than one per feature."
        ),
    },
    {
        "q": "Do I need an AI API key to use PracticeLoop?",
        "a": (
            "No. Every AI-backed feature degrades to a deterministic fallback or an honest "
            "“unavailable” message when no provider is configured — never a fabricated result. "
            "If you self-host, you can add a Groq, Gemini or AWS Bedrock key to enable AI lesson "
            "generation, diagnostics and answer grading."
        ),
    },
    {
        "q": "Can I use PracticeLoop to prepare for exams like IELTS, NEET or technical interviews?",
        "a": (
            "Yes. You describe the goal in your own words and PracticeLoop builds the path, so it "
            "is subject-agnostic. It ships with templates for Python, algebra, NEET biology, "
            "English speaking, personal finance and Class 8 science, and includes a separate "
            "interview-prep toolkit with skill-gap analysis and resume tailoring."
        ),
    },
    {
        "q": "Can teachers or parents track a student's progress?",
        "a": (
            "Yes, and only with the learner's explicit consent. A teacher creates a classroom with "
            "a join code that students enter themselves. A guardian receives an invite link the "
            "student generates, and then sees a summary — streak, level, XP, paths completed — "
            "never raw content like mentor conversations or diagnostic detail."
        ),
    },
    {
        "q": "Can I export or delete my data?",
        "a": (
            "Yes. You can export everything as a single JSON file from your account page, or "
            "permanently delete your account with password re-confirmation. Every table that "
            "references a user cascades on delete, so removal is complete rather than partial."
        ),
    },
]
