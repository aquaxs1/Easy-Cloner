#!/usr/bin/env python3
"""
Easy Server Cloner — Startpunkt.

    pip install -r requirements.txt
    cp .env.example .env      # dann Token eintragen
    python main.py
"""

from __future__ import annotations

import logging
import sys

import discord

from easy_server_cloner import __version__, logging_setup
from easy_server_cloner.bot import CloneBot
from easy_server_cloner.config import Config, ConfigError

BANNER = r"""
  ______                     _____ _
 |  ____|                   / ____| |
 | |__   __ _ ___ _   _    | |    | | ___  _ __   ___ _ __
 |  __| / _` / __| | | |   | |    | |/ _ \| '_ \ / _ \ '__|
 | |___| (_| \__ \ |_| |   | |____| | (_) | | | |  __/ |
 |______\__,_|___/\__, |    \_____|_|\___/|_| |_|\___|_|
                   __/ |     Easy Server Cloner v{version}
                  |___/      Discord Server Structure Cloner
"""


def main() -> int:
    print(BANNER.format(version=__version__))

    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"\n❌ Konfigurationsfehler\n\n{exc}\n", file=sys.stderr)
        return 1

    logging_setup.configure(config.log_level)
    log = logging.getLogger("easy_server_cloner")

    if not config.owner_ids:
        log.warning(
            "OWNER_IDS ist leer — jeder Server-Administrator darf den Bot bedienen. "
            "Für den produktiven Einsatz solltest du dort deine User-ID eintragen."
        )

    bot = CloneBot(config)

    try:
        bot.run(config.token, log_handler=None)
    except discord.LoginFailure:
        log.error(
            "Login fehlgeschlagen: der Token in DISCORD_TOKEN wird von Discord "
            "abgelehnt. Erzeuge im Developer Portal einen neuen Token "
            "('Reset Token') und trage ihn in die .env ein."
        )
        return 1
    except discord.PrivilegedIntentsRequired:
        log.error(
            "Discord verlangt privilegierte Intents. Aktiviere sie im Developer "
            "Portal unter 'Bot' → 'Privileged Gateway Intents'."
        )
        return 1
    except KeyboardInterrupt:
        log.info("Beendet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
