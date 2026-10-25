"""Network access policy: see tavern/security.py docstring."""

from tests.conftest import LOCAL_CLIENT, REMOTE_CLIENT

LAN_HOST = "192.168.1.5:8000"


async def test_local_client_needs_no_token(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_localhost_name_is_accepted(make_client):
    http = make_client(host_header="localhost:8000")
    assert (await http.get("/api/health")).status_code == 200


async def test_remote_client_rejected_when_remote_disabled(make_client):
    http = make_client(client=REMOTE_CLIENT)
    response = await http.get("/api/health")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "remote_disabled"


async def test_dns_rebinding_host_rejected(make_client):
    http = make_client(host_header="evil.example:8000")
    response = await http.get("/")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_host"


async def test_remote_client_needs_token(make_client, ctx):
    http = make_client(client=REMOTE_CLIENT, host_header=LAN_HOST, host="0.0.0.0")
    assert (await http.get("/")).status_code == 401
    assert (await http.get("/api/health")).status_code == 200  # public for monitoring

    token = ctx.secrets.access_token()
    ok = await http.get("/", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    bad = await http.get("/", headers={"Authorization": "Bearer wrong"})
    assert bad.status_code == 401


async def test_local_client_on_lan_bind_needs_no_token(make_client):
    http = make_client(client=LOCAL_CLIENT, host_header="127.0.0.1:8000", host="0.0.0.0")
    assert (await http.get("/")).status_code == 200


async def test_remote_access_mode_requires_token_even_from_loopback(make_client, ctx):
    # Behind a tunnel every request comes from 127.0.0.1 with a public Host header.
    http = make_client(client=LOCAL_CLIENT, host_header="my-tunnel.example", remote_access=True)
    assert (await http.get("/")).status_code == 401
    token = ctx.secrets.access_token()
    ok = await http.get("/", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200


async def test_login_sets_cookie_that_authorizes(make_client, ctx):
    http = make_client(client=REMOTE_CLIENT, host_header=LAN_HOST, host="0.0.0.0")
    assert (await http.get("/login", params={"token": "wrong"})).status_code == 401

    response = await http.get(
        "/login", params={"token": ctx.secrets.access_token()}, follow_redirects=False
    )
    assert response.status_code == 303
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie
    assert (await http.get("/")).status_code == 200  # cookie jar now carries the token


async def test_cross_origin_post_rejected(client):
    response = await client.post("/api/anything", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_origin"


async def test_same_origin_post_passes_to_app(client):
    response = await client.post("/api/anything", headers={"Origin": "http://127.0.0.1:8000"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_security_headers_present(client):
    response = await client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
