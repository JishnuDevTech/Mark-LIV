"""Provider-neutral reasoning router owned by JARVIS."""
from __future__ import annotations

from core import gemini
from core import llm_client


def _openrouter_config() -> tuple[str, str, str]:
    cfg = llm_client._load_config()
    key = str(cfg.get("openrouter_api_key", "") or "").strip()
    model = str(cfg.get("openrouter_model", "") or "openai/gpt-4o-mini").strip()
    url = str(cfg.get("openrouter_url", "https://openrouter.ai/api/v1") or "").rstrip("/")
    return key, model, url


def _openrouter(prompt: str, system: str, timeout: int) -> str:
    import requests

    key, model, url = _openrouter_config()
    if not key:
        raise RuntimeError("OpenRouter is not configured")
    response = requests.post(
        f"{url}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ], "temperature": 0.2, "max_tokens": 6000},
        timeout=timeout,
    )
    response.raise_for_status()
    return (response.json().get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


def _local(prompt: str, system: str, timeout: int) -> str:
    return llm_client.call_llm_text(prompt, system=system, timeout=timeout)


def generate(prompt: str, system: str = "", tier: str = gemini.SMART,
             timeout: int = 120, on_provider=None) -> tuple[str, str]:
    """Try providers in order; return text and provider used.

    Provider failures are intentionally not persisted here. The caller records
    them in JARVIS project state, while this router only chooses a brain.
    """
    providers = [
        ("gemini", lambda: gemini.text(prompt, tier=tier, timeout_ms=timeout * 1000)),
        ("openrouter", lambda: _openrouter(prompt, system, timeout)),
        ("local", lambda: _local(prompt, system, timeout)),
    ]
    failures = []
    for name, call in providers:
        try:
            result = call()
            if result:
                if on_provider:
                    on_provider(name)
                return result, name
            failures.append(f"{name}: empty response")
        except Exception as exc:
            failures.append(f"{name}: {exc}")
    raise RuntimeError("All reasoning providers failed: " + " | ".join(failures))
