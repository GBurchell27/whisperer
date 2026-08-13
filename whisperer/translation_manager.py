"""Translation of transcripts via the OpenAI chat API."""

from __future__ import annotations

from openai import OpenAI


class TranslationManager:
    """Translates transcript text using a chat model and a configurable system prompt."""

    def __init__(self, client: OpenAI, model: str, system_prompt: str) -> None:
        self._client = client
        self._model = model
        self._system_prompt = system_prompt

    def translate(self, text: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": text},
            ],
        )
        return (response.choices[0].message.content or "").strip()
