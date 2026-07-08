from pydantic import BaseModel


class ChatRequest(BaseModel):
    thread_id: str
    user_id: str
    message: str
    steam_id: str | None = None


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
    tool_calls_made: list[str] = []
    tool_rounds: int = 0
    token_usage: dict = {}


class StreamEvent(BaseModel):
    event: str  # "token" | "tool_call" | "done"
    data: str
