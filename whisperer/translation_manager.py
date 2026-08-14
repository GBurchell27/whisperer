"""Translation of transcripts via the OpenAI chat API."""

from __future__ import annotations

from openai import OpenAI


class TranslationManager:
    """Rewrites transcript text using a chat model and a system prompt."""

    def __init__(
        self,
        client: OpenAI,
        model: str,
        system_prompt: str,
        reasoning_effort: str = "",
    ) -> None:
        self._client = client
        self._model = model
        self._system_prompt = system_prompt
        self._reasoning_effort = reasoning_effort

    def rewrite(self, text: str, system_prompt: str | None = None) -> str:
        prompt = system_prompt if system_prompt is not None else self._system_prompt
        request: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
        }
        if self._reasoning_effort:
            request["reasoning_effort"] = self._reasoning_effort
        response = self._client.chat.completions.create(**request)
        return (response.choices[0].message.content or "").strip()
