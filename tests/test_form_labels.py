"""Regression tests for a real Lighthouse accessibility finding: login,
signup, and the profile page's target-role/target-companies fields had
bare <label> text with no `for` attribute pointing at the corresponding
input's `id` -- a screen reader can't associate the two, so it announces
an unlabeled field. Fixed for real (login/signup went from an
accessibility score of 90 to 100), these lock the fix in place."""

from tests.conftest import signup


async def test_login_page_labels_are_associated_with_their_inputs(client):
    response = await client.get("/login")
    assert '<label for="f-email">Email</label>' in response.text
    assert 'id="f-email"' in response.text
    assert '<label for="f-password">Password</label>' in response.text
    assert 'id="f-password"' in response.text


async def test_signup_page_labels_are_associated_with_their_inputs(client):
    response = await client.get("/signup")
    assert '<label for="f-name">Name</label>' in response.text
    assert 'id="f-name"' in response.text
    assert '<label for="f-email">Email</label>' in response.text
    assert '<label for="f-password">Password</label>' in response.text


async def test_profile_target_fields_are_associated_with_their_labels(client):
    await signup(client, "labels@example.com")
    response = await client.get("/profile")
    assert '<label for="f-target-role">Target role</label>' in response.text
    assert 'id="f-target-role"' in response.text
    assert '<label for="f-target-companies">Target companies</label>' in response.text
    assert 'id="f-target-companies"' in response.text
