"""A fake Google (sign-in + Drive) for tests and manual checks of stage 9б copies on Google Drive.

Only what Tavern uses: the sign-in page (answers at once by redirecting back with a code), the token
endpoint (code with PKCE, refresh), revoke, `about`, folders and files in one folder, resumable
uploads (308 with Range, status probe) and downloads. Knobs for tests: `offline` (every request
fails like a dropped network), `break_puts` (that many upload chunks fail before reaching it),
`quota_limit`, `revoke_refresh` (the next refresh answers invalid_grant).

Tests pass `FakeGoogle().transport()` to the service. A manual run serves it over HTTP:

    python -m tavern.devtools.fake_gdrive --port 5002
    TAVERN_GDRIVE_URL=http://127.0.0.1:5002 python -m tavern --sandbox
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import itertools
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

FOLDER_MIME = "application/vnd.google-apps.folder"


class FakeGoogle:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.codes: dict[str, dict[str, str]] = {}  # code -> {challenge, client_id}
        self.access: set[str] = set()
        self.refresh: set[str] = set()
        self.offline = False
        self.break_puts = 0
        self.revoke_refresh = False
        self.quota_limit = 15 * 1024**3
        self.email = "owner@example.com"
        self.put_bytes = 0  # upload bytes that reached the server (resume tests)
        self._ids = itertools.count(1)

    # -- for tests -----------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def approve(self, auth_url: str) -> str:
        """What the browser does: open the sign-in page, follow the redirect. Returns the address."""
        return self.handle(httpx.Request("GET", auth_url)).headers["location"]

    def named(self, name: str) -> dict[str, Any] | None:
        return next((f for f in self.files.values() if f["name"] == name), None)

    # -- the fake ------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("fake network is down", request=request)
        path = request.url.path
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        if path.endswith("/o/oauth2/v2/auth"):
            return self._authorize(q)
        if path.endswith("/token"):
            return self._token({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        if path.endswith("/revoke"):
            self.refresh.discard(q.get("token", ""))
            return httpx.Response(200)
        if request.headers.get("authorization", "")[7:] not in self.access:
            return _error(401, "Invalid Credentials")
        if path.endswith("/upload/drive/v3/files"):
            if request.method == "POST":
                return self._start_upload(request)
            return self._put(request, q.get("upload_id", ""))
        if path.endswith("/drive/v3/about"):
            usage = sum(len(f["data"]) for f in self.files.values())
            return httpx.Response(200, json={"user": {"emailAddress": self.email, "displayName": "Owner"},
                                             "storageQuota": {"limit": str(self.quota_limit), "usage": str(usage)}})
        if path.endswith("/drive/v3/files"):
            if request.method == "POST":
                return httpx.Response(200, json=self._public(self._add(json.loads(request.content))))
            return self._list(q)
        match = re.search(r"/drive/v3/files/([^/]+)$", path)
        if match:
            item = self.files.get(match.group(1))
            if item is None:
                return _error(404, "File not found")
            if request.method == "DELETE":
                del self.files[item["id"]]
                return httpx.Response(204)
            if q.get("alt") == "media":
                return httpx.Response(200, content=item["data"])
            return httpx.Response(200, json=self._public(item))
        return _error(404, f"fake google: no route {request.method} {path}")

    def _authorize(self, q: dict[str, str]) -> httpx.Response:
        code = secrets.token_urlsafe(8)
        self.codes[code] = {"challenge": q["code_challenge"], "client_id": q["client_id"]}
        back = q["redirect_uri"] + "?" + urlencode({"state": q["state"], "code": code, "scope": q["scope"]})
        return httpx.Response(302, headers={"location": back})

    def _token(self, form: dict[str, str]) -> httpx.Response:
        if form.get("grant_type") == "authorization_code":
            known = self.codes.pop(form.get("code", ""), None)
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if known is None or known["challenge"] != challenge or known["client_id"] != form.get("client_id"):
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code"})
            refresh = "r-" + secrets.token_urlsafe(8)
            self.refresh.add(refresh)
            return self._grant(refresh)
        if form.get("grant_type") == "refresh_token":
            if self.revoke_refresh:
                self.refresh.clear()
                self.revoke_refresh = False
            if form.get("refresh_token") not in self.refresh:
                return httpx.Response(400, json={"error": "invalid_grant",
                                                 "error_description": "Token has been expired or revoked."})
            return self._grant(None)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _grant(self, refresh: str | None) -> httpx.Response:
        access = "a-" + secrets.token_urlsafe(8)
        self.access.add(access)
        body = {"access_token": access, "expires_in": 3599, "token_type": "Bearer"}
        if refresh:
            body["refresh_token"] = refresh
        return httpx.Response(200, json=body)

    def _add(self, meta: dict[str, Any], data: bytes = b"") -> dict[str, Any]:
        file_id = f"f{next(self._ids)}"
        item = {"id": file_id, "name": meta["name"], "mimeType": meta.get("mimeType", "application/zip"),
                "parents": meta.get("parents") or [], "data": data,
                "createdTime": datetime.now(timezone.utc).isoformat()}
        self.files[file_id] = item
        return item

    def _public(self, item: dict[str, Any]) -> dict[str, Any]:
        out = {k: item[k] for k in ("id", "name", "mimeType", "createdTime")}
        if item["mimeType"] != FOLDER_MIME:
            out["size"] = str(len(item["data"]))
        return out

    def _list(self, q: dict[str, str]) -> httpx.Response:
        query = q.get("q", "")
        items = list(self.files.values())
        name = re.search(r"name = '((?:[^'\\]|\\.)*)'", query)
        if name:
            items = [f for f in items if f["name"] == name.group(1).replace("\\'", "'")]
        if f"mimeType = '{FOLDER_MIME}'" in query:
            items = [f for f in items if f["mimeType"] == FOLDER_MIME]
        parent = re.search(r"'([^']+)' in parents", query)
        if parent:
            items = [f for f in items if parent.group(1) in f["parents"]]
        items.sort(key=lambda f: f["createdTime"], reverse=True)
        return httpx.Response(200, json={"files": [self._public(f) for f in items]})

    def _start_upload(self, request: httpx.Request) -> httpx.Response:
        meta = json.loads(request.content)
        if any(p not in self.files for p in meta.get("parents", [])):
            return _error(404, "File not found: parent")
        sid = secrets.token_urlsafe(6)
        self.sessions[sid] = {"meta": meta, "data": bytearray(),
                              "total": int(request.headers["x-upload-content-length"])}
        base = f"{request.url.scheme}://{request.url.netloc.decode()}"
        return httpx.Response(200, headers={"location": f"{base}/upload/drive/v3/files?uploadType=resumable&upload_id={sid}"})

    def _put(self, request: httpx.Request, sid: str) -> httpx.Response:
        session = self.sessions.get(sid)
        if session is None:
            return _error(404, "No such upload session")
        content_range = request.headers.get("content-range", "")
        if content_range.startswith("bytes */"):
            return self._upload_state(sid, session)
        if self.break_puts > 0:
            self.break_puts -= 1
            raise httpx.WriteError("fake network dropped the chunk", request=request)
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if not match or int(match.group(1)) != len(session["data"]):
            return _error(400, f"bad Content-Range {content_range!r}")
        session["data"] += request.content
        self.put_bytes += len(request.content)
        return self._upload_state(sid, session)

    def _upload_state(self, sid: str, session: dict[str, Any]) -> httpx.Response:
        if len(session["data"]) < session["total"]:
            headers = {"range": f"bytes=0-{len(session['data']) - 1}"} if session["data"] else {}
            return httpx.Response(308, headers=headers)
        del self.sessions[sid]
        return httpx.Response(200, json=self._public(self._add(session["meta"], bytes(session["data"]))))


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": message}})


def make_app(fake: FakeGoogle):  # pragma: no cover - manual runs only
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    async def any_route(request: Request) -> Response:
        inner = httpx.Request(request.method, str(request.url), headers=request.headers.raw,
                              content=await request.body())
        try:
            answer = fake.handle(inner)
        except httpx.TransportError:
            return Response(status_code=503)
        headers = {k: v for k, v in answer.headers.items() if k.lower() not in ("content-length", "content-encoding")}
        return Response(answer.content, status_code=answer.status_code, headers=headers)

    async def knobs(request: Request) -> Response:
        """POST /fake/knobs {"break_puts": 2, "quota_limit": 100} for manual checks."""
        for key, value in (await request.json()).items():
            setattr(fake, key, value)
        return Response(status_code=204)

    return Starlette(routes=[Route("/fake/knobs", knobs, methods=["POST"]),
                             Route("/{path:path}", any_route, methods=["GET", "POST", "PUT", "DELETE"])])


def main() -> None:  # pragma: no cover
    import uvicorn

    parser = argparse.ArgumentParser(description="Fake Google sign-in + Drive for Tavern")
    parser.add_argument("--port", type=int, default=5002)
    args = parser.parse_args()
    uvicorn.run(make_app(FakeGoogle()), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
