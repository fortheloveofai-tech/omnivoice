"""
Ollama local LLM provider — free, runs entirely on your machine.
Install: https://ollama.com  then: ollama pull llama3
"""
import os
import httpx

MODEL = os.getenv("OLLAMA_MODEL", "llama3")
BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful voice assistant. Keep responses concise and conversational — "
    "they will be spoken aloud, so avoid markdown, bullet points, or long lists."
)


async def generate(prompt: str, history: list = None) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{BASE_URL}/api/chat",
            json={"model": MODEL, "messages": messages, "stream": False},
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
