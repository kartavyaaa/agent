from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.llm.base import LLMConfig, LLMMessage, LLMProvider
from integrations.local_fs import LocalFsClient
from plugins.base import HealthStatus, PluginBase
from plugins.file_reader.schemas import FileReaderConfig, FileReaderInput, FileReaderOutput

_SUMMARIZE_THRESHOLD = 500  # chars; below this, skip LLM call and return content directly

_SUMMARIZE_PROMPT = (
    "Summarise the following file content concisely. "
    "Focus on the key information a user would want to know. "
    "Do not include preamble like 'This file contains' — go straight to the summary.\n\n"
    "File: {path}\n\n"
    "{content}"
)


class FileReaderPlugin(PluginBase):
    name: ClassVar[str] = "read_file"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Read a file from the local filesystem and return its contents or a summary. "
        "The path must be relative to the configured sandbox root — do not use '..' or absolute paths. "
        "Set summarize=true (default) to get a concise LLM summary; summarize=false to get raw text."
    )
    capabilities: ClassVar[list[str]] = ["file_read"]
    permissions: ClassVar[list[str]] = ["fs:read"]
    dependencies: ClassVar[list[str]] = []
    input_schema: ClassVar[type[BaseModel]] = FileReaderInput
    output_schema: ClassVar[type[BaseModel]] = FileReaderOutput
    config_schema: ClassVar[type[BaseModel]] = FileReaderConfig

    def __init__(
        self,
        client: LocalFsClient,
        llm: LLMProvider,
        fast_model: str,
    ) -> None:
        self._client = client
        self._llm = llm
        self._fast_model = fast_model

    async def execute(
        self,
        input: BaseModel,  # noqa: A002
        *,
        user_id: uuid.UUID,  # noqa: ARG002
        db: AsyncSession,  # noqa: ARG002
    ) -> FileReaderOutput:
        data = cast(FileReaderInput, input)
        result = await self._client.read(data.path)

        if data.summarize and len(result.content) > _SUMMARIZE_THRESHOLD:
            prompt = _SUMMARIZE_PROMPT.format(path=result.path, content=result.content)
            llm_response = await self._llm.complete(
                messages=[LLMMessage(role="user", content=prompt)],
                tools=None,
                config=LLMConfig(model=self._fast_model),
            )
            summary = llm_response.content or result.content
            summarized = True
        else:
            summary = result.content
            summarized = False

        return FileReaderOutput(
            path=result.path,
            summary=summary,
            size_bytes=result.size_bytes,
            summarized=summarized,
        )

    async def health_check(self) -> HealthStatus:
        ok = await self._client.health_check()
        return HealthStatus(
            status="healthy" if ok else "unhealthy",
            message="ok" if ok else "sandbox root not found or not a directory",
            checked_at=datetime.now(UTC),
        )
