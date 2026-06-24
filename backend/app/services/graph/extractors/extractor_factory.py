from __future__ import annotations

from app.core.config import settings
from app.services.graph.extractors.extractor_base import GraphExtractor
from app.services.graph.extractors.extractor_fake_extractor import FakeGraphExtractor
from app.services.graph.extractors.extractor_llm_extractor import LLMGraphExtractor
from app.services.llm_gateway import LLMGateway


def build_graph_extractor(
    *,
    llm_provider: LLMGateway,
    provider: str | None = None,
) -> GraphExtractor:
    selected = (provider or settings.graph_extractor_provider).lower().strip()
    if selected == "fake":
        return FakeGraphExtractor()
    if selected == "llm":
        return LLMGraphExtractor(llm_provider)
    raise ValueError(f"Unsupported graph extractor provider: {selected}")
