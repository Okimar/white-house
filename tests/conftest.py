"""Shared fixtures. Every test gets its own temporary data directory; real data is never touched."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import uvicorn

from tavern.app import create_app
from tavern.context import AppContext, open_context
from tavern.devtools.fake_llm import FakeLLM
from tavern.paths import DataPaths

LOCAL_CLIENT = ("127.0.0.1", 50000)
REMOTE_CLIENT = ("192.0.2.10", 50000)  # TEST-NET-1, never a real host


# Two tiers (see pyproject.toml): `-m fast` is every test in tests/unit plus the files below (security, data
# safety, the chat path), about 30 s; `-m slow` marks what starts git, a server or a regex worker process.
FAST_INTEGRATION = {"test_access_control.py", "test_app.py", "test_chat_api.py", "test_prompt_api.py",
                    "test_backups.py", "test_themes_api.py", "test_cli.py"}
SLOW_FILES = {"test_updates.py", "test_regex.py", "test_regex_st_files.py", "test_search.py"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run every `async def` test under anyio without decorating each one; sort tests into the two tiers."""
    for item in items:
        if isinstance(item, pytest.Function) and inspect.iscoroutinefunction(item.obj):
            item.add_marker(pytest.mark.anyio)
        path = Path(str(item.fspath))
        if "smoke" in path.parts or path.name in SLOW_FILES:
            item.add_marker(pytest.mark.slow)
        elif "unit" in path.parts or path.name in FAST_INTEGRATION:
            item.add_marker(pytest.mark.fast)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_builtin_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests script the fake model's replies; a built-in skill would add the "pick a skill" request.
    Tests about skills point BUILTIN_DIR at their own folder."""
    from tavern.services import skills

    monkeypatch.setattr(skills, "BUILTIN_DIR", tmp_path / "no-builtin-skills")


@pytest.fixture
def data_paths(tmp_path: Path) -> DataPaths:
    return DataPaths(tmp_path / "data")


@pytest.fixture
def ctx(data_paths: DataPaths) -> Iterator[AppContext]:
    context = open_context(data_paths)
    yield context
    context.close()


ClientFactory = Callable[..., httpx.AsyncClient]


@pytest.fixture
async def make_client(ctx: AppContext) -> AsyncIterator[ClientFactory]:
    """Build an in-process HTTP client.

    make_client(client=REMOTE_CLIENT, host_header="192.168.1.5:8000", host="0.0.0.0")
    `client` is the peer address, `host_header` the Host the browser sends, and the remaining
    keyword arguments override fields of ServerConfig.
    """
    clients: list[httpx.AsyncClient] = []

    def factory(
        *,
        client: tuple[str, int] = LOCAL_CLIENT,
        host_header: str = "127.0.0.1:8000",
        **server: object,
    ) -> httpx.AsyncClient:
        if server:
            ctx.config = replace(ctx.config, server=replace(ctx.config.server, **server))
        transport = httpx.ASGITransport(app=create_app(ctx), client=client)
        http = httpx.AsyncClient(transport=transport, base_url=f"http://{host_header}")
        clients.append(http)
        return http

    yield factory
    for http in clients:
        await http.aclose()


@pytest.fixture
async def client(make_client: ClientFactory) -> httpx.AsyncClient:
    return make_client()


@pytest.fixture
async def live_llm(ctx: AppContext) -> AsyncIterator[FakeLLM]:
    """FakeLLM on a real localhost socket (`fake.base_url`).

    httpx.ASGITransport buffers a whole response before returning it, so tests that need
    incremental streaming (stop mid-reply, a second send during a run) must use this fixture.
    """
    fake = FakeLLM()
    server = uvicorn.Server(
        uvicorn.Config(
            fake.app, host="127.0.0.1", port=0, lifespan="off", log_config=None,
            loop="asyncio", http="h11", ws="none",
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    fake.base_url = f"http://127.0.0.1:{port}/v1"
    ctx.providers.http_client = None  # use a real httpx client
    yield fake
    server.should_exit = True
    await task


@pytest.fixture
async def fake_llm(ctx: AppContext) -> AsyncIterator[FakeLLM]:
    """A scriptable OpenAI-compatible server; connections with base_url http://llm/v1 reach it."""
    fake = FakeLLM()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")
    ctx.providers.http_client = http
    yield fake
    await http.aclose()
