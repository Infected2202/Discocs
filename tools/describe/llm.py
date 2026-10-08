"""LM Studio (OpenAI-совместимый API): ответ — всегда JSON по схеме.

``think=False`` выключает размышления модели (``reasoning_effort: none``): там, где модель
работает с готовым текстом страницы, они только тратят время. Без текста перед глазами
модель без размышлений врёт — для составления запросов и итогового текста они включены.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import requests


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    by_step: dict[str, float] = field(default_factory=dict)


class LLM:
    def __init__(self, url: str, model: str) -> None:
        self.url = url.rstrip("/") + "/chat/completions"
        self.model = model
        self.usage = Usage()

    def json(
        self,
        step: str,
        system: str,
        user: str,
        schema: dict,
        *,
        think: bool,
        temperature: float = 0.2,
        max_tokens: int = 6000,
    ) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": step, "strict": True, "schema": schema}},
        }
        if not think:
            body["reasoning_effort"] = "none"
        started = time.time()
        response = requests.post(self.url, json=body, timeout=900)
        response.raise_for_status()
        data = response.json()
        elapsed = time.time() - started
        usage = data.get("usage") or {}
        self.usage.calls += 1
        self.usage.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.usage.completion_tokens += int(usage.get("completion_tokens") or 0)
        self.usage.seconds += elapsed
        self.usage.by_step[step] = self.usage.by_step.get(step, 0.0) + elapsed
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if not content.strip():
            raise RuntimeError(f"{step}: empty answer (finish_reason={choice.get('finish_reason')}, "
                               f"completion_tokens={usage.get('completion_tokens')})")
        return json.loads(content)
