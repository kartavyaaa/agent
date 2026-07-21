from __future__ import annotations

from pydantic import BaseModel, Field


class WebSearchInput(BaseModel):
    query: str
    max_results: int = Field(default=5, ge=1, le=10)


class SearchResult(BaseModel):
    title: str
    link: str
    snippet: str


class WebSearchOutput(BaseModel):
    query: str
    results: list[SearchResult]
    result_count: int


class WebSearchConfig(BaseModel):
    pass
