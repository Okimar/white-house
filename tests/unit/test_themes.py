"""Theme storage: validation, sharing as one file, version history, and the image library."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from tavern.devtools.fake_llm import _pattern_png
from tavern.errors import NotFound
from tavern.formats.media import sniff_image
from tavern.paths import BUILTIN_THEMES_DIR
from tavern.storage.assets import AssetError, AssetStore
from tavern.storage.themes import (
    MAX_FILE_BYTES,
    ThemeError,
    ThemeExists,
    ThemeStore,
    normalize_manifest,
    read_theme_zip,
    safe_relpath,
    slugify,
)


def make_zip(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def manifest(**fields: object) -> str:
    return json.dumps({"format": "tavern-theme", "format_version": 1, "id": "night", "name": "Night", **fields})


@pytest.fixture
def store(tmp_path: Path) -> ThemeStore:
    builtin = tmp_path / "builtin"
    (builtin / "default").mkdir(parents=True)
    (builtin / "default" / "theme.json").write_text(json.dumps({"name": "Standard"}), encoding="utf-8")
    return ThemeStore(builtin, tmp_path / "user")


@pytest.mark.parametrize("bad", [
    "../x.css", "a/../../x.css", "/etc/x.css", "C:/x.css", ".hidden.css", "a/.git/x.css",
    "page.html", "run.exe", "noext", "", "con.css", "a//b.css",
])
def test_unsafe_theme_paths_are_rejected(bad: str) -> None:
    with pytest.raises(ThemeError):
        safe_relpath(bad)


def test_safe_paths_are_normalised() -> None:
    assert safe_relpath("assets\\bg.PNG") == "assets/bg.PNG"
    assert safe_relpath("theme.css") == "theme.css"


def test_slugify_transliterates_russian() -> None:
    assert slugify("Моя Сакура!") == "moya-sakura"
    assert slugify("!!!") == "theme"


def test_manifest_drops_unknown_fields_and_checks_paths() -> None:
    clean = normalize_manifest({"name": "X", "evil": 1, "base": "purple", "script": "fx.js"}, theme_id="x")
    assert "evil" not in clean and clean["base"] == "auto" and clean["script"] == "fx.js"
    with pytest.raises(ThemeError):
        normalize_manifest({"script": "fx.css"}, theme_id="x")
    with pytest.raises(ThemeError, match="newer"):
        normalize_manifest({"format_version": 99}, theme_id="x")


def test_builtin_themes_in_the_repo_are_valid() -> None:
    themes = ThemeStore(BUILTIN_THEMES_DIR, Path("unused")).list()
    ids = [t["id"] for t in themes]
    assert ids[0] == "default"
    assert {"default", "light", "dark", "sakura"} <= set(ids)
    assert not [t for t in themes if "error" in t]
    sakura = next(t for t in themes if t["id"] == "sakura")
    assert sakura["script_sha256"] and sakura["has_css"] and sakura["builtin"]


def test_import_export_round_trip(store: ThemeStore) -> None:
    blob = make_zip({"theme.json": manifest(version="1"), "theme.css": ":root{}", "assets/bg.png": _pattern_png("x")})
    installed = store.import_zip(blob)
    assert installed["id"] == "night" and not installed["builtin"]
    name, exported = store.export("night")
    assert name == "night.tavern-theme"
    again, files = read_theme_zip(exported)
    assert again["name"] == "Night" and set(files) == {"theme.json", "theme.css", "assets/bg.png"}


def test_import_accepts_a_zipped_folder_and_skips_junk(store: ThemeStore) -> None:
    blob = make_zip({
        "night/theme.json": manifest(), "night/theme.css": "", "__MACOSX/night/._theme.css": "x",
        "night/.DS_Store": "x",
    })
    assert store.import_zip(blob)["id"] == "night"


@pytest.mark.parametrize("files, message", [
    ({"theme.css": ""}, "no theme.json"),
    ({"theme.json": manifest(), "../evil.css": ""}, "Invalid file path"),
    ({"theme.json": manifest(), "index.html": "<script>"}, "not allowed"),
    ({"theme.json": manifest(script="fx.js")}, "missing"),
    ({"theme.json": "{not json"}, "not valid JSON"),
])
def test_bad_theme_files_are_refused(store: ThemeStore, files: dict, message: str) -> None:
    with pytest.raises(ThemeError, match=message):
        store.import_zip(make_zip(files))
    assert store.list() == [t for t in store.list() if t["builtin"]]  # nothing half-installed


def test_zip_bomb_is_cut_off(store: ThemeStore) -> None:
    huge = make_zip({"theme.json": manifest(), "big.txt": b"0" * (MAX_FILE_BYTES + 1)})
    with pytest.raises(ThemeError, match="too large"):
        store.import_zip(huge)


def test_not_a_zip(store: ThemeStore) -> None:
    with pytest.raises(ThemeError, match="Это не тема"):
        store.import_zip(b"hello")


def test_replacing_needs_consent_and_keeps_the_old_version(store: ThemeStore) -> None:
    store.import_zip(make_zip({"theme.json": manifest(version="1"), "theme.css": "/* v1 */"}))
    v2 = make_zip({"theme.json": manifest(version="2"), "theme.css": "/* v2 */"})
    with pytest.raises(ThemeExists) as exc:
        store.import_zip(v2)
    assert exc.value.details["existing"]["version"] == "1" and exc.value.details["incoming"]["version"] == "2"
    assert store.import_zip(v2, replace=True)["version"] == "2"
    history = store.versions("night")
    assert [v["theme_version"] for v in history] == ["1"]
    restored = store.restore("night", history[0]["version"])
    assert restored["version"] == "1"
    assert store.file("night", "theme.css").read_text() == "/* v1 */"
    assert [v["theme_version"] for v in store.versions("night")] == ["2"]  # the undo can be undone


def test_builtin_id_clash_installs_a_custom_copy(store: ThemeStore) -> None:
    blob = make_zip({"theme.json": json.dumps({"id": "default", "name": "Fake standard"})})
    first = store.import_zip(blob)
    assert first["id"] == "default-custom"
    with pytest.raises(ThemeExists):
        store.import_zip(blob)  # importing again targets the same copy, not a new one
    assert store.get("default")["builtin"]


def test_deleted_theme_can_be_restored(store: ThemeStore) -> None:
    store.create("Night")
    store.delete("night")
    with pytest.raises(NotFound):
        store.get("night")
    assert [d["id"] for d in store.deleted()] == ["night"]
    store.restore("night", store.versions("night")[0]["version"])
    assert store.get("night")["name"] == "Night" and store.deleted() == []


def test_create_copies_and_builtins_are_read_only(store: ThemeStore) -> None:
    copy = store.create("Мой стандарт", source="default")
    assert copy["id"] == "moy-standart" and copy["has_css"]
    assert store.create("Мой стандарт")["id"] == "moy-standart-2"
    with pytest.raises(ThemeError, match="Built-in"):
        store.write_file("default", "theme.css", b"x")
    with pytest.raises(ThemeError, match="Built-in"):
        store.delete("default")


def test_write_file_validates_manifest_and_keeps_id(store: ThemeStore) -> None:
    store.create("Night")
    updated = store.write_file("night", "theme.json", json.dumps({"id": "other", "name": "Renamed"}).encode())
    assert updated["id"] == "night" and updated["name"] == "Renamed"
    with pytest.raises(ThemeError):
        store.write_file("night", "theme.json", b"{broken")
    with pytest.raises(ThemeError):
        store.write_file("night", "../escape.css", b"x")


def test_broken_theme_folder_is_listed_with_an_error(store: ThemeStore) -> None:
    folder = store.user_dir / "broken"
    folder.mkdir(parents=True)
    (folder / "theme.json").write_text("{nope", encoding="utf-8")
    broken = next(t for t in store.list() if t["id"] == "broken")
    assert "error" in broken
    with pytest.raises(ThemeError, match="broken"):
        store.export("broken")


def test_theme_file_cannot_escape_through_a_symlink(store: ThemeStore, tmp_path: Path) -> None:
    store.create("Night")
    secret = tmp_path / "secret.css"
    secret.write_text("secret")
    try:
        (store.user_dir / "night" / "leak.css").symlink_to(secret)
    except OSError:
        pytest.skip("symlinks not permitted here")
    with pytest.raises(NotFound):
        store.file("night", "leak.css")


def test_asset_store_detects_type_and_never_overwrites(tmp_path: Path) -> None:
    assets = AssetStore(tmp_path / "assets")
    png = _pattern_png("a")
    first = assets.save("My Pic.jpg", png)  # wrong extension: the content decides
    assert first["name"] == "My-Pic.png" and first["type"] == "image/png"
    assert assets.save("My Pic.png", png)["name"] == "My-Pic-2.png"
    assert {a["name"] for a in assets.list()} == {"My-Pic-2.png", "My-Pic.png"}
    with pytest.raises(AssetError):
        assets.save("x.png", b"<html>not an image</html>")
    with pytest.raises(NotFound):
        assets.path("../secrets.json")


def test_sniff_image() -> None:
    assert sniff_image(_pattern_png("x")) == ".png"
    assert sniff_image(b"\xff\xd8\xff\xe0rest") == ".jpg"
    assert sniff_image(b"GIF89a...") == ".gif"
    assert sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    assert sniff_image(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>') == ".svg"
    assert sniff_image(b"plain text") is None


def test_set_colors_writes_block_keeps_history_and_rejects_bad_values(store: ThemeStore) -> None:
    store.create("Night", source="default")
    before = len(store.versions("night"))
    done = store.set_colors("night", {"bg": "#112233", "accent": "#FFAA00"})
    assert done["colors"] == {"bg": "#112233", "accent": "#ffaa00"}
    assert len(store.versions("night")) == before + 1
    css = store.file("night", "theme.css").read_text(encoding="utf-8")
    assert css.count("tavern-colors:") == 1 and ":root:root" in css
    # a second save replaces the block, it does not pile up
    store.set_colors("night", {"fg": "#ffffff"})
    css = store.file("night", "theme.css").read_text(encoding="utf-8")
    assert css.count("tavern-colors:") == 1 and "--bg" not in css
    assert store.set_colors("night", {})["colors"] == {}
    assert "tavern-colors" not in store.file("night", "theme.css").read_text(encoding="utf-8")
    for bad in ({"nope": "#112233"}, {"bg": "red"}, {"bg": "#12345"}):
        with pytest.raises(ThemeError):
            store.set_colors("night", bad)
    with pytest.raises(ThemeError, match="Built-in"):
        store.set_colors("default", {"bg": "#112233"})
