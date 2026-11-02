"""Stage 9б: copies on Google Drive against a fake Google - sign-in, sending every new copy, the
offline queue, resuming a broken upload, restoring on a new device, a sign-in that stopped working,
and what never leaves the machine (the pass, the sign-in, a sandbox's real account)."""

from __future__ import annotations

import os

import httpx
import pytest

from tavern.context import open_context
from tavern.devtools.fake_gdrive import FakeGoogle
from tavern.services import gdrive as gdrive_module
from tavern.services.assistant_app import _check as assistant_check
from tavern.services.assistant_tools import ToolError
from tavern.services.gdrive import DriveError, DriveService, Endpoints

CLIENT_ID = "123-abc.apps.googleusercontent.com"


def attach(ctx, fake: FakeGoogle) -> DriveService:
    ctx.gdrive = DriveService(ctx, endpoints=Endpoints.at("https://fake.google"), transport=fake.transport())
    return ctx.gdrive


def sign_in(drive: DriveService, fake: FakeGoogle) -> None:
    drive.save({"client_id": CLIENT_ID, "client_secret": "pass-secret"})
    drive.finish(fake.approve(drive.login_url()))


def new_copy(ctx, probe: int, kind: str = "hourly") -> str:
    ctx.settings.set("probe", probe)  # something changed, so a copy is made
    return ctx.backups.create(kind)["name"]


async def test_sign_in_through_the_page_and_secrets_stay_here(client, ctx):
    fake = FakeGoogle()
    attach(ctx, fake)
    bad = await client.put("/api/backups/gdrive", json={"client_id": "not-a-client"})
    assert bad.status_code == 400 and "googleusercontent" in bad.json()["error"]["message"]
    await client.put("/api/backups/gdrive", json={"client_id": CLIENT_ID, "client_secret": "pass-secret"})
    login = (await client.post("/api/backups/gdrive/login")).json()
    assert login["redirect"] == f"http://127.0.0.1:{ctx.config.server.port}/"

    # Opened from another device: the user pastes the address of the empty page Google sent them to.
    address = fake.approve(login["url"])
    stale = await client.post("/api/backups/gdrive/finish", json={"address": address.replace("state=", "state=x")})
    assert stale.status_code == 400 and "устарел" in stale.json()["error"]["message"]
    done = (await client.post("/api/backups/gdrive/finish", json={"address": address})).json()
    assert done["signed_in"] and done["account"] == "owner@example.com" and done["has_secret"]
    assert fake.named("Tavern — копии") is not None

    page = (await client.get("/api/backups/gdrive")).text + (await client.get("/api/backups/status")).text
    refresh = ctx.secrets.get("gdrive")["refresh_token"]
    assert "pass-secret" not in page and refresh not in page
    made = ctx.backups.create("manual", force=True)
    blob = (ctx.paths.backups / made["name"]).read_bytes()
    assert b"pass-secret" not in blob and refresh.encode() not in blob

    with pytest.raises(ToolError, match="Google Drive"):
        assistant_check("POST", "/backups/gdrive/login", {})


def test_every_new_copy_goes_up_and_nothing_is_deleted_there(ctx):
    fake = FakeGoogle()
    drive = attach(ctx, fake)
    before = new_copy(ctx, 0, "manual")
    sign_in(drive, fake)
    ctx.backups.save_settings({"keep": {"hourly": 1}})
    first = new_copy(ctx, 1)
    assert drive.sync() == [first]
    second = new_copy(ctx, 2)  # the local first hourly copy is pruned now
    assert drive.sync() == [second]

    assert fake.named(before) is None  # made before sign-in: not sent by itself
    assert fake.named(first) is not None and fake.named(second) is not None
    listed = {b["name"]: b["drive"] for b in ctx.backups.list()}
    assert listed == {before: False, second: True}

    drive.upload(before)  # «На Диск» by hand
    there = {f["name"]: f for f in drive.files()}
    assert set(there) == {before, first, second}
    assert there[second]["here"] == second and there[first]["here"] == ""


def test_offline_copies_wait_and_go_later(ctx):
    fake = FakeGoogle()
    drive = attach(ctx, fake)
    sign_in(drive, fake)
    fake.offline = True
    waiting = [new_copy(ctx, 1), new_copy(ctx, 2)]
    assert drive.sync() == [] and drive.sync() == []
    state = drive.public()
    assert state["problem"].startswith("Google Диск: Нет связи") and state["queue"] == 2
    assert state["problem"].endswith("Ждут отправки: 2.")
    assert drive.retry_pause() == 120.0  # the second failure in a row waits longer

    fake.offline = False
    drive.upload(waiting[0])  # «На Диск» by hand works again: the error goes away
    assert drive.public()["problem"] == "" and drive.retry_pause() is None
    assert drive.sync() == waiting[1:]


