"""Tiny async OpenAI helper used by qbo matcher / pipelines."""

from typing import Optional

from openai import AsyncOpenAI

from config.settings import settings


def make_llm_async(model: Optional[str] = None):
    """Return an async callable: prompt:str -> str. Convenience for matcher."""
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    chosen_model = model or settings.OPENAI_MODEL or "gpt-4o-mini"

    async def _call(prompt: str) -> str:
        resp = await client.chat.completions.create(
            model=chosen_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return (resp.choices[0].message.content or "") if resp.choices else ""

    return _call
