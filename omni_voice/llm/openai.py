"""
OpenAI GPT LLM provider.
Set OPENAI_API_KEY in .env
"""
import os

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful voice assistant. Keep responses concise and conversational — "
    "they will be spoken aloud, so avoid markdown, bullet points, or long lists."
)


async def generate(prompt: str, history: list = None) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    response = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=1024,
    )
    return response.choices[0].message.content.strip()
