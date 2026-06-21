from __future__ import annotations

import os
import time
from dataclasses import dataclass

from openai import OpenAI

from .config import load_env


@dataclass
class MiniMaxResult:
    response_text: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    error: str | None


def get_key() -> str | None:
    load_env()
    return os.environ.get("MINIMAX_API_KEY") or os.environ.get("MINIMAX_KEY") or os.environ.get("MINIMAX_TOKEN")


def call_minimax(prompt: str) -> MiniMaxResult:
    key = get_key()
    if not key:
        return MiniMaxResult(None, 0, None, None, "missing MiniMax API key")
    client = OpenAI(api_key=key, base_url="https://api.minimax.io/v1")
    started = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model="MiniMax-M3",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_completion_tokens=4096,
            extra_body={
                "thinking": {"type": "disabled"},
                "service_tier": "standard",
            },
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = getattr(resp, "usage", None)
        return MiniMaxResult(
            response_text=resp.choices[0].message.content,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            error=None,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return MiniMaxResult(None, latency_ms, None, None, str(exc))
