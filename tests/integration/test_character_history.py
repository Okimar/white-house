"""History of a character: every change is a step, "Отменить" takes them back, versions restore."""

from __future__ import annotations

from tavern.services import history


async def setup(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Kind."})).json()
    return ann


async def blocks(client, ann):
    return {b["title"]: b for b in (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["blocks"]}


async def undo(client, ann):
    response = await client.post(f"/api/characters/{ann['id']}/undo")
    assert response.status_code == 200, response.text
    return response.json()


async def test_undo_takes_back_every_kind_of_change_one_by_one(client):
    ann = await setup(client)
    look = (await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "Look", "content": "Tall."})).json()
    hair = (await client.post(f"/api/characters/{ann['id']}/blocks",
                              json={"title": "Hair", "content": "Red.", "parent_id": look["id"]})).json()
    await client.put(f"/api/blocks/{hair['id']}", json={"content": "Black.", "chance": 40})
    await client.put(f"/api/characters/{ann['id']}/blocks/layout", json={"items": [{"id": hair["id"], "x": 5, "y": 5}]})
    eyes = (await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "Eyes"})).json()
    await client.post(f"/api/characters/{ann['id']}/links", json={"a": hair["id"], "b": eyes["id"]})
    await client.delete(f"/api/blocks/{look['id']}")  # takes Hair and the link with it
    card = (await client.get(f"/api/characters/{ann['id']}")).json()["data"]
    await client.put(f"/api/characters/{ann['id']}", json={**card, "description": "Cruel."})

    steps = (await client.get(f"/api/characters/{ann['id']}/history")).json()["versions"][0]["steps"]
    assert len(steps) == 7  # the move on the map is not a step

    assert (await undo(client, ann))["undone"]["what"].startswith("Карточка")
    assert (await client.get(f"/api/characters/{ann['id']}")).json()["data"]["description"] == "Kind."
    await undo(client, ann)  # the delete: Look, Hair and the link come back
    now = await blocks(client, ann)
    assert now["Hair"]["parent_id"] == look["id"] and now["Hair"]["content"] == "Black."
    assert (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["links"] == [sorted([hair["id"], eyes["id"]])]
    await undo(client, ann)  # the link
    assert (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["links"] == []
    await undo(client, ann)  # Eyes was created
    assert "Eyes" not in await blocks(client, ann)
    await undo(client, ann)  # the edit
    hair_now = (await blocks(client, ann))["Hair"]
    assert (hair_now["content"], hair_now["chance"]) == ("Red.", 100)
    await undo(client, ann)
    await undo(client, ann)
    assert await blocks(client, ann) == {}
    assert (await undo(client, ann)) == {"undone": None}


async def test_a_new_change_after_undo_drops_the_undone_steps(client):
    ann = await setup(client)
    await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "A"})
    await undo(client, ann)
    await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "B"})
    steps = (await client.get(f"/api/characters/{ann['id']}/history")).json()["versions"][0]["steps"]
    assert [s["what"] for s in steps] == ["Новый пузырь «B»"]


async def test_versions_close_and_restore(client, ctx):
    ann = await setup(client)
    await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "First"})
    await client.post(f"/api/characters/{ann['id']}/history/close")
    await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "Second"})
    await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "Third"})
    listed = (await client.get(f"/api/characters/{ann['id']}/history")).json()
    assert [len(v["steps"]) for v in listed["versions"]] == [2, 1] and listed["bytes"] > 0
    first = listed["versions"][-1]["id"]
    restored = await client.post(f"/api/characters/{ann['id']}/history/{first}/restore")
    assert restored.json() == {"undone": 2}
    assert set(await blocks(client, ann)) == {"First"}


async def test_history_can_be_switched_off(client):
    ann = await setup(client)
    await client.put("/api/settings", json={"character_history": False})
    await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "A"})
    assert (await client.get(f"/api/characters/{ann['id']}/history")).json()["versions"] == []


async def test_a_long_history_stays_small(client, ctx):
    ann = await setup(client)
    block = (await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "Diary", "content": "x"})).json()
    text = "Она помнит каждый день у реки. " * 40
    for i in range(200):
        await client.put(f"/api/blocks/{block['id']}", json={"content": f"{text} {i}"})
    size = ctx.history.size(ann["id"])
    assert size < 200 * len(text.encode()) / 5  # packed: far smaller than the raw texts
    assert history.enabled(ctx)
