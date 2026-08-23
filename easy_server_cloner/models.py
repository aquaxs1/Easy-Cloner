"""Datenmodelle für Klon-Optionen, Fortschritt und Ergebnisbericht."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Stage(str, Enum):
    """Die Phasen, die ein Klon-Vorgang durchläuft."""

    IDLE = "Bereit"
    VALIDATING = "Prüfe Voraussetzungen"
    READING = "Lese Quell-Server"
    WIPING = "Räume Ziel-Server auf"
    SETTINGS = "Übertrage Server-Einstellungen"
    ROLES = "Kopiere Rollen"
    CATEGORIES = "Kopiere Kategorien"
    CHANNELS = "Kopiere Channels"
    FINALIZING = "Setze Feinheiten"
    EMOJIS = "Kopiere Emojis"
    STICKERS = "Kopiere Sticker"
    MESSAGES = "Baue Nachrichten nach"
    MEMBER_ROLES = "Weise Mitgliedern Rollen zu"
    DONE = "Abgeschlossen"
    FAILED = "Fehlgeschlagen"
    CANCELLED = "Abgebrochen"


# Reihenfolge für die Fortschrittsanzeige. IDLE und die Endzustände zählen nicht.
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
    """Was genau soll kopiert werden?"""

    wipe_target: bool = True
    settings: bool = True
    icon: bool = True
    roles: bool = True
    channels: bool = True
    emojis: bool = True
    stickers: bool = True
    dry_run: bool = False

    # Gleichnamige Objekte im Ziel wiederverwenden statt doppelt anzulegen.
    # Das ist der Kern von /sync.
    skip_existing: bool = False

    # Wie viele Nachrichten je Text-Channel per Webhook nachgebaut werden.
    # 0 = aus. Braucht den privilegierten Message-Content-Intent.
    messages_per_channel: int = 0

    # Rollen an Mitglieder verteilen, die auf beiden Servern sind.
    # Braucht den privilegierten Members-Intent.
    member_roles: bool = False

    def summary(self) -> list[str]:
        """Menschenlesbare Auflistung der aktiven Optionen."""
        parts = [
            ("Server-Einstellungen", self.settings),
            ("Icon & Banner", self.icon),
            ("Rollen", self.roles),
            ("Kategorien & Channels", self.channels),
            ("Emojis", self.emojis),
            ("Sticker", self.stickers),
        ]
        lines = [f"{'✅' if on else '❌'} {label}" for label, on in parts]
        if self.messages_per_channel:
            lines.append(f"✅ Nachrichten (max. {self.messages_per_channel}/Channel)")
        if self.member_roles:
            lines.append("✅ Rollen-Zuweisungen")
        if self.skip_existing:
            lines.append("♻️ Vorhandenes wird wiederverwendet")
        return lines


@dataclass(slots=True)
class CloneReport:
    """Zählt mit, was tatsächlich passiert ist."""

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

    # Warnungen und Fehler werden gedeckelt, damit ein kaputter Server das
    # Embed nicht sprengt — die Zähler bleiben trotzdem korrekt.
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
    """Aktueller Stand, den die Live-Anzeige rendert."""

    stage: Stage = Stage.IDLE
    detail: str = ""
    current: int = 0
    total: int = 0
    report: CloneReport = field(default_factory=CloneReport)
    # Aktuelle Pause der adaptiven Taktung — für die Anzeige "Discord bremst".
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
        """Grober Gesamtfortschritt zwischen 0.0 und 1.0."""
        if self.stage in END_STAGES:
            return 1.0
        if self.stage is Stage.IDLE:
            return 0.0
        completed_stages = self.stage_index - 1
        within = (self.current / self.total) if self.total else 0.0
        return min(1.0, (completed_stages + within) / len(STAGE_ORDER))
