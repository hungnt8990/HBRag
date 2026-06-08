from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

MemoryType = Literal["preference", "task", "entity", "instruction", "fact"]
MemoryContent = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class MemorySettingsResponse(BaseModel):
    memory_enabled: bool
    memory_provider: str
    mem0_enabled: bool
    memory_top_k: int
    memory_auto_save: bool
    memory_inject_into_prompt: bool


class MemoryItemResponse(BaseModel):
    id: str | None
    content: str
    memory_type: str
    source: str
    score: float | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class MemoryCreateRequest(BaseModel):
    content: MemoryContent
    memory_type: MemoryType = "fact"
    source: Literal["manual"] = "manual"


class MemoryDeleteResponse(BaseModel):
    memory_id: str
    deleted: bool
