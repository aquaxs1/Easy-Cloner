"""Data models for clone options, progress and the result report."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Stage(str, Enum):
    """The stages a clone run passes through."""

    IDLE = "Bereit"
    VALIDATING = "Checking prerequisites"
    READING = "Lese Quell-Server"
    WIPING = "Clearing the target server"
    SETTINGS = "Copying server settings"
    ROLES = "Copying roles"
    CATEGORIES = "Kopiere Kategorien"
    CHANNELS = "Kopiere Channels"
    FINALIZING = "Setze Feinheiten"
    EMOJIS = "Kopiere Emojis"
    STICKERS = "Kopiere Sticker"
    MESSAGES = "Re-staging messages"
    MEMBER_ROLES = "Assigning roles to members"
    DONE = "Abgeschlossen"
    FAILED = "Fehlgeschlagen"
    CANCELLED = "Abgebrochen"


# Order for the progress display. IDLE and the terminal states do not count.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.VALIDATING,
    Stage.READING,
    Stage.WIPING,
    Stage.SETTINGS,
    Stage.ROLES,
    Stage.CATEGORIES,
    Stage.CHANNELS,
    Stage.FINALIZING,
    Stage.EMOJIS,
    Stage.STICKERS,
    Stage.MESSAGES,
    Stage.MEMBER_ROLES,
)

END_STAGES = (Stage.DONE, Stage.FAILED, Stage.CANCELLED)


@dataclass(slots=True)
class CloneOptions:
    """What exactly should be copied?"""

    wipe_target: bool = True
    settings: bool = True
    icon: bool = True
    roles: bool = True
    channels: bool = True
    emojis: bool = True
    stickers: bool = True
    dry_run: bool = False

    # Gleichnamige Objekte im Ziel wiederverwenden statt doppelt anzulegen.
    # This is the heart of /sync.
    skip_existing: bool = False

    # How many messages per text channel are re-staged through a webhook.
    # 0 = off. Needs the privileged message content intent.
    messages_per_channel: int = 0

    # Give roles to members present on both servers.
    # Needs the privileged members intent.
    member_roles: bool = False

    def summary(self) -> list[str]:
        """A human-readable list of the active options."""
        parts = [
            ("Server-Einstellungen", self.settings),
            ("Icon & Banner", self.icon),
            ("Roles", self.roles),
            ("Kategorien & Channels", self.channels),
            ("Emojis", self.emojis),
            ("Sticker", self.stickers),
        ]
        lines = [f"{'✅' if on else '❌'} {label}" for label, on in parts]
        if self.messages_per_channel:
            lines.append(f"✅ Messages (max {self.messages_per_channel}/channel)")
        if self.member_roles:
            lines.append("✅ Role assignments")
        if self.skip_existing:
            lines.append("♻️ Existing objects are reused")
        return lines


@dataclass(slots=True)
class CloneReport:
    """Counts what actually happened."""

    roles_created: int = 0
    categories_created: int = 0
    text_channels_created: int = 0
    voice_channels_created: int = 0
    stage_channels_created: int = 0
    forum_channels_created: int = 0
    emojis_created: int = 0
    stickers_created: int = 0
    messages_copied: int = 0
    members_updated: int = 0
    channels_deleted: int = 0
    roles_deleted: int = 0
    throttle_events: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    # Warnings and errors are capped so a broken server cannot blow up the
    # embed — the counters stay correct regardless.
    _warning_overflow: int = 0
    _error_overflow: int = 0

    MAX_MESSAGES = 25

    @property
    def channels_created(self) -> int:
        return (
            self.text_channels_created
            + self.voice_channels_created
            + self.stage_channels_created
            + self.forum_channels_created
        )

    @property
    def total_created(self) -> int:
        return (
            self.roles_created
            + self.categories_created
            + self.channels_created
            + self.emojis_created
            + self.stickers_created
        )

    @property
    def duration(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at

    @property
    def warning_count(self) -> int:
        return len(self.warnings) + self._warning_overflow

    @property
    def error_count(self) -> int:
        return len(self.errors) + self._error_overflow

    def warn(self, message: str) -> None:
        if len(self.warnings) < self.MAX_MESSAGES:
            self.warnings.append(message)
        else:
            self._warning_overflow += 1

    def fail(self, message: str) -> None:
        if len(self.errors) < self.MAX_MESSAGES:
            self.errors.append(message)
        else:
            self._error_overflow += 1


@dataclass(slots=True)
class Progress:
    """The current state the live display renders."""

    stage: Stage = Stage.IDLE
    detail: str = ""
    current: int = 0
    total: int = 0
    report: CloneReport = field(default_factory=CloneReport)
    # The adaptive pacer's current pause — for the "Discord is throttling" note.
    delay: float = 0.0
    throttled: bool = False

    @property
    def stage_index(self) -> int:
        try:
            return STAGE_ORDER.index(self.stage) + 1
        except ValueError:
            return len(STAGE_ORDER)

    @property
    def fraction(self) -> float:
        """Rough overall progress between 0.0 and 1.0."""
        if self.stage in END_STAGES:
            return 1.0
        if self.stage is Stage.IDLE:
            return 0.0
        completed_stages = self.stage_index - 1
        within = (self.current / self.total) if self.total else 0.0
        return min(1.0, (completed_stages + within) / len(STAGE_ORDER))
