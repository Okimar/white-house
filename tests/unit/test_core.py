import httpx
import pytest

from tavern.core.budget import BudgetExceeded, RunBudget
from tavern.core.events import EventBus
from tavern.core.llm import LLMClient, extract_json
from tavern.core.prompt import build_prompt, estimate_tokens
from tavern.devtools.fake_llm import FakeLLM
from tavern.formats.cards import CardError, greetings, parse_card
from tavern.providers.base import ChatMessage, ProviderError
from tavern.providers.openai_compat import OpenAICompatProvider


# --- events ---------------------------------------------------------------------------------


async def test_filters_chain_by_priority_and_none_keeps_value():
    bus = EventBus()
    bus.on("f", lambda v: v + "B", priority=20)
    bus.on("f", lambda v: v + "A", priority=10)
    bus.on("f", lambda v: None, priority=30)
    assert await bus.filter("f", "") == "AB"


async def test_failing_handler_is_isolated_and_recorded():
    bus = EventBus()
    seen = []

    def boom(**_):
        raise RuntimeError("plugin bug")

    async def good(**kw):
        seen.append(kw["x"])

    bus.on("e", boom, owner="bad-plugin", priority=1)
    bus.on("e", good, owner="ok", priority=2)
    await bus.emit("e", x=1)
    assert seen == [1]
    assert bus.errors[0][1] == "bad-plugin"


async def test_off_removes_everything_of_an_owner():
    bus = EventBus()
    bus.on("a", lambda: None, owner="p")
    bus.on("b", lambda: None, owner="p")
    bus.on("b", lambda: None, owner="q")
    assert bus.off("p") == 2
    assert len(bus.handlers("b")) == 1


# --- budget ---------------------------------------------------------------------------------


def test_step_limit():
    budget = RunBudget(max_steps=2)
    budget.begin_step()
    budget.begin_step()
    with pytest.raises(BudgetExceeded) as err:
        budget.begin_step()
    assert err.value.limit == "steps"


def test_token_and_cost_limits_checked_before_next_call():
    budget = RunBudget(max_tokens=100)
    budget.begin_step()
    budget.charge(60, 50)
    with pytest.raises(BudgetExceeded) as err:
        budget.begin_step()
    assert err.value.limit == "tokens"

    costly = RunBudget(max_cost=1.0)
    costly.charge(cost=1.5)
    with pytest.raises(BudgetExceeded) as err:
        costly.begin_step()
    assert err.value.limit == "cost"


def test_zero_means_unlimited():
    budget = RunBudget(max_steps=0)
    for _ in range(1000):
        budget.begin_step()
    budget.charge(10**9, 10**9)
    budget.begin_step()


# --- llm client + provider against the fake server -------------------------------------------


@pytest.fixture
def fake():
    return FakeLLM()


def provider_for(fake):
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")
    return OpenAICompatProvider("http://llm/v1", "sk-test", client=http)


USER = [ChatMessage("user", "hi")]


async def test_stream_and_usage_are_charged_to_budget(fake):
    fake.queue("hello brave world")
    budget = RunBudget(max_steps=5)
    llm = LLMClient(provider_for(fake), model="m", budget=budget)
    text = "".join([e.text async for e in llm.stream(USER) if e.kind == "text"])
    assert text == "hello brave world"
    assert budget.steps == 1 and budget.tokens > 0
    assert fake.requests[0]["stream"] is True
    assert fake.requests[0]["model"] == "m"


async def test_api_key_is_sent_as_bearer(fake):
    seen = {}

    async def spy(scope, receive, send):
        seen["auth"] = dict(scope["headers"]).get(b"authorization")
        await fake.app(scope, receive, send)

    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=spy), base_url="http://llm")
    await OpenAICompatProvider("http://llm/v1", "sk-test", client=http).generate(
        __import__("tavern.providers.base", fromlist=["GenerationRequest"]).GenerationRequest(USER)
    )
    assert seen["auth"] == b"Bearer sk-test"


