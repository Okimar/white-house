from tavern import __version__
from tavern.context import open_context
from tavern.storage import db as db_module
from tavern.storage.backup import list_backups
from tavern.storage.migrations import Migration, load_migrations


async def test_health_reports_version_and_schema(client):
    body = (await client.get("/api/health")).json()
    assert body == {
        "status": "ok",
        "version": __version__,
        "schema_version": load_migrations()[-1].version,
        "sandbox": False,
    }


async def test_unknown_api_route_is_json_404(client):
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_index_is_served(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "<title>White House</title>" in response.text


def test_open_context_creates_layout(data_paths):
    ctx = open_context(data_paths)
    ctx.close()
    for path in (data_paths.db, data_paths.config, data_paths.backups, data_paths.logs):
        assert path.exists(), path


def test_reopening_current_schema_takes_no_backup(data_paths):
    open_context(data_paths).close()
    open_context(data_paths).close()
    assert list_backups(data_paths.backups) == []


def test_schema_upgrade_takes_backup_first(data_paths, monkeypatch):
    open_context(data_paths).close()
    shipped = load_migrations()
    current = shipped[-1].version
    upgraded = [*shipped, Migration(current + 1, "future", "CREATE TABLE future (x);")]
    monkeypatch.setattr(db_module, "load_migrations", lambda: upgraded)

    ctx = open_context(data_paths)
    assert ctx.db.schema_version == current + 1
    ctx.close()
    names = [b.name for b in list_backups(data_paths.backups)]
    assert len(names) == 1
    assert names[0].endswith(f"pre-migrate-v{current}-to-v{current + 1}.sqlite3")


async def test_page_has_an_icon(client):
    """Regression: without an icon the browser asked for /favicon.ico and logged a 404."""
    assert 'rel="icon" href="/favicon.svg"' in (await client.get("/")).text
    icon = await client.get("/favicon.svg")
    assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/svg+xml")
