"""
Configuration for Easy Server Cloner.

Every setting comes from environment variables or a local `.env` file. There
is deliberately NO token in the code itself, so the repository can be public
without credentials travelling with it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is optional but recommended
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - reiner Komfortpfad
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class ConfigError(RuntimeError):
    """Raised when the configuration is incomplete or invalid."""


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
            f"{key}='{raw}' is not a valid number. "
            f"Discord IDs consist of digits only."
        ) from exc


def _get_bool(key: str, default: bool = False) -> bool:
    raw = _get_str(key).lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(
        f"{key}='{raw}' is not a boolean. Allowed: true/false or yes/no."
    )


def _get_float(key: str, default: float) -> float:
    raw = _get_str(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}='{raw}' is not a valid decimal number.") from exc


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
                f"{key} contains the invalid entry '{part}'. "
                f"A comma-separated list of Discord IDs is expected."
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
                f"{key}='{raw}' is not a valid colour. Example: 0x5865F2"
            ) from exc


# Discord allows at most 500 roles and 500 channels per server.
MAX_ROLES = 250
MAX_CHANNELS = 500

# Lower bound for the pause between API calls. Anything below provokes
# needless 429s and makes the clone slower overall, not faster.
MIN_CLONE_DELAY = 0.25


@dataclass(slots=True)
class Config:
    """The parsed and validated runtime configuration."""

    token: str
    owner_ids: set[int] = field(default_factory=set)
    dev_guild_id: int | None = None
    default_source_id: int | None = None
    default_target_id: int | None = None
    clone_delay: float = 0.6
    embed_color: int = 0x5865F2
    log_level: str = "INFO"
    # Both enable privileged intents that also have to be switched on in the
    # Developer Portal.
    enable_message_clone: bool = False
    enable_member_roles: bool = False

    @classmethod
    def load(cls) -> "Config":
        """Reads the configuration from `.env` or the environment."""
        load_dotenv(ENV_FILE)

        token = _get_str("DISCORD_TOKEN") or _get_str("BOT_TOKEN")
        if not token or token.startswith("put."):
            raise ConfigError(
                "No bot token is configured.\n\n"
                "  1. Copy the template:  cp .env.example .env\n"
                "  2. Open .env and put your token in DISCORD_TOKEN\n"
                "  3. You will find the token under "
                "https://discord.com/developers/applications "
                "→ your bot → 'Bot' → 'Reset Token'\n\n"
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
        """With no owner list configured, the check passes."""
        return not self.owner_ids or user_id in self.owner_ids
