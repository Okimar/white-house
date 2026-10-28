"""Stage 3а over HTTP: themes, theme files, the image library, picture generation, safety headers."""

from __future__ import annotations

import io
import json
import zipfile

from tavern.devtools.fake_llm import _pattern_png
from tavern.formats.cards import card_to_png
from tavern.storage.assets import MAX_IMAGE_BYTES


def theme_zip(theme_id: str = "night", version: str = "1") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("theme.json", json.dumps({"id": theme_id, "name": "Night", "version": version}))
        archive.writestr("theme.css", f"/* v{version} */")
        archive.writestr("assets/pic.svg", '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    return buffer.getvalue()


async def test_page_has_a_strict_content_security_policy(client):
    policy = (await client.get("/")).headers["content-security-policy"]
    assert "default-src 'self'" in policy and "script-src 'self'" in policy and "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy


async def test_themes_list_has_builtins_first(client):
    data = (await client.get("/api/themes")).json()
    ids = [t["id"] for t in data["themes"]]
    assert ids[0] == "default" and "sakura" in ids
    assert data["deleted"] == []


async def test_builtin_theme_files_are_served_with_revalidation(client):
    css = await client.get("/themes/sakura/theme.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert css.headers["cache-control"] == "no-cache" and "etag" in css.headers
    script = await client.get("/themes/sakura/script.js")
    assert script.headers["content-type"].startswith("text/javascript")
    picture = await client.get("/themes/sakura/assets/petal.svg")
    assert "sandbox" in picture.headers["content-security-policy"]


async def test_theme_files_cannot_be_escaped(client):
    for path in ("/themes/sakura/../../tavern/app.py", "/themes/sakura/%2e%2e/%2e%2e/README.md",
                 "/themes/..%2f..%2fdata/secrets.json", "/themes/nope/theme.css", "/themes/sakura/missing.css"):
        response = await client.get(path)
        assert response.status_code == 404, path


async def test_import_conflict_replace_and_restore(client):
    first = await client.post("/api/themes/import", content=theme_zip(version="1"))
    assert first.status_code == 201, first.text
    clash = await client.post("/api/themes/import", content=theme_zip(version="2"))
    assert clash.status_code == 409
    details = clash.json()["error"]["details"]
    assert details["existing"]["version"] == "1" and details["incoming"]["version"] == "2"
    replaced = await client.post("/api/themes/import?replace=1", content=theme_zip(version="2"))
    assert replaced.json()["version"] == "2" and replaced.json()["versions"] == 1
    assert (await client.get("/themes/night/theme.css")).text == "/* v2 */"
    versions = (await client.get("/api/themes/night/versions")).json()["versions"]
    restored = await client.post("/api/themes/night/restore", json={"version": versions[0]["version"]})
    assert restored.json()["version"] == "1"
    assert (await client.get("/themes/night/theme.css")).text == "/* v1 */"


async def test_export_is_one_file(client):
    await client.post("/api/themes/import", content=theme_zip())
    response = await client.get("/api/themes/night/export")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert "night.tavern-theme" in response.headers["content-disposition"]
    assert "theme.json" in zipfile.ZipFile(io.BytesIO(response.content)).namelist()


async def test_svg_from_a_theme_cannot_run_script_when_opened(client):
    await client.post("/api/themes/import", content=theme_zip())
    response = await client.get("/themes/night/assets/pic.svg")
    assert response.headers["content-type"] == "image/svg+xml"
    policy = response.headers["content-security-policy"]
    assert "sandbox" in policy and "default-src 'none'" in policy


async def test_bad_upload_is_rejected_with_a_message(client):
    response = await client.post("/api/themes/import", content=b"not a zip")
    assert response.status_code == 400 and response.json()["error"]["code"] == "bad_theme"


async def test_create_edit_and_delete_a_theme(client):
    created = await client.post("/api/themes", json={"name": "Моя сакура", "from": "sakura"})
    assert created.status_code == 201
    theme_id = created.json()["id"]
    assert created.json()["script_sha256"]  # the script was copied too
    edited = await client.put(f"/api/themes/{theme_id}/files/script.js", content=b"export default () => {}")
    assert edited.json()["script_sha256"] != created.json()["script_sha256"]  # approval must be renewed
    assert (await client.put("/api/themes/sakura/files/theme.css", content=b"x")).status_code == 400
    assert (await client.put(f"/api/themes/{theme_id}/files/page.html", content=b"x")).status_code == 400
    detail = (await client.get(f"/api/themes/{theme_id}")).json()
    assert {"theme.json", "theme.css", "script.js"} <= {f["path"] for f in detail["files"]}

    await client.put("/api/settings", json={"theme": theme_id, "character_themes": {"c1": theme_id, "c2": "dark"}})
    assert (await client.delete(f"/api/themes/{theme_id}")).status_code == 200
    settings = (await client.get("/api/settings")).json()
    assert "theme" not in settings and settings["character_themes"] == {"c2": "dark"}
    data = (await client.get("/api/themes")).json()
    assert theme_id not in [t["id"] for t in data["themes"]]
    assert [d["id"] for d in data["deleted"]] == [theme_id]


async def test_theme_settings_are_validated(client):
    ok = await client.put("/api/settings", json={"character_themes": {"c1": "sakura"},
                                                 "trusted_theme_scripts": {"t": "abc"}})
    assert ok.status_code == 200
    assert (await client.put("/api/settings", json={"character_themes": {"c1": 5}})).status_code == 400
    assert (await client.put("/api/settings", json={"character_themes": ["x"]})).status_code == 400


async def test_asset_upload_list_serve_delete(client):
    uploaded = await client.post("/api/assets?name=Фон ночь.jpg", content=_pattern_png("bg"))
    assert uploaded.status_code == 201
    asset = uploaded.json()
    assert asset["name"].endswith(".png") and asset["type"] == "image/png"
    served = await client.get(asset["url"])
    assert served.status_code == 200 and served.content.startswith(b"\x89PNG")
    assert "sandbox" in served.headers["content-security-policy"]
    assert [a["name"] for a in (await client.get("/api/assets")).json()["assets"]] == [asset["name"]]
    assert (await client.post("/api/assets", content=b"<html>")).status_code == 400
    assert (await client.delete(f"/api/assets/{asset['name']}")).status_code == 200
    assert (await client.get(asset["url"])).status_code == 404


async def test_asset_can_be_copied_into_a_theme(client):
    asset = (await client.post("/api/assets?name=bg", content=_pattern_png("bg"))).json()
    theme_id = (await client.post("/api/themes", json={"name": "Mine"})).json()["id"]
    result = await client.post(f"/api/themes/{theme_id}/assets", json={"asset": asset["name"]})
    assert result.status_code == 200, result.text
    assert (await client.get(f"/themes/{theme_id}/{result.json()['path']}")).content.startswith(b"\x89PNG")


async def test_generate_picture_with_the_fake_server(client, fake_llm):
    conn = (await client.post("/api/connections", json={"name": "img", "base_url": "http://llm/v1",
                                                        "model": "fake-image"})).json()
    missing = await client.post("/api/assets/generate", json={"prompt": "moon"})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "no_image_connection"
    await client.put("/api/settings", json={"image_connection": conn["id"]})
    made = await client.post("/api/assets/generate", json={"prompt": "ночное небо", "size": "512x512"})
    assert made.status_code == 201, made.text
    assert made.json()["name"].endswith(".png")
    assert fake_llm.image_requests[-1] == {"prompt": "ночное небо", "n": 1, "size": "512x512", "model": "fake-image"}
    assert (await client.post("/api/assets/generate", json={"prompt": "x", "size": "huge"})).status_code == 400


async def test_generate_picture_reports_provider_errors(client, fake_llm):
    conn = (await client.post("/api/connections", json={"name": "img", "base_url": "http://llm/v1"})).json()
    await client.put("/api/settings", json={"image_connection": conn["id"]})
    fake_llm.fail_next(400, "image model not available")
    response = await client.post("/api/assets/generate", json={"prompt": "moon"})
    assert response.status_code == 502
    assert "image model not available" in response.json()["error"]["message"]


async def test_character_avatar_endpoint(client):
    character = (await client.post("/api/characters/import/png", content=card_to_png({"name": "Ава"}))).json()
    avatar = await client.get(f"/api/characters/{character['id']}/avatar")
    assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/png"
    plain = (await client.post("/api/characters", json={"name": "Без аватара"})).json()
    assert (await client.get(f"/api/characters/{plain['id']}/avatar")).status_code == 404


async def test_upload_size_limit(client):
    response = await client.post("/api/assets", content=b"\x89PNG\r\n\x1a\n" + b"0" * MAX_IMAGE_BYTES)
    assert response.status_code == 413


def test_fake_pictures_differ_by_prompt():
    assert _pattern_png("a") != _pattern_png("b") and _pattern_png("a").startswith(b"\x89PNG")
