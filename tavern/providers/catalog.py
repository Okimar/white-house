"""Known services: a ready address and protocol for each, so connecting is "pick and paste a key".

A connection remembers the service it was made from (`service`), but only `kind` and `base_url`
decide how requests go; the user may change the address (a proxy of their own, a mirror).
Addresses are the services' documented API roots and may drift: the user can always edit them.
"""

from __future__ import annotations

from typing import Any

# key: "required" | "optional" | "none"
SERVICES: list[dict[str, Any]] = [
    # Cloud
    {"id": "gemini", "name": "Google Gemini (AI Studio)", "kind": "gemini", "group": "cloud",
     "base_url": "https://generativelanguage.googleapis.com/v1beta", "key": "required",
     "site": "https://aistudio.google.com/apikey"},
    {"id": "gemini_openai", "name": "Google Gemini (как OpenAI)", "kind": "openai", "group": "cloud",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key": "required",
     "site": "https://aistudio.google.com/apikey"},
    {"id": "anthropic", "name": "Anthropic (Claude)", "kind": "anthropic", "group": "cloud",
     "base_url": "https://api.anthropic.com/v1", "key": "required",
     "site": "https://console.anthropic.com/settings/keys"},
    {"id": "openrouter", "name": "OpenRouter", "kind": "openai", "group": "cloud",
     "base_url": "https://openrouter.ai/api/v1", "key": "required", "site": "https://openrouter.ai/keys"},
    {"id": "openai", "name": "OpenAI", "kind": "openai", "group": "cloud",
     "base_url": "https://api.openai.com/v1", "key": "required", "site": "https://platform.openai.com/api-keys"},
    {"id": "deepseek", "name": "DeepSeek", "kind": "openai", "group": "cloud",
     "base_url": "https://api.deepseek.com/v1", "key": "required", "site": "https://platform.deepseek.com/api_keys"},
    {"id": "mistral", "name": "Mistral", "kind": "openai", "group": "cloud",
     "base_url": "https://api.mistral.ai/v1", "key": "required", "site": "https://console.mistral.ai/api-keys"},
    {"id": "xai", "name": "xAI (Grok)", "kind": "openai", "group": "cloud",
     "base_url": "https://api.x.ai/v1", "key": "required", "site": "https://console.x.ai"},
    {"id": "groq", "name": "Groq", "kind": "openai", "group": "cloud",
     "base_url": "https://api.groq.com/openai/v1", "key": "required", "site": "https://console.groq.com/keys"},
    {"id": "cohere", "name": "Cohere", "kind": "openai", "group": "cloud",
     "base_url": "https://api.cohere.ai/compatibility/v1", "key": "required",
     "site": "https://dashboard.cohere.com/api-keys"},
    {"id": "perplexity", "name": "Perplexity", "kind": "openai", "group": "cloud",
     "base_url": "https://api.perplexity.ai", "key": "required", "site": "https://www.perplexity.ai/settings/api"},
    {"id": "together", "name": "Together AI", "kind": "openai", "group": "cloud",
     "base_url": "https://api.together.xyz/v1", "key": "required", "site": "https://api.together.xyz/settings/api-keys"},
    {"id": "fireworks", "name": "Fireworks", "kind": "openai", "group": "cloud",
     "base_url": "https://api.fireworks.ai/inference/v1", "key": "required",
     "site": "https://fireworks.ai/account/api-keys"},
    {"id": "deepinfra", "name": "DeepInfra", "kind": "openai", "group": "cloud",
     "base_url": "https://api.deepinfra.com/v1/openai", "key": "required", "site": "https://deepinfra.com/dash/api_keys"},
    {"id": "cerebras", "name": "Cerebras", "kind": "openai", "group": "cloud",
     "base_url": "https://api.cerebras.ai/v1", "key": "required", "site": "https://cloud.cerebras.ai"},
    {"id": "moonshot", "name": "Moonshot (Kimi)", "kind": "openai", "group": "cloud",
     "base_url": "https://api.moonshot.ai/v1", "key": "required", "site": "https://platform.moonshot.ai"},
    {"id": "zai", "name": "Z.ai (GLM)", "kind": "openai", "group": "cloud",
     "base_url": "https://api.z.ai/api/paas/v4", "key": "required", "site": "https://z.ai/manage-apikey/apikey-list"},
    {"id": "nanogpt", "name": "NanoGPT", "kind": "openai", "group": "cloud",
     "base_url": "https://nano-gpt.com/api/v1", "key": "required", "site": "https://nano-gpt.com/api"},
    {"id": "novita", "name": "Novita AI", "kind": "openai", "group": "cloud",
     "base_url": "https://api.novita.ai/v3/openai", "key": "required", "site": "https://novita.ai/settings/key-management"},
    {"id": "featherless", "name": "Featherless", "kind": "openai", "group": "cloud",
     "base_url": "https://api.featherless.ai/v1", "key": "required", "site": "https://featherless.ai/account/api-keys"},
    {"id": "infermatic", "name": "Infermatic", "kind": "openai", "group": "cloud",
     "base_url": "https://api.totalgpt.ai/v1", "key": "required", "site": "https://infermatic.ai"},
    {"id": "chutes", "name": "Chutes", "kind": "openai", "group": "cloud",
     "base_url": "https://llm.chutes.ai/v1", "key": "required", "site": "https://chutes.ai"},
    {"id": "aimlapi", "name": "AI/ML API", "kind": "openai", "group": "cloud",
     "base_url": "https://api.aimlapi.com/v1", "key": "required", "site": "https://aimlapi.com/app/keys"},
    # On your own machine
    {"id": "koboldcpp", "name": "KoboldCpp", "kind": "openai", "group": "local",
     "base_url": "http://127.0.0.1:5001/v1", "key": "none"},
    {"id": "llamacpp", "name": "llama.cpp server", "kind": "openai", "group": "local",
     "base_url": "http://127.0.0.1:8080/v1", "key": "optional"},
    {"id": "ollama", "name": "Ollama", "kind": "openai", "group": "local",
     "base_url": "http://127.0.0.1:11434/v1", "key": "none"},
    {"id": "lmstudio", "name": "LM Studio", "kind": "openai", "group": "local",
     "base_url": "http://127.0.0.1:1234/v1", "key": "none"},
    {"id": "textgen", "name": "text-generation-webui", "kind": "openai", "group": "local",
     "base_url": "http://127.0.0.1:5000/v1", "key": "none"},
    {"id": "tabby", "name": "TabbyAPI", "kind": "openai", "group": "local",
     "base_url": "http://127.0.0.1:5000/v1", "key": "optional"},
    {"id": "vllm", "name": "vLLM", "kind": "openai", "group": "local",
     "base_url": "http://127.0.0.1:8000/v1", "key": "optional"},
    {"id": "aphrodite", "name": "Aphrodite", "kind": "openai", "group": "local",
     "base_url": "http://127.0.0.1:2242/v1", "key": "optional"},
    # Your own address
    {"id": "custom", "name": "Свой адрес (как OpenAI)", "kind": "openai", "group": "custom",
     "base_url": "", "key": "optional"},
    {"id": "custom_anthropic", "name": "Свой адрес (как Claude)", "kind": "anthropic", "group": "custom",
     "base_url": "", "key": "optional"},
    {"id": "custom_gemini", "name": "Свой адрес (как Gemini)", "kind": "gemini", "group": "custom",
     "base_url": "", "key": "optional"},
]

GROUPS = [("cloud", "Сервисы в интернете"), ("local", "Модели на своём компьютере"), ("custom", "Свой адрес")]

BY_ID = {s["id"]: s for s in SERVICES}


def catalog(kinds: list[str]) -> dict[str, Any]:
    """Services whose protocol is available (a plugin may add kinds; core ones are always there)."""
    return {"services": [s for s in SERVICES if s["kind"] in kinds], "groups": GROUPS}
