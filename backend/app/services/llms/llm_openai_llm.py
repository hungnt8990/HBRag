from __future__ import annotations

from collections.abc import AsyncIterator

from app.services.llm_gateway.llm_gateway_external_client import ExternalLLMClient


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        client: ExternalLLMClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = client or ExternalLLMClient(
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
        )

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        return await self._client.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._model,
        )

    async def stream_generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> AsyncIterator[str]:
        async for delta in self._client.stream_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._model,
        ):
            yield delta

    @staticmethod
    def _parse_sse_line(line: str) -> str:
        return ExternalLLMClient._parse_sse_line(line)

    def _headers(self) -> dict[str, str]:
        return self._client._headers()

    @staticmethod
    def _extract_content(response: dict[str, object]) -> str:
        return ExternalLLMClient._extract_chat_content(response)


OpenAILLM = OpenAICompatibleLLM
