---
name: write-tests
description: How to write and run tests in Tavern (pytest, async API tests via httpx.ASGITransport, fake LLM, temp data dirs). Use when adding a feature, fixing a bug, or touching any test.
---

# Writing tests

## Run

```bash
python -m pytest                    # everything (tests/ and plugins/*/tests)
python -m pytest tests/unit         # fast, pure logic
python -m pytest -k backup -x       # by name, stop at first failure
```

Use the venv interpreter (`.venv/bin/python`, Windows `.venv\Scripts\python.exe`).

## Where tests go

| Folder | What | I/O allowed |
|---|---|---|
| `tests/unit/` | one module's logic | `tmp_path` only |
| `tests/integration/` | HTTP API, CLI, several modules together | in-process app, `tmp_path` |
| `tests/smoke/` | real server subprocess | keep to one or two tests, they are slow |
| `plugins/<id>/tests/` | a plugin's own tests | same fixtures as core |

File names: `test_<module or feature>.py`. Test names say the behaviour:
`test_restore_refuses_corrupt_snapshot`, not `test_restore_3`.

## Fixtures (tests/conftest.py)

- `data_paths`: `DataPaths` in a fresh temp dir. Real `data/` is never used.
- `ctx`: an opened `AppContext` (config, migrated db, secrets) on `data_paths`.
- `client`: `httpx.AsyncClient` talking to the app in-process as a loopback client.
- `make_client(client=..., host_header=..., **server_overrides)`: client with a different peer
  address, Host header or server config. `REMOTE_CLIENT` and `LOCAL_CLIENT` are provided.

`async def` tests run automatically under anyio (asyncio backend); no decorator needed.

## API test pattern

```python
async def test_unknown_api_route_is_json_404(client):
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
```

Check the status code and the error `code`, not the human message text, unless the message is
the behaviour under test.

## Talking to an LLM in tests

Never call real providers. Use `tavern.devtools.fake_llm.FakeLLM`:

```python
fake = FakeLLM()
fake.queue("first reply", "second reply")   # then echoes "Echo: <last user msg>"
fake.fail_next(429, "rate limited")          # next request fails
http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")
...
assert fake.requests[-1]["messages"][-1]["content"] == "hi"   # inspect what was sent
```

Pictures: `POST /v1/images/generations` on the fake returns a small PNG whose colours depend on
the prompt; requests land in `fake.image_requests`, and `fail_next` applies to them too.
Manual checks in the browser follow the same rule: point the active connection at
`python -m tavern.devtools.fake_llm`, never at the owner's real model.

## Rules

- Bug fix: write the failing test first, see it fail, then fix.
- No sleeps for synchronization in unit/integration tests; await the thing instead.
- No network, no real API keys, no writes outside `tmp_path`.
- Parametrize input tables (`@pytest.mark.parametrize`) instead of copy-pasting tests.
- A test that needs a schema change goes with its migration (see core-changes skill).
- Compatibility tests for SillyTavern formats use fixture files in `tests/compat/fixtures/`
  that you create yourself from the public spec. Never copy files out of SillyTavern's repo.