def test_a_broken_upload_resumes_where_it_stopped(ctx, monkeypatch):
    monkeypatch.setattr(gdrive_module, "CHUNK", 256 * 1024)
    fake = FakeGoogle()
    drive = attach(ctx, fake)
    sign_in(drive, fake)
    (ctx.paths.assets / "big.png").write_bytes(os.urandom(700 * 1024))  # stored as is: ~3 chunks
    name = new_copy(ctx, 1)
    size = (ctx.paths.backups / name).stat().st_size

    puts = []
    handle = fake.handle

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and "bytes */" not in request.headers.get("content-range", ""):
            puts.append(1)
            if len(puts) == 2:
                raise httpx.WriteError("dropped", request=request)
        return handle(request)

    drive._transport = httpx.MockTransport(flaky)  # noqa: SLF001
    assert drive.sync() == []
    assert drive.state()["session"]["name"] == name  # kept: survives a restart too
    restarted = attach(ctx, fake)
    assert restarted.sync() == [name]
    assert fake.put_bytes == size  # the first chunk was not sent twice
    assert fake.named(name)["data"] == (ctx.paths.backups / name).read_bytes()


async def test_restore_on_a_new_device_from_drive(client, ctx, tmp_path):
    fake = FakeGoogle()
    drive = attach(ctx, fake)
    sign_in(drive, fake)
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    made = ctx.backups.create("manual", force=True)["name"]
    drive.sync()

    other = open_context(type(ctx.paths)(tmp_path / "phone"))
    try:
        phone = attach(other, fake)
        sign_in(phone, fake)
        found = [f for f in phone.files() if f["name"] == made]
        assert found and found[0]["here"] == ""
        done = phone.restore(found[0]["id"])
        assert other.chats.get_chat(chat["id"])["id"] == chat["id"]
        assert done["restored"] not in [p.name for p in phone.pending()]  # it came from Drive
        assert [f["here"] for f in phone.files() if f["id"] == found[0]["id"]] == [done["restored"]]
    finally:
        other.close()


def test_a_sign_in_that_stopped_working_asks_to_sign_in_again(ctx):
    fake = FakeGoogle()
    drive = attach(ctx, fake)
    sign_in(drive, fake)
    drive._access = ("", 0.0)  # noqa: SLF001 - the hour-long access token ran out
    fake.revoke_refresh = True
    new_copy(ctx, 1)
    drive.sync()
    assert not drive.public()["signed_in"]
    assert ctx.secrets.get("gdrive") == {"client_id": CLIENT_ID, "client_secret": "pass-secret"}
    with pytest.raises(DriveError, match="Сначала войдите"):
        drive.files()


def test_copies_deleted_on_drive_are_not_sent_again_and_low_space_warns(ctx):
    fake = FakeGoogle()
    drive = attach(ctx, fake)
    sign_in(drive, fake)
    name = new_copy(ctx, 1)
    drive.sync()
    del fake.files[fake.named(name)["id"]]
    drive.files()
    assert drive.pending() == [] and name not in drive.public()["uploaded"]

    fake.quota_limit = 500 * 1024**2
    drive.files()
    assert "мало места" in drive.public()["problem"]


def test_a_sandbox_never_sees_the_real_sign_in(data_paths):
    real = open_context(data_paths)
    real.secrets.set("gdrive", {"client_id": CLIENT_ID, "client_secret": "s", "refresh_token": "r-real"})
    real.close()
    sandbox = open_context(data_paths, sandbox=True)
    try:
        assert not sandbox.gdrive.signed_in() and sandbox.gdrive.public()["client_id"] == ""
    finally:
        sandbox.close()


async def test_the_guide_is_one_text_for_the_tab_and_the_assistant(client, ctx):
    from tavern.services.assistant import SYSTEM
    from tavern.services.assistant_app import drive_guide
    from tavern.services.assistant_tools import Env

    steps = (await client.get("/api/backups/gdrive/guide")).json()["steps"]
    said = (await drive_guide(Env(ctx), {})).result["guide"]
    assert all(s["text"] in said and s["link"] in said for s in steps)
    assert any("github.com" in s["text"] for s in steps)  # Google wants the two links before «Publish app»
    assert "drive_guide" in SYSTEM and "word for word" in SYSTEM
