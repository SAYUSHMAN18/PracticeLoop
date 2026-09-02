"""Loop Mentor answers in markdown; the panel has to render it.

Models format their replies whether or not you ask -- bold labels, bullet
lists, and tables for anything plan-shaped. Rendering that as plain text is
what turned a study plan into a wall of literal `**Week 1**` and `| Week |`
pipes for the user who reported this.

The other half is that model output is not fully trusted input: a prompt
injection buried in a pasted job description could try to get `<script>` or
a `javascript:` link into the page. `html=False` is the guarantee that
can't happen, so it's tested here directly.
"""

from __future__ import annotations

from app.core.db import get_pool
from app.core.markdown import render_markdown
from tests.conftest import signup


def test_bold_and_lists_become_real_markup():
    html = render_markdown("**Week 1**\n- **Listening:** two tests\n- **Reading:** two passages")
    assert "<strong>Week 1</strong>" in html
    assert "<ul>" in html and "<li>" in html
    assert "**" not in html


def test_tables_become_real_tables():
    html = render_markdown("| Week | Focus |\n|------|-------|\n| 1-2 | Data structures |")
    assert "<table>" in html
    assert "<th>Week</th>" in html
    assert "<td>Data structures</td>" in html
    assert "|" not in html


def test_code_spans_and_fences_render():
    html = render_markdown("Run `pip install x`:\n\n```python\nprint('hi')\n```")
    assert "<code>pip install x</code>" in html
    assert "<pre>" in html


def test_raw_html_in_model_output_is_escaped_not_executed():
    """The whole security model. A model talked into emitting a script tag
    must produce visible text, never markup."""
    html = render_markdown("<script>alert('xss')</script><img src=x onerror=alert(1)>")
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_javascript_urls_are_not_turned_into_links():
    html = render_markdown("[click me](javascript:alert(1))")
    assert "javascript:" not in html.lower().replace("javascript:alert(1)", "")
    assert 'href="javascript:' not in html


def test_empty_and_none_render_to_nothing():
    assert render_markdown("") == ""
    assert render_markdown(None) == ""


async def test_the_mentor_panel_renders_an_assistant_reply_as_html(client):
    await signup(client, "md-panel@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "md-panel@example.com")
    conversation_id = await pool.fetchval(
        """INSERT INTO mentor_conversations (user_id, context_type, context_id)
           VALUES ($1, 'general', NULL) RETURNING conversation_id""",
        user_id,
    )
    await pool.execute(
        "INSERT INTO mentor_messages (conversation_id, role, content) VALUES ($1, 'assistant', $2)",
        conversation_id,
        "**Plan**\n\n| Week | Focus |\n|---|---|\n| 1 | Arrays |",
    )

    response = await client.get("/mentor/conversation?context_type=general")
    assert response.status_code == 200
    assert "<strong>Plan</strong>" in response.text
    assert "<td>Arrays</td>" in response.text
    assert "| Week | Focus |" not in response.text


async def test_a_students_own_message_is_shown_literally(client):
    """Only the assistant writes markdown. Running a student's question
    through a renderer would silently reformat what they typed."""
    await signup(client, "md-user@example.com")
    pool = await get_pool()
    user_id = await pool.fetchval("SELECT user_id FROM users WHERE email = $1", "md-user@example.com")
    conversation_id = await pool.fetchval(
        """INSERT INTO mentor_conversations (user_id, context_type, context_id)
           VALUES ($1, 'general', NULL) RETURNING conversation_id""",
        user_id,
    )
    await pool.execute(
        "INSERT INTO mentor_messages (conversation_id, role, content) VALUES ($1, 'user', $2)",
        conversation_id,
        "what does **this** mean?",
    )

    response = await client.get("/mentor/conversation?context_type=general")
    assert "what does **this** mean?" in response.text
    assert "<strong>this</strong>" not in response.text


def test_math_notation_survives_because_emphasis_is_off_there():
    """Regression: Math Lab steps are full of `*` as multiplication. With
    emphasis on, "2*x + 3 = 9 ... 2*x = 6" pairs the asterisks into an <em>
    and the rendered solution silently loses both multiplication signs."""
    steps = "Start with: 2*x + 3 = 9\nSubtract 3: 2*x = 6\nDivide by 2: x = 3"

    mangled = render_markdown(steps)
    assert "<em>" in mangled  # the default renderer really does eat them

    safe = render_markdown(steps, emphasis=False)
    assert "<em>" not in safe
    assert "2*x + 3 = 9" in safe
    assert "2*x = 6" in safe


def test_disabling_emphasis_keeps_the_rest_of_markdown():
    html = render_markdown("# Steps\n\n- first\n- second\n\nUse `x = 3`", emphasis=False)
    assert "<h1>" in html
    assert "<li>first</li>" in html
    assert "<code>x = 3</code>" in html
