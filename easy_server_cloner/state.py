"""
Fortsetzbarkeit.

Cloning a large server takes minutes. If it breaks off — a dropped
connection, a restart, the cancel button — the second attempt should not
start from zero. So the cloner records what already exists after every
already exists.

The state lives as JSON at `state/<target-id>.json` and is deleted once a
run finishes cleanly.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

STATE_DIR = Path(__file__).resolve().parent.parent / "state"

# A state older than this belongs to a long-forgotten attempt and is no
# longer offered for resuming.
MAX_AGE_SECONDS = 24 * 60 * 60


@dataclass(slots=True)
class CloneState:
    """What has already been created in this target server."""

    target_id: int
    source_name: str = ""
    # Blueprint key -> ID of the object created in the target
    roles: dict[str, int] = field(default_factory=dict)
    channels: dict[str, int] = field(default_factory=dict)
    emojis: list[str] = field(default_factory=list)
    stickers: list[str] = field(default_factory=list)
    stages_done: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    # ── Dateizugriff ──────────────────────────────────────────────────────────

    @staticmethod
    def path_for(target_id: int) -> Path:
        return STATE_DIR / f"{target_id}.json"

    @classmethod
    def load(cls, target_id: int) -> "CloneState | None":
        path = cls.path_for(target_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Checkpoint %s is unreadable: %s", path, exc)
            return None

        if time.time() - data.get("updated_at", 0) > MAX_AGE_SECONDS:
            log.info("Checkpoint for %s is stale and is being discarded.", target_id)
            path.unlink(missing_ok=True)
            return None

        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        self.updated_at = time.time()
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            path = self.path_for(self.target_id)
            # Write to a sidecar file first, then rename: that way a crash
            # mid-write leaves the previous state intact.
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.__as_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
            )
            tmp.replace(path)
        except OSError as exc:
            log.warning("Could not save the checkpoint: %s", exc)

    def clear(self) -> None:
        self.path_for(self.target_id).unlink(missing_ok=True)

    def __as_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "source_name": self.source_name,
            "roles": self.roles,
            "channels": self.channels,
            "emojis": self.emojis,
            "stickers": self.stickers,
            "stages_done": self.stages_done,
            "updated_at": self.updated_at,
        }

    # ── Abfragen ──────────────────────────────────────────────────────────────

    @property
    def is_empty(self) -> bool:
        return not (self.roles or self.channels or self.emojis or self.stickers)

    @property
    def object_count(self) -> int:
        return len(self.roles) + len(self.channels) + len(self.emojis) + len(self.stickers)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.updated_at)

    def mark_stage(self, stage: str) -> None:
        if stage not in self.stages_done:
            self.stages_done.append(stage)
