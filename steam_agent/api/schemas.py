from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    user_id: str | None = Field(default=None, min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=6000)
    steam_id: str | None = Field(default=None, max_length=32)


class AuthRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class SteamBindRequest(BaseModel):
    steam_id: str = Field(pattern=r"^\d{17}$")


class ThemeRequest(BaseModel):
    theme: str = Field(pattern=r"^(dark|light)$")


class ThreadTitleRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=50)


class ExecutionStep(BaseModel):
    name: str
    status: str
    duration_ms: int = Field(default=0, ge=0)
    round: int | None = Field(default=None, ge=0)


class ExecutionSummary(BaseModel):
    status: str = "success"
    duration_ms: int = Field(default=0, ge=0)
    steps: list[ExecutionStep] = Field(default_factory=list)


class ChatResponse(BaseModel):
    status: str = "success"
    thread_id: str
    reply: str
    tool_calls_made: list[str] = Field(default_factory=list)
    tool_rounds: int = 0
    token_usage: dict = Field(default_factory=dict)
    execution: ExecutionSummary = Field(default_factory=ExecutionSummary)
    run_metadata: dict = Field(default_factory=dict)


class StreamEvent(BaseModel):
    event: str  # "snapshot" | "status" | "token" | "done" | "error" | "cancelled"
    data: str | dict
