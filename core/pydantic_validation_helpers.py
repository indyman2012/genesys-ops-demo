from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.genesys_pydantic_models import validate_conversation_payload


class ValidationLogger:
    """
    CSV logger for validation failures.

    This keeps ingestion in warn-and-continue mode while giving us a clean
    audit trail of malformed or unexpected payloads.
    """

    def __init__(self, log_dir: str | Path = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / "pydantic_validation_failures.csv"
        self._ensure_header()

    def _ensure_header(self) -> None:
        if self.path.exists():
            return

        with self.path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "logged_at",
                "conversation_id",
                "error_count",
                "errors_json",
            ])

    def log_failure(self, conversation_id: str | None, error: ValidationError) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            errors = error.errors()
            writer.writerow([
                datetime.utcnow().isoformat(timespec="seconds") + "Z",
                conversation_id or "",
                len(errors),
                json.dumps(errors, default=str),
            ])


def validate_conversation_warn(payload: dict[str, Any], logger: ValidationLogger | None = None):
    """
    Validate a conversation in warn-and-continue mode.

    Returns:
        Conversation model if valid
        None if invalid
    """
    try:
        return validate_conversation_payload(payload)
    except ValidationError as exc:
        if logger:
            conversation_id = payload.get("conversationId") if isinstance(payload, dict) else None
            logger.log_failure(conversation_id, exc)
        return None
