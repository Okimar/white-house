"""Updating from the page (ADR 0016): every case on throwaway git repos, never on this repository."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tavern.services import look, updates
from tests.conftest import REMOTE_CLIENT

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is needed")


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout.strip()


def commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                       ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TAVERN_SUPERVISED", raising=False)
    updates.install_exit_hook(None)


@pytest.fixture(scope="module")
def origin_template(tmp_path_factory):
    """The server's repository, built once; each test gets a copy (init and commits cost most of a test)."""
    origin = tmp_path_factory.mktemp("origin-template") / "origin"
    origin.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        for key, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                           ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
            mp.setenv(key, value)
        git(origin, "init", "-q", "-b", "main")
        commit(origin, "requirements.txt", "starlette==1\n", "first")
        commit(origin, "app.txt", "v1", "second")
    return origin


@pytest.fixture
def repos(tmp_path, monkeypatch, origin_template):
    """origin (the server) and install (the clone the app runs from), the code check and pip replaced."""
    origin = tmp_path / "origin"
    shutil.copytree(origin_template, origin)
    install = tmp_path / "install"
    git(tmp_path, "clone", "-q", str(origin), str(install))
    calls = {"pip": 0}
    monkeypatch.setattr(updates, "_imports", lambda root: True)
    monkeypatch.setattr(updates, "_install_requirements", lambda root: calls.__setitem__("pip", calls["pip"] + 1))
    monkeypatch.setattr(updates, "REPO_ROOT", install)
    return origin, install, calls


async def post(client, path):
    return await client.post(f"/api/updates/{path}")


async def test_up_to_date(client, repos):
    found = (await post(client, "check")).json()
    assert found["state"] == "up_to_date" and found["changes"] == [] and not found["can_update"]
    assert found["current"]["subject"] == "second"
    assert (await post(client, "apply")).status_code == 409


async def test_new_commits_are_listed_in_plain_subjects(client, repos):
    origin, install, _ = repos
    commit(origin, "a.txt", "1", "Новая кнопка")
    commit(origin, "b.txt", "2", "Починили шрифт")
    found = (await post(client, "check")).json()
    assert found["state"] == "available" and found["can_update"] and found["behind"] == 2
    assert [c["subject"] for c in found["changes"]] == ["Починили шрифт", "Новая кнопка"]
    assert git(install, "log", "-1", "--format=%s") == "second"  # looking changes nothing


async def test_apply_fast_forwards_after_a_copy_and_without_pip_when_requirements_are_the_same(client, ctx, repos):
    origin, install, calls = repos
    commit(origin, "a.txt", "1", "Новая кнопка")
    before = len(ctx.backups.list())
    done = await post(client, "apply")
    assert done.status_code == 200
    body = done.json()
    assert body["updated"] and body["to"]["subject"] == "Новая кнопка" and body["restart"] == "manual"
    assert git(install, "rev-parse", "HEAD") == git(origin, "rev-parse", "HEAD")
    assert (install / "a.txt").exists()
    assert len(ctx.backups.list()) == before + 1 and body["copy"]
    assert calls["pip"] == 0
    assert (await post(client, "check")).json()["state"] == "up_to_date"


async def test_requirements_are_installed_when_they_changed(client, repos):
    origin, _, calls = repos
    commit(origin, "requirements.txt", "starlette==2\n", "Новая библиотека")
    assert (await post(client, "apply")).status_code == 200
    assert calls["pip"] == 1


async def test_a_supervised_run_says_it_restarts_itself(client, repos, monkeypatch):
    origin, _, _ = repos
    monkeypatch.setenv("TAVERN_SUPERVISED", "1")
    commit(origin, "a.txt", "1", "x")
    assert (await post(client, "apply")).json()["restart"] == "auto"


async def _noop():
    return None


async def test_the_process_is_asked_to_leave_after_the_answer(client, repos, monkeypatch):
    import tavern.api.updates as api

    origin, _, _ = repos
    commit(origin, "a.txt", "1", "x")
    stopped = []
    updates.install_exit_hook(lambda: stopped.append(1))
    monkeypatch.setattr(api.asyncio, "sleep", lambda _s: _noop())
    assert (await post(client, "apply")).status_code == 200
    assert stopped == [1] and updates.exit_requested()


async def test_own_edits_in_tracked_files_stop_the_update(client, ctx, repos):
    origin, install, _ = repos
    commit(origin, "a.txt", "1", "x")
    (install / "app.txt").write_text("my edit", encoding="utf-8")
    found = (await post(client, "check")).json()
    assert found["state"] == "blocked" and found["code"] == "dirty" and not found["can_update"]
    before, copies = git(install, "rev-parse", "HEAD"), len(ctx.backups.list())
    refused = await post(client, "apply")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "dirty"
    assert git(install, "rev-parse", "HEAD") == before and (install / "app.txt").read_text() == "my edit"
    assert len(ctx.backups.list()) == copies  # nothing was done, not even a copy


async def test_untracked_files_do_not_block(client, repos):
    origin, install, _ = repos
    commit(origin, "a.txt", "1", "x")
    (install / "notes.tmp").write_text("mine", encoding="utf-8")
    assert (await post(client, "apply")).status_code == 200
    assert (install / "notes.tmp").exists()


async def test_diverged_history_is_refused(client, repos):
    origin, install, _ = repos
    commit(origin, "a.txt", "1", "server side")
    commit(install, "mine.txt", "m", "local change")
    found = (await post(client, "check")).json()
    assert found["state"] == "blocked" and found["code"] == "diverged"
    refused = await post(client, "apply")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "diverged"
    assert git(install, "log", "-1", "--format=%s") == "local change"


