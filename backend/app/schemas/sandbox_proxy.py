"""Pydantic schemas for the sandbox LLM proxy endpoint."""

from pydantic import BaseModel


class SandboxProxyChatMessage(BaseModel):
    role: str
    content: str


class SandboxProxyChatRequest(BaseModel):
    model: str
    messages: list[SandboxProxyChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    # Pass through any extra OpenAI params
    model_config = {"extra": "allow"}


class SandboxProxyChatChoice(BaseModel):
    index: int
    message: SandboxProxyChatMessage
    finish_reason: str | None = None


class SandboxProxyChatUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class SandboxProxyChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    choices: list[SandboxProxyChatChoice]
    usage: SandboxProxyChatUsage | None = None
