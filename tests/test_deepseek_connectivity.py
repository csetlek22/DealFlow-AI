"""Minimal DeepSeek connectivity + structured-output smoke test.

Run with the repo venv:
    .venv/Scripts/python.exe tests/test_deepseek_connectivity.py

Reads DEEPSEEK_API_KEY / DEEPSEEK_MODEL / DEEPSEEK_BASE_URL from .env. Makes
three tiny calls so we can see (1) whether DeepSeek is reachable, (2) that the
default `with_structured_output` method fails, and (3) that
`method="function_calling"` works.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")


class Tiny(BaseModel):
    company: str = Field(description="A company name")
    is_real: bool = Field(description="Whether it appears to be a real company")


def main():
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    if not api_key:
        print("NO DEEPSEEK_API_KEY in .env — cannot test")
        return

    llm = ChatOpenAI(
        model=model,
        temperature=0,
        api_key=api_key,
        base_url=base_url,
    )

    # 1) Plain connectivity (no structured output involved).
    try:
        msg = llm.invoke("Reply with exactly the word: OK")
        print("CONNECTIVITY_OK:", msg.content)
    except Exception as e:
        print("CONNECTIVITY_FAIL:", type(e).__name__, e)
        return

    # 2) Default structured output (this is what the pipeline uses today).
    try:
        out = llm.with_structured_output(Tiny).invoke(
            "Nestle is a real food and beverage company"
        )
        print("DEFAULT_METHOD_OK:", out)
    except Exception as e:
        print("DEFAULT_METHOD_FAIL:", type(e).__name__, str(e)[:300])

    # 3) function_calling method (the fix).
    try:
        out = llm.with_structured_output(
            Tiny, method="function_calling"
        ).invoke("Nestle is a real food and beverage company")
        print("FUNCTION_CALLING_OK:", out)
    except Exception as e:
        print("FUNCTION_CALLING_FAIL:", type(e).__name__, str(e)[:300])


if __name__ == "__main__":
    main()
