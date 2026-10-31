"""Spending: the summary the UI shows, the settings (where shown, currency, limits, own prices), the report."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import ctx_of, json_body
from tavern.services import spending


async def get_summary(request: Request) -> JSONResponse:
    """?chat=<id>: today, this month, that chat, whether a limit is reached, and the settings."""
    return JSONResponse(spending.summary(ctx_of(request), request.query_params.get("chat", "")))


async def get_settings(request: Request) -> JSONResponse:
    return JSONResponse(spending.get_settings(ctx_of(request)))


async def put_settings(request: Request) -> JSONResponse:
    return JSONResponse(spending.save_settings(ctx_of(request), await json_body(request)))


async def get_report(request: Request) -> JSONResponse:
    return JSONResponse(spending.report(ctx_of(request)))


async def get_message(request: Request) -> JSONResponse:
    """Everything spent on one message: all its versions, fallback attempts and extra requests."""
    return JSONResponse(ctx_of(request).spending.message(request.path_params["id"]))


routes = [
    Route("/spending", get_summary, methods=["GET"]),
    Route("/spending/settings", get_settings, methods=["GET"]),
    Route("/spending/settings", put_settings, methods=["PUT"]),
    Route("/spending/report", get_report, methods=["GET"]),
    Route("/messages/{id}/spending", get_message, methods=["GET"]),
]
