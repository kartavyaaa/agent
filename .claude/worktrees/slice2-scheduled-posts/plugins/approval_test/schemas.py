from __future__ import annotations

from pydantic import BaseModel


class ApprovalTestInput(BaseModel):
    message: str  # LLM-supplied; no user_id


class ApprovalTestOutput(BaseModel):
    result: str
    confirmation: str


class ApprovalTestConfig(BaseModel):
    pass
