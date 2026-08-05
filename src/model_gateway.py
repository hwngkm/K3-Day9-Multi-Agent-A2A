"""One optional, post-verification gateway for a lightweight Qwen model."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen


QWEN_MODEL_NAME = "Qwen/Qwen2.5-3B-Instruct"
QWEN_OLLAMA_MODEL = "qwen2.5:3b"
QWEN_PARAMETER_COUNT = 3_085_938_688
QWEN_PARAMETER_SIZE = "3.09B"


class ModelGateway:
    """Qwen is optional and has no authority over submitted decision fields."""

    def __init__(self) -> None:
        self._enabled = os.environ.get("ENABLE_QWEN_EXPLANATION") == "1"
        self._base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    def explain(self, facts: Mapping[str, Any], fallback: str) -> dict[str, str]:
        if not self._enabled:
            return {
                "model": QWEN_MODEL_NAME,
                "mode": "deterministic_fallback",
                "text": fallback,
            }
        prompt = (
            "Write one concise Vietnamese customer-facing explanation. "
            "Do not alter facts, money, responsible party, or action. Facts: "
            + json.dumps(dict(facts), ensure_ascii=False)
        )
        payload = json.dumps(
            {
                "model": QWEN_OLLAMA_MODEL,
                "stream": False,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")
        request = Request(
            f"{self._base_url.rstrip('/')}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            text = str(data["message"]["content"]).strip()
            if text:
                return {"model": QWEN_MODEL_NAME, "mode": "qwen", "text": text}
        except (URLError, TimeoutError, KeyError, ValueError, OSError):
            pass
        return {"model": QWEN_MODEL_NAME, "mode": "deterministic_fallback", "text": fallback}
