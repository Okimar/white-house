"""Backups and the doctor (stage 9): list, take now, download, upload, delete, restore, settings,
status for the page's warning line; doctor findings and fixes; Google Drive (stage 9б): the app pass,
sign-in, auto sending, the list there, download, restore, «На Диск». Logic lives in services/backups.py,
services/gdrive.py and services/doctor.py; the slow parts (anything that talks to Google) run off the
event loop.
"""

from __future__ import annotations

import shutil

import anyio
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from tavern.api.util import BadRequest, attachment, ctx_of, field, json_body, save_body
from tavern.services import doctor
from tavern.services.backups import MAX_UPLOAD_BYTES
from tavern.services.gdrive import GUIDE, GUIDE_PLACE


async def list_backups(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    return JSONResponse({"backups": ctx.backups.list(), **ctx.backups.status()})


async def status(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).backups.status())


async def create(request: Request) -> JSONResponse:
    made = await anyio.to_thread.run_sync(lambda: ctx_of(request).backups.create("manual", force=True))
    return JSONResponse(made, status_code=201)


async def save_settings(request: Request) -> JSONResponse:
    saved = ctx_of(request).backups.save_settings(await json_body(request))
    await anyio.to_thread.run_sync(ctx_of(request).backups.refresh_warnings)  # the folder may be fine now
    return JSONResponse(saved)


async def download(request: Request) -> FileResponse:
    name = request.path_params["name"]
    path = ctx_of(request).backups.find(name)
    return FileResponse(path, media_type="application/zip" if path.suffix == ".zip" else "application/octet-stream",
                        headers={"Content-Disposition": attachment(name)})


async def delete(request: Request) -> Response:
    ctx_of(request).backups.delete(request.path_params["name"])
    return Response(status_code=204)


async def upload(request: Request) -> JSONResponse:
    backups = ctx_of(request).backups
    work = backups.tmp_dir()
    try:
        probe = work / "upload.zip"
        if not await save_body(request, probe, MAX_UPLOAD_BYTES):
            raise BadRequest("Пустой файл")
        made = await anyio.to_thread.run_sync(backups.upload_file, probe)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return JSONResponse(made, status_code=201)


async def restore(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    return JSONResponse(await anyio.to_thread.run_sync(ctx_of(request).backups.restore, name))


async def gdrive_status(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).gdrive.public())


async def gdrive_guide(request: Request) -> JSONResponse:
    """How to get the Google Drive app pass: steps with links (the same text the assistant gives)."""
    return JSONResponse({"place": GUIDE_PLACE, "steps": list(GUIDE)})


async def gdrive_save(request: Request) -> JSONResponse:
    body = await json_body(request)
    return JSONResponse(await anyio.to_thread.run_sync(ctx_of(request).gdrive.save, body))


async def gdrive_login(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    return JSONResponse({"url": ctx.gdrive.login_url(), "redirect": ctx.gdrive.redirect_uri()})


async def gdrive_finish(request: Request) -> JSONResponse:
    address = field(await json_body(request), "address", str)
    return JSONResponse(await anyio.to_thread.run_sync(ctx_of(request).gdrive.finish, address))


async def gdrive_logout(request: Request) -> JSONResponse:
    return JSONResponse(await anyio.to_thread.run_sync(ctx_of(request).gdrive.sign_out))


async def gdrive_files(request: Request) -> JSONResponse:
    return JSONResponse({"files": await anyio.to_thread.run_sync(ctx_of(request).gdrive.files)})


async def gdrive_download(request: Request) -> JSONResponse:
    file_id = request.path_params["file_id"]
    return JSONResponse({"name": await anyio.to_thread.run_sync(ctx_of(request).gdrive.download, file_id)})


async def gdrive_restore(request: Request) -> JSONResponse:
    file_id = request.path_params["file_id"]
    return JSONResponse(await anyio.to_thread.run_sync(ctx_of(request).gdrive.restore, file_id))


async def gdrive_upload(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    return JSONResponse(await anyio.to_thread.run_sync(ctx_of(request).gdrive.upload, name))


async def doctor_check(request: Request) -> JSONResponse:
    return JSONResponse({"findings": await anyio.to_thread.run_sync(doctor.full, ctx_of(request))})


async def doctor_fix(request: Request) -> JSONResponse:
    body = await json_body(request)
    ids = field(body, "ids", list)
    if not all(isinstance(i, str) for i in ids):
        raise BadRequest("ids: список строк")
    return JSONResponse(await doctor.fix_all(ctx_of(request), ids))


routes = [
    Route("/backups", list_backups, methods=["GET"]),
    Route("/backups", create, methods=["POST"]),
    Route("/backups/status", status, methods=["GET"]),
    Route("/backups/settings", save_settings, methods=["PUT"]),
    Route("/backups/upload", upload, methods=["POST"]),
    Route("/backups/gdrive", gdrive_status, methods=["GET"]),
    Route("/backups/gdrive", gdrive_save, methods=["PUT"]),
    Route("/backups/gdrive/guide", gdrive_guide, methods=["GET"]),
    Route("/backups/gdrive/login", gdrive_login, methods=["POST"]),
    Route("/backups/gdrive/finish", gdrive_finish, methods=["POST"]),
    Route("/backups/gdrive/logout", gdrive_logout, methods=["POST"]),
    Route("/backups/gdrive/files", gdrive_files, methods=["GET"]),
    Route("/backups/gdrive/files/{file_id}/download", gdrive_download, methods=["POST"]),
    Route("/backups/gdrive/files/{file_id}/restore", gdrive_restore, methods=["POST"]),
    Route("/backups/{name}", download, methods=["GET"]),
    Route("/backups/{name}", delete, methods=["DELETE"]),
    Route("/backups/{name}/restore", restore, methods=["POST"]),
    Route("/backups/{name}/gdrive", gdrive_upload, methods=["POST"]),
    Route("/doctor", doctor_check, methods=["GET"]),
    Route("/doctor/fix", doctor_fix, methods=["POST"]),
]
