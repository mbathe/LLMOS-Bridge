"""Example module — Typed parameter models.

Define one Pydantic model per action.
All fields must have type annotations and descriptions.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SayHelloParams(BaseModel):
    """Parameters for the say_hello action."""

    name: str = Field(description="Name of the person to greet.")
    formal: bool = Field(default=False, description="Use a formal greeting.")


class CountWordsParams(BaseModel):
    """Parameters for the count_words action."""

    text: str = Field(description="Text to count words in.")
    include_punctuation: bool = Field(
        default=False, description="Count punctuation marks as words."
    )


# ---------------------------------------------------------------------------
# Registry — Map action names to their param models.
# ---------------------------------------------------------------------------

PARAMS_MAP: dict[str, type[BaseModel]] = {
    "say_hello": SayHelloParams,
    "count_words": CountWordsParams,
}
