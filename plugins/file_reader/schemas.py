from __future__ import annotations

from pydantic import BaseModel


class FileReaderInput(BaseModel):
    path: str
    summarize: bool = True


class FileReaderOutput(BaseModel):
    path: str
    summary: str  # LLM summary when summarized=True, raw content otherwise
    size_bytes: int
    summarized: bool


class FileReaderConfig(BaseModel):
    pass
