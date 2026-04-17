from pydantic import BaseModel, Field
from typing import Any


class SportsDataPayload(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    categories: list[dict[str, Any]] = Field(default_factory=list)
    highlights: list[dict[str, Any]] = Field(default_factory=list)
    source_url: str | None = None


class SportsDataResponse(BaseModel):
    status: str = "success"
    data: SportsDataPayload
