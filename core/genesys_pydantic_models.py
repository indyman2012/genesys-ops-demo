from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class Segment(BaseModel):
    """
    Loose validation for Genesys segment objects.

    extra='allow' is intentional because Genesys may add fields and we do not
    want ingestion to fail just because the payload contains more than we use.
    """
    model_config = ConfigDict(extra="allow")

    segmentType: Optional[str] = None
    segmentStart: Optional[str] = None
    segmentEnd: Optional[str] = None
    disconnectType: Optional[str] = None
    queueId: Optional[str] = None
    wrapUpCode: Optional[str] = None


class Session(BaseModel):
    model_config = ConfigDict(extra="allow")

    mediaType: Optional[str] = None
    direction: Optional[str] = None
    queueId: Optional[str] = None
    queueName: Optional[str] = None
    dnis: Optional[str] = None
    ani: Optional[str] = None
    segments: list[Segment] = Field(default_factory=list)


class Participant(BaseModel):
    model_config = ConfigDict(extra="allow")

    participantId: Optional[str] = None
    participantName: Optional[str] = None
    purpose: Optional[str] = None
    queueId: Optional[str] = None
    queueName: Optional[str] = None
    sessions: list[Session] = Field(default_factory=list)


class Conversation(BaseModel):
    model_config = ConfigDict(extra="allow")

    conversationId: Optional[str] = None
    conversationStart: Optional[str] = None
    conversationEnd: Optional[str] = None
    originatingDirection: Optional[str] = None
    participants: list[Participant] = Field(default_factory=list)


def validate_conversation_payload(payload: dict[str, Any]) -> Conversation:
    """
    Validates a raw Genesys conversation payload.

    This is intentionally permissive:
    - it requires valid structure for fields we use
    - it allows extra Genesys fields
    - it does not block future schema expansion
    """
    return Conversation.model_validate(payload)
