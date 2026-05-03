"""
OpenRouter LLM provider — access 100+ models via one API key.
Set OPENROUTER_API_KEY and OPENROUTER_MODEL in .env
"""
import os
import httpx

API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL   = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful voice assistant. Keep responses concise and conversational — "
    "they will be spoken aloud, so avoid markdown, bullet points, or long lists."
)


async def generate(prompt: str, history: list = None) -> str:
    if not API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set. Run python setup_env.py")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": MODEL, "messages": messages},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
