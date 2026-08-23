"""
Konfiguration für Easy Server Cloner.

Sämtliche Einstellungen kommen aus Umgebungsvariablen bzw. aus einer lokalen
`.env`-Datei. Im Code selbst steht bewusst KEIN Token — so kann das Repository
öffentlich sein, ohne dass Zugangsdaten mitwandern.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv ist optional, aber empfohlen
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - reiner Komfortpfad
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class ConfigError(RuntimeError):
    """Wird geworfen, wenn die Konfiguration unvollständig oder ungültig ist."""


def _get_str(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _get_int(key: str, default: int | None = None) -> int | None:
    raw = _get_str(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{key}='{raw}' ist keine gültige Zahl. "
            f"Discord-IDs bestehen nur aus Ziffern."
        ) from exc


def _get_bool(key: str, default: bool = False) -> bool:
    raw = _get_str(key).lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "ja", "on"):
        return True
    if raw in ("0", "false", "no", "nein", "off"):
        return False
    raise ConfigError(
        f"{key}='{raw}' ist kein Wahrheitswert. Erlaubt sind true/false bzw. ja/nein."
    )


def _get_float(key: str, default: float) -> float:
    raw = _get_str(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}='{raw}' ist keine gültige Kommazahl.") from exc


def _get_id_set(key: str) -> set[int]:
    raw = _get_str(key)
    if not raw:
        return set()
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(
                f"{key} enthält den ungültigen Eintrag '{part}'. "
                f"Erwartet wird eine Komma-getrennte Liste von Discord-IDs."
            ) from exc
    return ids


def _get_color(key: str, default: int) -> int:
    raw = _get_str(key)
    if not raw:
        return default
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 16)
    except ValueError:
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(
                f"{key}='{raw}' ist keine gültige Farbe. Beispiel: 0x5865F2"
            ) from exc


# Discord erlaubt maximal 500 Rollen und 500 Channels pro Server.
MAX_ROLES = 250
MAX_CHANNELS = 500

# Untergrenze für die Pause zwischen API-Calls. Alles darunter provoziert
# unnötig viele 429er und macht den Klon am Ende langsamer, nicht schneller.
MIN_CLONE_DELAY = 0.25


@dataclass(slots=True)
class Config:
    """Eingelesene und validierte Laufzeit-Konfiguration."""

    token: str
    owner_ids: set[int] = field(default_factory=set)
    dev_guild_id: int | None = None
    default_source_id: int | None = None
    default_target_id: int | None = None
    clone_delay: float = 0.6
    embed_color: int = 0x5865F2
    log_level: str = "INFO"
    # Beide schalten privilegierte Intents ein, die im Developer Portal
    # zusätzlich freigeschaltet werden müssen.
    enable_message_clone: bool = False
    enable_member_roles: bool = False

    @classmethod
    def load(cls) -> "Config":
        """Liest die Konfiguration aus `.env` bzw. der Umgebung."""
        load_dotenv(ENV_FILE)

        token = _get_str("DISCORD_TOKEN") or _get_str("BOT_TOKEN")
        if not token or token.startswith("hier."):
            raise ConfigError(
                "Es ist kein Bot-Token hinterlegt.\n\n"
                "  1. Kopiere die Vorlage:  cp .env.example .env\n"
                "  2. Öffne die .env und trage bei DISCORD_TOKEN deinen Token ein\n"
                "  3. Den Token findest du unter "
                "https://discord.com/developers/applications "
                "→ dein Bot → 'Bot' → 'Reset Token'\n\n"
                "Alternativ als Umgebungsvariable:  export DISCORD_TOKEN=...",
            )

        delay = _get_float("CLONE_DELAY", 0.6)
        if delay < MIN_CLONE_DELAY:
            delay = MIN_CLONE_DELAY

        log_level = _get_str("LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level = "INFO"

        return cls(
            token=token,
            owner_ids=_get_id_set("OWNER_IDS"),
            dev_guild_id=_get_int("DEV_GUILD_ID"),
            default_source_id=_get_int("SOURCE_GUILD_ID"),
            default_target_id=_get_int("TARGET_GUILD_ID"),
            clone_delay=delay,
            embed_color=_get_color("EMBED_COLOR", 0x5865F2),
            log_level=log_level,
            enable_message_clone=_get_bool("ENABLE_MESSAGE_CLONE"),
            enable_member_roles=_get_bool("ENABLE_MEMBER_ROLES"),
        )

    def is_owner(self, user_id: int) -> bool:
        """Ist keine Owner-Liste gesetzt, gilt die Prüfung als bestanden."""
        return not self.owner_ids or user_id in self.owner_ids
