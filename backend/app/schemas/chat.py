from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

SearchQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
AnswerMode = Literal["generative", "extractive", "hybrid"]
AnswerStyle = Literal["concise", "detailed", "policy_explainer", "table_qa"]
ProfileName = Literal[
    "auto", "legal_admin", "general", "technical", "faq", "spreadsheet"
]


class RagChatRequest(BaseModel):
    query: SearchQuery
    session_id: UUID | None = None
    document_id: UUID | None = None
    organization_id: UUID | None = None
    knowledge_base_ids: list[UUID] | None = None
    include_descendants: bool = False
    profile: ProfileName | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    use_memory: bool = True
    use_mem0: bool = True
    memory_top_k: int = Field(default=5, ge=1, le=50)
    answer_mode: AnswerMode | None = None
    answer_style: AnswerStyle | None = None
    max_context_chars: int | None = Field(default=None, ge=500, le=20000)
    use_graph: bool = False
    graph_expansion_depth: int = Field(default=1, ge=0, le=5)
    graph_expansion_limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_candidate_window(self) -> "RagChatRequest":
        if (
            self.candidate_k is not None
            and self.top_k is not None
            and self.candidate_k < self.top_k
        ):
            raise ValueError("candidate_k must be greater than or equal to top_k.")
        return self


class RagChatScope(BaseModel):
    document_id: UUID | None = None
    organization_id: UUID | None = None
    knowledge_base_ids: list[UUID] | None = None
    include_descendants: bool = False


class RagChatStreamRequest(BaseModel):
    query: SearchQuery
    session_id: UUID | None = None
    scope: RagChatScope = Field(default_factory=RagChatScope)
    profile: ProfileName | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    stream: bool = True
    use_memory: bool = True
    use_mem0: bool = True
    memory_top_k: int = Field(default=5, ge=1, le=50)
    answer_mode: AnswerMode | None = None
    answer_style: AnswerStyle | None = None
    max_context_chars: int | None = Field(default=None, ge=500, le=20000)
    use_graph: bool = False
    graph_expansion_depth: int = Field(default=1, ge=0, le=5)
    graph_expansion_limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_candidate_window(self) -> "RagChatStreamRequest":
        if (
            self.candidate_k is not None
            and self.top_k is not None
            and self.candidate_k < self.top_k
        ):
            raise ValueError("candidate_k must be greater than or equal to top_k.")
        return self


class RagCitationResponse(BaseModel):
    citation_index: int
    chunk_id: UUID
    document_id: UUID
    document_title: str | None = None
    file_name: str | None = None
    chunk_index: int
    quote: str | None
    article_number: str | None = None
    article_title: str | None = None
    chapter_title: str | None = None
    page_number: int | None = None
    source_flags: list[Literal["vector", "keyword", "graph", "neighbor"]]
    metadata: dict[str, object]


class RagChatResponse(BaseModel):
    session_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    answer: str
    citations: list[RagCitationResponse]