async def test_only_local_commits_count_as_up_to_date(client, repos):
    _, install, _ = repos
    commit(install, "mine.txt", "m", "local change")
    found = (await post(client, "check")).json()
    assert found["state"] == "up_to_date" and found["ahead"] == 1


async def test_not_a_git_install(client, tmp_path, monkeypatch):
    plain = tmp_path / "unzipped"
    plain.mkdir()
    monkeypatch.setattr(updates, "REPO_ROOT", plain)
    found = (await post(client, "check")).json()
    assert found["state"] == "unsupported" and found["code"] == "not_git" and "вручную" in found["how"]
    refused = await post(client, "apply")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_git"
    assert (await client.get("/api/updates")).json()["git"] is False


async def test_no_network_is_said_plainly_and_shows_no_address(client, repos):
    _, install, _ = repos
    git(install, "remote", "set-url", "origin", "https://user:secret-token@127.0.0.1:9/none.git")
    found = (await post(client, "check")).json()
    assert found["state"] == "error" and found["code"] == "no_network"
    assert "secret-token" not in str(found) and "127.0.0.1" not in str(found)
    assert (await post(client, "apply")).status_code == 409


async def test_a_running_reply_or_assistant_blocks_it(client, ctx, repos, monkeypatch):
    origin, install, _ = repos
    commit(origin, "a.txt", "1", "x")
    monkeypatch.setattr(ctx.runs, "running", lambda: ["assistant:abc"])
    refused = await post(client, "apply")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "busy"
    assert git(install, "log", "-1", "--format=%s") == "second"


async def test_a_new_version_that_does_not_load_is_put_back(client, repos, monkeypatch):
    origin, install, _ = repos
    commit(origin, "a.txt", "1", "broken")
    monkeypatch.setattr(updates, "_imports", lambda root: False)
    refused = await post(client, "apply")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "update_failed"
    assert "прежней" in refused.json()["error"]["message"]
    assert git(install, "log", "-1", "--format=%s") == "second" and not (install / "a.txt").exists()


async def test_a_failed_pip_puts_the_old_version_back(client, repos, monkeypatch):
    origin, install, _ = repos
    commit(origin, "requirements.txt", "starlette==2\n", "lib")

    def boom(root):
        raise RuntimeError("Не удалось установить нужные библиотеки")

    monkeypatch.setattr(updates, "_install_requirements", boom)
    refused = await post(client, "apply")
    assert refused.status_code == 409
    assert (install / "requirements.txt").read_text() == "starlette==1\n"


async def test_no_copy_no_update(client, ctx, repos, monkeypatch):
    origin, install, _ = repos
    commit(origin, "a.txt", "1", "x")

    def fail(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ctx.backups, "create", fail)
    refused = await post(client, "apply")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "no_copy"
    assert git(install, "log", "-1", "--format=%s") == "second"


async def test_remote_clients_need_the_normal_access(make_client):
    http = make_client(client=REMOTE_CLIENT)
    for method, path in (("GET", "/api/updates"), ("POST", "/api/updates/check"), ("POST", "/api/updates/apply")):
        assert (await http.request(method, path)).status_code == 403


def test_the_assistant_cannot_reach_it():
    from tavern.errors import TavernError
    from tavern.services import assistant_app

    for method in ("GET", "POST"):
        with pytest.raises(TavernError):
            assistant_app._check(method, "/updates/apply", {})
    assert not any(path.startswith("/updates") for _m, path, _d in assistant_app._routes())


def test_git_messages_lose_credentials():
    assert updates.scrub("fatal: unable to access 'https://me:tok3n@github.com/x/y.git/'") == \
        "fatal: unable to access 'https://github.com/x/y.git/'"


def test_the_custom_look_is_parked_by_the_first_start_after_an_update(ctx):
    """Updating changes web/, so the stamp differs at the next start and the look-parking of ADR 0015 fires."""
    mine = ctx.themes.create("Mine")["id"]
    ctx.settings.set("theme", mine)
    ctx.settings.set(look.KEY_STAMP, look.interface_stamp())
    assert look.check_upgrade(ctx) is None
    done = look.check_upgrade(ctx, "stamp-of-the-new-web-folder")
    assert done and ctx.settings.get("theme") is None and look.parked(ctx)["theme"] == mine


async def test_a_mode_only_difference_does_not_block_but_a_content_edit_does(client, repos):
    # Old installs: install.sh ran chmod +x on a file stored as 100644. (On Windows chmod shows nothing, but the
    # check below still holds: the setting is switched off and the update goes through.)
    origin, install, _ = repos
    commit(origin, "start.sh", "echo 1\n", "script")
    git(install, "pull", "-q")
    git(install, "config", "core.fileMode", "true")
    (install / "start.sh").chmod(0o755)
    commit(origin, "start.sh", "echo 2\n", "script changed")
    found = (await post(client, "check")).json()
    assert found["can_update"], found
    assert git(install, "config", "core.fileMode") == "false"
    assert (await post(client, "apply")).json()["updated"] is True
    assert (install / "start.sh").read_text() == "echo 2\n"
    commit(origin, "a.txt", "1", "more")
    (install / "start.sh").write_text("echo mine\n", encoding="utf-8")
    assert (await post(client, "check")).json()["code"] == "dirty"
