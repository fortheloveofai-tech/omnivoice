"""
Anthropic Claude LLM provider.
Set ANTHROPIC_API_KEY in .env
"""
import os

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful voice assistant. Keep responses concise and conversational — "
    "they will be spoken aloud, so avoid markdown, bullet points, or long lists."
)


async def generate(prompt: str, history: list = None) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    response = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return response.content[0].text.strip()
