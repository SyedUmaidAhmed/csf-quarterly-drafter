"""The boundary between this workflow and whatever model serves it.

Two calls, because the workflow only makes two kinds of request:

  read_with_citations   evidence in, cited prose out
  structured            prose in, a validated object out

They are separate methods rather than one flexible one because the Citations
API refuses to do both at once: enabling citations on a document and asking for
a structured output returns a 400. That constraint is the reason the graph
reads and structures in separate passes, so it is worth having it visible in
the type rather than buried in a call site.

Keeping this interface narrow is also the point at which model routing would
be handed to the corporate architecture engagement. Replacing AnthropicClient
with something that speaks to a shared gateway should not require touching a
single node.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from .config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    async def read_with_citations(
        self, system: str, instruction: str, documents: list[dict[str, Any]]
    ) -> Any:
        """Return raw response content: text blocks, some carrying citations."""
        ...

    async def structured(
        self, system: str, instruction: str, schema: type[T], fast: bool = False
    ) -> T:
        """Return an instance of schema. No documents, so no citation conflict.

        `fast` routes mechanical work — segmenting a document, say — to the
        smaller model, leaving the larger one for the judgment calls.
        """
        ...


class AnthropicClient:
    """LLMClient backed by Claude.

    The reading pass runs on a smaller model: it is mechanical work — restate
    what a document says, cite it — and the citations come from the API rather
    than from the model's judgment. The reasoning passes, where the actual
    difficulty lives, run on the larger one.
    """

    def __init__(self, settings: Settings) -> None:
        from langchain_anthropic import ChatAnthropic

        common = {
            "api_key": settings.require_api_key(),
            "max_tokens": settings.max_tokens,
            "timeout": 120,
            "max_retries": 3,
        }
        self._reader = ChatAnthropic(model=settings.reader_model, **common)
        self._reasoner = ChatAnthropic(model=settings.model, **common)

    async def check(self) -> None:
        """Smallest possible round trip, to tell a bad key from a working one.

        Worth its few tokens: without it the first sign of a wrong key is a
        failed run several model calls in, which reads like the workflow is
        broken rather than the credentials.
        """
        await self._reader.ainvoke(
            [{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=8,
        )

    async def read_with_citations(
        self, system: str, instruction: str, documents: list[dict[str, Any]]
    ) -> Any:
        response = await self._reader.ainvoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": [*documents, {"type": "text", "text": instruction}]},
            ]
        )
        return response.content

    async def structured(
        self, system: str, instruction: str, schema: type[T], fast: bool = False
    ) -> T:
        # Defaults to forced tool use rather than the native structured-output
        # format. Either would work here — there are no documents attached to
        # these calls — but tool use is the portable choice.
        base = self._reader if fast else self._reasoner
        model = base.with_structured_output(schema)
        return await model.ainvoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": instruction},
            ]
        )


class ReadOnlyClient:
    """Enough of an LLMClient to open the graph for checkpoint reads.

    Review, evidence, and audit must work without a live key. Generation still
    requires AnthropicClient.
    """

    async def read_with_citations(
        self, system: str, instruction: str, documents: list[dict[str, Any]]
    ) -> Any:
        raise RuntimeError("No API key loaded — cannot call the model.")

    async def structured(
        self, system: str, instruction: str, schema: type[T], fast: bool = False
    ) -> T:
        raise RuntimeError("No API key loaded — cannot call the model.")