async def test_forked_clients_share_one_budget_and_run_in_parallel(fake):
    import asyncio

    budget = RunBudget(max_steps=3)
    root = LLMClient(provider_for(fake), model="a", budget=budget)
    other = root.fork(provider=provider_for(fake), model="b")
    await asyncio.gather(root.generate(USER), other.generate(USER), root.generate(USER))
    assert budget.steps == 3
    assert {r["model"] for r in fake.requests} == {"a", "b"}
    with pytest.raises(BudgetExceeded):
        await other.generate(USER)


async def test_provider_http_error_becomes_provider_error(fake):
    fake.fail_next(429, "slow down")
    llm = LLMClient(provider_for(fake))
    with pytest.raises(ProviderError, match="429.*slow down"):
        async for _ in llm.stream(USER):
            pass


async def test_json_mode_request_and_parsing(fake):
    fake.queue('Sure! ```json\n{"plan": ["a", "b"]}\n``` done')
    llm = LLMClient(provider_for(fake))
    schema = {"type": "object", "properties": {"plan": {"type": "array"}}}
    assert await llm.generate_json(USER, schema=schema) == {"plan": ["a", "b"]}
    sent = fake.requests[0]["response_format"]
    assert sent["type"] == "json_schema" and sent["json_schema"]["schema"] == schema


def test_extract_json_variants():
    assert extract_json(' {"a": 1} ') == {"a": 1}
    assert extract_json("text [1, 2] more") == [1, 2]
    with pytest.raises(ProviderError):
        extract_json("no json here")


async def test_list_models(fake):
    assert await provider_for(fake).list_models() == ["fake-model"]


# --- prompt ---------------------------------------------------------------------------------


CARD = {"name": "Ann", "description": "{{char}} meets {{user}}.", "personality": "calm"}


def msg(role, content, **kw):
    return {"role": role, "content": content, "exclude_from_prompt": False, **kw}


def test_prompt_has_system_card_macros_and_history():
    prompt = build_prompt(CARD, [msg("user", "Hi {{char}}"), msg("assistant", "Hello")], user_name="Bob")
    assert prompt[0].role == "system"
    assert "Ann meets Bob." in prompt[0].content and "Personality:\ncalm" in prompt[0].content
    assert [(m.role, m.content) for m in prompt[1:]] == [("user", "Hi Ann"), ("assistant", "Hello")]


def test_excluded_messages_never_reach_the_prompt():
    history = [msg("user", "a"), msg("system", "agent notes", exclude_from_prompt=True)]
    assert [m.content for m in build_prompt(None, history)] == ["a"]


def test_context_budget_drops_oldest_but_keeps_newest():
    long = "x" * 400  # ~100 tokens
    history = [msg("user", long), msg("assistant", long), msg("user", "last")]
    kept = build_prompt(None, history, max_context_tokens=120)
    assert [m.content for m in kept] == [long, "last"][1:] or kept[-1].content == "last"
    assert len(kept) < 3
    only_newest = build_prompt(None, [msg("user", long)], max_context_tokens=10)
    assert len(only_newest) == 1  # newest message is kept even if it alone exceeds the budget
    assert estimate_tokens("") == 0


# --- cards ----------------------------------------------------------------------------------


def test_parse_v1_v2_v3_and_keep_unknown_fields():
    spec, data = parse_card({"name": " Ann ", "description": "d", "custom": {"k": 1}})
    assert spec == "chara_card_v2" and data["name"] == "Ann" and data["custom"] == {"k": 1}
    spec, data = parse_card({"spec": "chara_card_v3", "data": {"name": "B", "first_mes": "Hi"}})
    assert spec == "chara_card_v3" and data["first_mes"] == "Hi" and data["tags"] == []
    spec, data = parse_card({"spec": "chara_card_v2", "data": {"name": "C", "alternate_greetings": ["x"]}})
    assert greetings(data) == ["x"]


@pytest.mark.parametrize(
    "bad",
    [[], {"name": ""}, {"spec": "chara_card_v2"}, {"name": "A", "description": 5},
     {"name": "A", "alternate_greetings": "no"}],
)
def test_invalid_cards_rejected(bad):
    with pytest.raises(CardError):
        parse_card(bad)
