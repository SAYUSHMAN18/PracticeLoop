from tests.conftest import signup


async def test_command_palette_markup_is_present_on_authenticated_pages(client):
    await signup(client, "palette@example.com")
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert 'id="command-palette-trigger"' in response.text
    assert 'id="command-palette-backdrop"' in response.text
    assert 'id="command-palette-input"' in response.text


async def test_command_palette_is_not_present_on_logged_out_auth_pages(client):
    response = await client.get("/login")
    assert response.status_code == 200
    assert "command-palette" not in response.text


async def test_command_palette_covers_import_and_a_theme_action(client):
    await signup(client, "palette-cmds@example.com")
    page = (await client.get("/dashboard")).text
    assert '"/practice/import"' in page  # Wave 7's import page is reachable from the palette
    assert 'action: "theme"' in page  # a non-navigation command that cycles the theme
