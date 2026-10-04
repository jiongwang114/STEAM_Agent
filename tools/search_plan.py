"""Validated, stable contract for structured Steam retrieval plans."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class SearchPlan(BaseModel):
    """Small set of hard constraints that can be safely passed to RAG."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)
    free_only: bool = False
    min_year: int | None = Field(default=None, ge=1970, le=2100)
    has_multiplayer: bool | None = None
    genre: str | None = Field(default=None, max_length=80)
    min_metacritic: int | None = Field(default=None, ge=0, le=100)
    top_k: int = Field(default=10, ge=1, le=20)
    min_similarity: float = Field(default=0.3, ge=0.0, le=1.0)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("genre")
    @classmethod
    def strip_genre(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def validate_search_plan(value: SearchPlan | dict) -> SearchPlan:
    """Validate a plan supplied by an LLM or an integration boundary."""
    if isinstance(value, SearchPlan):
        return value
    return SearchPlan.model_validate(value)


__all__ = ["SearchPlan", "ValidationError", "validate_search_plan"]
