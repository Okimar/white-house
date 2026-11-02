"""Copies on Google Drive (stage 9б): sign-in with the user's own app pass, sending copies to a
visible Drive folder, the list there, bringing a copy back and restoring from it.

- The pass (client id + secret of a «Desktop app» OAuth client) and the sign-in (refresh token) are
  secrets: `secrets.json` under "gdrive", never in a copy, never to the browser.
- Machine state lives in `gdrive.json` next to the database, outside the copies, so a restore keeps
  it: auto on/off, account, folder id, since when copies go up, which local copy is which Drive
  file, an unfinished upload session (resumed after a restart), free space.
- Sign-in: Google sends the browser back to `http://127.0.0.1:<port>/?code=…&state=…`; the page
  hands that address to `finish()`. Opened from another device, the user pastes it. PKCE + state.
- Scope `drive.file`: Tavern sees only files it created. Every copy made after sign-in goes up
  (the owner chose «all copies»); nothing is deleted on Drive. A copy the user deleted on Drive
  is not sent again by itself.
- A worker thread sends the queue; a network failure is retried later with a growing pause.
- A sandbox uses its own secret key and state file: it never sees the real sign-in.
- `TAVERN_GDRIVE_URL` points every Google address at a fake (devtools/fake_gdrive.py).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from tavern.errors import AppError, Conflict, NotFound
from tavern.storage.backup import parse_archive_name
from tavern.util.fs import atomic_write_text

if TYPE_CHECKING:
    from tavern.context import AppContext

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_NAME = "Tavern — копии"
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 8 * 1024 * 1024  # a multiple of 256 KiB, as resumable uploads require
LOW_SPACE = 1024**3
LOGIN_TTL = 15 * 60
MAX_RETRY_PAUSE = 3600.0
FAKE_ENV = "TAVERN_GDRIVE_URL"


# The guide to the app pass: one place for the «Копии» tab and the assistant (tool drive_guide), so
# both say the same. Google Cloud's buttons are in English for most people: names are given as shown.
GUIDE: tuple[dict[str, str], ...] = (
    {"link": "https://console.cloud.google.com/projectcreate",
     "text": "Войдите в Google → нажмите «Create»."},
    {"link": "https://console.cloud.google.com/apis/library/drive.googleapis.com",
     "text": "Нажмите «Enable»."},
    {"link": "https://console.cloud.google.com/auth/overview",
     "text": "«Get started» → App name: Tavern, ниже ваша почта → Next → External → Next → ваша почта → "
             "Next → галочка → Create."},
    {"link": "https://console.cloud.google.com/auth/branding",
     "text": "В «Application home page» и «Application privacy policy link» вставьте https://github.com → "
             "«Add domain» → github.com → внизу «Save». Логотип не загружайте."},
    {"link": "https://console.cloud.google.com/auth/audience",
     "text": "«Publish app» → «Confirm». Не вышло? Там же в «Test users» → «Add users» → ваша почта → «Save» "
             "(тогда вход придётся повторять раз в 7 дней)."},
    {"link": "https://console.cloud.google.com/auth/clients/create",
     "text": "Application type: Desktop app → «Create». Скопируйте Client ID и Client secret в поля ниже → "
             "«Сохранить пропуск»."},
    {"link": "",
     "text": "Нажмите «Войти через Google». Видите «Google hasn't verified this app»? «Advanced» → "
             "«Go to Tavern» → разрешите доступ."},
    {"link": "",
     "text": "Новое устройство: тот же Client ID, тот же секрет, тот же аккаунт Google — и копии с Диска видны."},
)
GUIDE_PLACE = "Настройки ⚙ → «Копии» → «Google Диск»"


def guide_text() -> str:
    """The guide as the assistant gives it: numbered steps with their links."""
    lines = [f"Где: {GUIDE_PLACE}. Пропуск приложения делается один раз:"]
    for number, step in enumerate(GUIDE, 1):
        lines.append(f"{number}. {step['text']}" + (f"\n   Ссылка: {step['link']}" if step["link"] else ""))
    return "\n".join(lines)


@dataclass(frozen=True)
class Endpoints:
    auth: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token: str = "https://oauth2.googleapis.com/token"
    revoke: str = "https://oauth2.googleapis.com/revoke"
    api: str = "https://www.googleapis.com/drive/v3"
    upload: str = "https://www.googleapis.com/upload/drive/v3/files"

    @classmethod
    def at(cls, base: str) -> Endpoints:
        base = base.rstrip("/")
        return cls(auth=f"{base}/o/oauth2/v2/auth", token=f"{base}/token", revoke=f"{base}/revoke",
                   api=f"{base}/drive/v3", upload=f"{base}/upload/drive/v3/files")


def default_endpoints() -> Endpoints:
    base = os.environ.get(FAKE_ENV, "").strip()
    return Endpoints.at(base) if base else Endpoints()


class DriveError(AppError):
    """Google answered with an error or the setup is incomplete (message shown to the user)."""

    code = "gdrive_failed"


class DriveOffline(DriveError):
    """No connection to Google: the queue waits and tries again."""

    code = "gdrive_offline"


class DriveService:
    def __init__(self, ctx: AppContext, *, endpoints: Endpoints | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.ctx = ctx
        self.endpoints = endpoints or default_endpoints()
        self._transport = transport
        self._secret_key = "gdrive_sandbox" if ctx.sandbox else "gdrive"
        self._state_path = ctx.paths.root / ("gdrive-sandbox.json" if ctx.sandbox else "gdrive.json")
        self._lock = threading.RLock()  # the state file
        self._send_lock = threading.Lock()  # one upload at a time
        self._login: dict[str, Any] | None = None
        self._access = ("", 0.0)  # token, monotonic expiry
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.failures = 0
        self.last_error = ""
        self.busy = ""

    # -- state and secrets ---------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        with self._lock:
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                data = {}
            except (OSError, ValueError) as exc:
                log.warning("Ignoring unreadable %s: %s", self._state_path.name, exc)
                data = {}
            return {"auto": True, "account": "", "folder_id": "", "since": "", "uploaded": {},
                    "session": None, "free": None, "last_ok": "", **(data if isinstance(data, dict) else {})}

    def _update(self, **changes: Any) -> dict[str, Any]:
        with self._lock:
            state = {**self.state(), **changes}
            atomic_write_text(self._state_path, json.dumps(state, ensure_ascii=False, indent=1))
            return state

    def _mark(self, name: str, file_id: str) -> None:
        with self._lock:
            self._update(uploaded={**self.state()["uploaded"], name: file_id})

    def _secret(self) -> dict[str, str]:
        value = self.ctx.secrets.get(self._secret_key) or {}
        return value if isinstance(value, dict) else {}

    def _set_secret(self, **changes: str) -> None:
        value = {k: v for k, v in {**self._secret(), **changes}.items() if v}
        if value:
            self.ctx.secrets.set(self._secret_key, value)
        else:
            self.ctx.secrets.delete(self._secret_key)

    def signed_in(self) -> bool:
        return bool(self._secret().get("refresh_token"))

    def pending(self) -> list[Path]:
        """Copies waiting to go up, oldest first."""
        if not self.signed_in():
            return []
        state = self.state()
        since = datetime.fromisoformat(state["since"]) if state["since"] else None
        out = [a for a in self.ctx.backups.archives()
               if a.name not in state["uploaded"] and (since is None or a.created_at >= since)]
        return [a.path for a in reversed(out)]

    def public(self) -> dict[str, Any]:
        """What the page sees. Never the secret or the sign-in itself."""
        secret, state = self._secret(), self.state()
        signed = bool(secret.get("refresh_token"))
        queue = len(self.pending()) if signed else 0
        free = state["free"]
        problem = ""
        if signed and self.last_error:
            problem = self.last_error.rstrip(".") + "." + (f" Ждут отправки: {queue}." if queue else "")
        elif signed and free is not None and free < LOW_SPACE:
            problem = f"На Google Диске осталось мало места: {free / 1024**2:.0f} МБ."
        return {
            "client_id": secret.get("client_id", ""),
            "has_secret": bool(secret.get("client_secret")),
            "signed_in": signed,
            "account": state["account"] if signed else "",
            "auto": state["auto"],
            "free": free if signed else None,
            "queue": queue,
            "busy": self.busy,
            "last_ok": state["last_ok"] if signed else "",
            "last_error": self.last_error if signed else "",
            "problem": problem,
            "uploaded": sorted(n for n, i in state["uploaded"].items() if i) if signed else [],
        }

    def save(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise DriveError("Google Диск: нужен объект")
        unknown = set(raw) - {"client_id", "client_secret", "auto"}
        if unknown:
            raise DriveError(f"Нет такой настройки Диска: {sorted(unknown)[0]!r}")
        if "auto" in raw:
            if not isinstance(raw["auto"], bool):
                raise DriveError("Отправлять сами: да или нет")
            self._update(auto=raw["auto"])
        if "client_id" in raw or "client_secret" in raw:
            client_id = raw.get("client_id", self._secret().get("client_id", ""))
            client_secret = raw.get("client_secret") or ""
            if not isinstance(client_id, str) or not isinstance(client_secret, str):
                raise DriveError("Пропуск приложения: строки")
            client_id, client_secret = client_id.strip(), client_secret.strip()
            if client_id and not client_id.endswith(".apps.googleusercontent.com"):
                raise DriveError("Идентификатор клиента кончается на .apps.googleusercontent.com — "
                                 "скопируйте его из Google Cloud целиком")
            if client_id != self._secret().get("client_id", ""):
                self.sign_out()  # a sign-in belongs to the pass it was made with
            self._set_secret(client_id=client_id, client_secret=client_secret or self._secret().get("client_secret", ""))
        self.ctx.notices.emit("backups")
        self.wake()
        return self.public()

    # -- HTTP ----------------------------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=httpx.Timeout(60.0, connect=15.0))

    def _send(self, client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise DriveOffline(f"Нет связи с Google ({exc.__class__.__name__}).") from exc

    def _token(self, client: httpx.Client, *, fresh: bool = False) -> str:
        token, until = self._access
        if token and not fresh and time.monotonic() < until:
            return token
        secret = self._secret()
        if not secret.get("refresh_token"):
            raise DriveError("Сначала войдите в Google.")
        answer = self._send(client, "POST", self.endpoints.token, data={
            "client_id": secret.get("client_id", ""), "client_secret": secret.get("client_secret", ""),
            "refresh_token": secret["refresh_token"], "grant_type": "refresh_token",
        })
        body = _json(answer)
        if answer.status_code == 400 and body.get("error") == "invalid_grant":
            self._set_secret(refresh_token="")
            self._access = ("", 0.0)
            self.ctx.notices.emit("backups")
            raise DriveError("Вход в Google больше не действует — войдите снова. Если так бывает каждую неделю, "
                             "опубликуйте приложение в Google Cloud (шаг 4 инструкции).")
        if answer.status_code != 200 or "access_token" not in body:
            raise DriveError(f"Google не пустил: {_message(answer)}")
        self._access = (body["access_token"], time.monotonic() + float(body.get("expires_in", 3600)) - 60)
        return body["access_token"]

    def _api(self, client: httpx.Client, method: str, url: str, *, headers: dict[str, str] | None = None,
             **kwargs: Any) -> httpx.Response:
        """A Drive call with the sign-in; an expired access token is renewed once."""
        for fresh in (False, True):
            auth = {**(headers or {}), "Authorization": f"Bearer {self._token(client, fresh=fresh)}"}
            answer = self._send(client, method, url, headers=auth, **kwargs)
            if answer.status_code != 401:
                return answer
        raise DriveError(f"Google не пустил: {_message(answer)}")

    # -- sign-in -------------------------------------------------------------------------------

    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.ctx.config.server.port}/"

    def login_url(self) -> str:
        secret = self._secret()
        if not secret.get("client_id") or not secret.get("client_secret"):
            raise DriveError("Сначала вставьте пропуск приложения: идентификатор клиента и секрет.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        self._login = {"state": state, "verifier": verifier, "redirect": self.redirect_uri(),
                       "until": time.monotonic() + LOGIN_TTL}
        return self.endpoints.auth + "?" + urlencode({
            "client_id": secret["client_id"], "redirect_uri": self._login["redirect"], "response_type": "code",
            "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })

    def finish(self, address: str) -> dict[str, Any]:
        """The address Google sent the browser to after sign-in (or just its `?code=…&state=…`)."""
        text = (address or "").strip()
        query = parse_qs(urlsplit(text).query if "://" in text else text.lstrip("?"))
        q = {k: v[0] for k, v in query.items()}
        login = self._login
        if q.get("error"):
            raise DriveError("Вход отменён." if q["error"] == "access_denied" else f"Google: {q['error']}")
        if not q.get("code") or not q.get("state"):
            raise DriveError("В адресе нет кода входа. Скопируйте адрес из строки браузера целиком.")
        if login is None or not secrets.compare_digest(q["state"], login["state"]) or time.monotonic() > login["until"]:
            raise DriveError("Этот вход устарел или начат не здесь. Нажмите «Войти через Google» ещё раз.")
        self._login = None
        secret = self._secret()
        with self._client() as client:
            answer = self._send(client, "POST", self.endpoints.token, data={
                "code": q["code"], "client_id": secret.get("client_id", ""),
                "client_secret": secret.get("client_secret", ""), "redirect_uri": login["redirect"],
                "grant_type": "authorization_code", "code_verifier": login["verifier"],
            })
            body = _json(answer)
            if answer.status_code != 200 or not body.get("refresh_token"):
                raise DriveError(f"Google не пустил: {_message(answer)}")
            self._set_secret(refresh_token=body["refresh_token"])
            self._access = (body["access_token"], time.monotonic() + float(body.get("expires_in", 3600)) - 60)
            self._update(since=datetime.now(timezone.utc).isoformat(), folder_id="", session=None)
            self._about(client)
            self._folder(client)
        self.last_error = ""
        self.failures = 0
        log.info("Signed in to Google Drive")
        self.ctx.notices.emit("backups")
        self.wake()
        return self.public()

    def sign_out(self) -> dict[str, Any]:
        token = self._secret().get("refresh_token")
        if token:
            try:
                with self._client() as client:
                    client.post(self.endpoints.revoke, params={"token": token})
            except httpx.HTTPError as exc:
                log.info("Revoking the Google sign-in failed (signed out here anyway): %s", exc)
        self._set_secret(refresh_token="")
        self._access = ("", 0.0)
        self._update(account="", folder_id="", since="", uploaded={}, session=None, free=None, last_ok="")
        self.last_error = ""
        self.failures = 0
        self.ctx.notices.emit("backups")
        return self.public()

    # -- Drive ---------------------------------------------------------------------------------

    def _about(self, client: httpx.Client) -> None:
        answer = self._api(client, "GET", f"{self.endpoints.api}/about",
                           params={"fields": "user(emailAddress,displayName),storageQuota(limit,usage)"})
        if answer.status_code != 200:
            return
        body = _json(answer)
        quota = body.get("storageQuota") or {}
        free = int(quota["limit"]) - int(quota.get("usage", 0)) if quota.get("limit") else None
        self._update(account=(body.get("user") or {}).get("emailAddress", ""), free=free)

    def _folder(self, client: httpx.Client) -> str:
        folder = self.state()["folder_id"]
        if folder:
            return folder
        name = FOLDER_NAME.replace("\\", "\\\\").replace("'", "\\'")
        answer = self._api(client, "GET", f"{self.endpoints.api}/files", params={
            "q": f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false", "fields": "files(id)"})
        _check(answer)
        found = _json(answer).get("files") or []
        if found:
            folder = found[0]["id"]
        else:
            answer = self._api(client, "POST", f"{self.endpoints.api}/files", params={"fields": "id"},
                               json={"name": FOLDER_NAME, "mimeType": FOLDER_MIME})
            _check(answer)
            folder = _json(answer)["id"]
        self._update(folder_id=folder)
        return folder

    def files(self) -> list[dict[str, Any]]:
        """Copies in the Drive folder, newest first. Also notices copies deleted on Drive."""
        with self._client() as client:
            folder = self._folder(client)
            items: list[dict[str, Any]] = []
            page = ""
            while True:
                params = {"q": f"'{folder}' in parents and trashed = false", "pageSize": "1000",
                          "fields": "nextPageToken,files(id,name,size,createdTime)", "orderBy": "createdTime desc"}
                if page:
                    params["pageToken"] = page
                answer = self._api(client, "GET", f"{self.endpoints.api}/files", params=params)
                _check(answer)
                body = _json(answer)
                items += body.get("files") or []
                page = body.get("nextPageToken") or ""
                if not page:
                    break
            self._about(client)
        with self._lock:
            state = self.state()
            there = {f["id"] for f in items}
            # A copy the user deleted on Drive keeps its entry with no id: not sent again by itself.
            uploaded = {n: (i if i in there else "") for n, i in state["uploaded"].items()}
            if uploaded != state["uploaded"]:
                self._update(uploaded=uploaded)
        local = {a.name for a in self.ctx.backups.archives()}
        here = {i: n for n, i in uploaded.items() if i and n in local}
        out = []
        for f in items:
            parsed = parse_archive_name(f["name"])
            if parsed is None:
                continue  # not a Tavern copy
            created, kind, reason = parsed
            out.append({"id": f["id"], "name": f["name"], "size": int(f.get("size") or 0),
                        "created_at": created.isoformat(), "kind": kind, "reason": reason,
                        "here": here.get(f["id"], f["name"] if f["name"] in local else "")})
        return out

    def upload(self, name: str) -> dict[str, Any]:
        """Send one copy by hand («На Диск»)."""
        path = self.ctx.backups.find(name)
        if path.suffix != ".zip":
            raise DriveError("На Диск уходят только полные копии")
        with self._send_lock, self._client() as client:
            file_id = self.state()["uploaded"].get(name) or self._upload_file(client, path)
            self._about(client)
        self.failures = 0  # Google answers again: the waiting copies can go now
        self.last_error = ""
        self.ctx.notices.emit("backups")
        self.wake()
        return {"name": name, "id": file_id}

    def _upload_file(self, client: httpx.Client, path: Path) -> str:
        size = path.stat().st_size
        session = self.state()["session"]
        url, offset = "", 0
        if session and session.get("name") == path.name and session.get("size") == size:
            url = session["url"]
            done, offset = self._probe(client, url, size)
            if done:
                return self._uploaded(path.name, done)
            if offset < 0:
                url = ""  # the session ran out (they live about a week): start again
        if not url:
            url, offset = self._open_session(client, path.name, size), 0
        self.busy = path.name
        self.ctx.notices.emit("backups")
        try:
            with path.open("rb") as fh:
                while True:
                    fh.seek(offset)
                    chunk = fh.read(CHUNK)
                    end = offset + len(chunk) - 1
                    span = f"bytes {offset}-{end}/{size}" if chunk else f"bytes */{size}"
                    answer = self._api(client, "PUT", url, content=chunk, headers={"Content-Range": span})
                    if answer.status_code in (200, 201):
                        return self._uploaded(path.name, _json(answer)["id"])
                    if answer.status_code == 308:
                        offset = _range_end(answer)
                        continue
                    if answer.status_code in (404, 410):
                        self._update(session=None)
                    raise DriveError(f"Копия {path.name} не ушла: {_message(answer)}")
        finally:
            self.busy = ""

    def _open_session(self, client: httpx.Client, name: str, size: int) -> str:
        for attempt in range(2):
            folder = self._folder(client)
            answer = self._api(client, "POST", self.endpoints.upload, params={"uploadType": "resumable"},
                               json={"name": name, "parents": [folder], "mimeType": "application/zip"},
                               headers={"X-Upload-Content-Type": "application/zip",
                                        "X-Upload-Content-Length": str(size)})
            if answer.status_code == 404 and attempt == 0:
                self._update(folder_id="")  # the user deleted the folder: make it again
                continue
            _check(answer)
            url = answer.headers.get("location", "")
            if not url:
                raise DriveError("Google не дал адрес для отправки")
            self._update(session={"name": name, "size": size, "url": url})
            return url
        raise DriveError("Не удалось создать папку на Диске")

    def _probe(self, client: httpx.Client, url: str, size: int) -> tuple[str, int]:
        """(file id if already complete, bytes Google has; -1 when the session is gone)."""
        answer = self._api(client, "PUT", url, content=b"", headers={"Content-Range": f"bytes */{size}"})
        if answer.status_code in (200, 201):
            return _json(answer).get("id", ""), size
        if answer.status_code == 308:
            return "", _range_end(answer)
        self._update(session=None)
        return "", -1

    def _uploaded(self, name: str, file_id: str) -> str:
        self._update(session=None, last_ok=datetime.now(timezone.utc).isoformat())
        self._mark(name, file_id)
        log.info("Copy %s is on Google Drive", name)
        return file_id

    def sync(self) -> list[str]:
        """Send every waiting copy. A failure stops the round; the next one resumes."""
        sent: list[str] = []
        if not self.signed_in():
            return sent
        with self._send_lock:
            try:
                with self._client() as client:
                    for path in self.pending():
                        if not path.is_file():
                            continue  # pruned meanwhile
                        self._upload_file(client, path)
                        sent.append(path.name)
                    if sent:
                        self._about(client)
            except (DriveError, OSError) as exc:
                self.failures += 1
                text = exc.message if isinstance(exc, DriveError) else f"не прочитать копию ({exc})"
                self.last_error = text if text.startswith("Google") else f"Google Диск: {text}"
                log.warning("Google Drive upload failed (%d in a row): %s", self.failures, text)
                self.ctx.notices.emit("backups")
                return sent
        if self.failures or self.last_error or sent:
            self.failures = 0
            self.last_error = ""
            self.ctx.notices.emit("backups")
        return sent

    def download(self, file_id: str) -> str:
        """Bring a Drive copy into the local list. Returns its local name."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", file_id):
            raise NotFound("Нет такой копии на Диске")
        known = {i: n for n, i in self.state()["uploaded"].items() if i}
        if known.get(file_id) in {a.name for a in self.ctx.backups.archives()}:
            return known[file_id]
        work = self.ctx.backups.tmp_dir()
        try:
            target = work / "drive.zip"
            with self._client() as client, target.open("wb") as fh:
                headers = {"Authorization": f"Bearer {self._token(client)}"}
                try:
                    with client.stream("GET", f"{self.endpoints.api}/files/{file_id}",
                                       params={"alt": "media"}, headers=headers) as answer:
                        if answer.status_code == 404:
                            raise NotFound("Нет такой копии на Диске")
                        if answer.status_code != 200:
                            answer.read()
                            raise DriveError(f"Копия не скачалась: {_message(answer)}")
                        for block in answer.iter_bytes(1 << 20):
                            fh.write(block)
                except httpx.TransportError as exc:
                    raise DriveOffline(f"Нет связи с Google ({exc.__class__.__name__}).") from exc
            with self._send_lock:  # marked before the sender can see it as a new copy to send
                name = self.ctx.backups.keep(target)
                self._mark(name, file_id)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return name

    def restore(self, file_id: str) -> dict[str, Any]:
        if self.ctx.runs.running():
            raise Conflict("Сейчас пишется ответ. Остановите его или дождитесь конца, потом восстанавливайте.",
                           code="busy")
        return self.ctx.backups.restore(self.download(file_id))

    # -- the worker ----------------------------------------------------------------------------

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tavern-gdrive", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def retry_pause(self) -> float | None:
        if not self.failures:
            return None
        return min(60.0 * 2 ** (self.failures - 1), MAX_RETRY_PAUSE)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.signed_in() and self.state()["auto"]:
                    self.sync()
            except Exception:  # noqa: BLE001 - the worker must outlive one bad round
                log.exception("Google Drive worker round failed")
            self._wake.wait(self.retry_pause())
            self._wake.clear()


def _json(answer: httpx.Response) -> dict[str, Any]:
    try:
        body = answer.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _message(answer: httpx.Response) -> str:
    body = _json(answer)
    error = body.get("error")
    if isinstance(error, dict):
        return f"{error.get('message') or error.get('status') or answer.status_code}"
    if isinstance(error, str):
        return f"{error}: {body.get('error_description', '')}".rstrip(": ")
    if answer.status_code >= 500:
        return f"Google сейчас не отвечает (ответ {answer.status_code}), копии уйдут позже"
    return f"ответ {answer.status_code}"


def _check(answer: httpx.Response) -> None:
    if answer.status_code >= 400:
        raise DriveError(f"Google Диск: {_message(answer)}")


def _range_end(answer: httpx.Response) -> int:
    """Bytes Google has after a 308: the end of `Range: bytes=0-N` plus one, 0 without it."""
    match = re.fullmatch(r"bytes=0-(\d+)", answer.headers.get("range", ""))
    return int(match.group(1)) + 1 if match else 0
