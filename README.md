<div align="center">

<img src="site/assets/img/logo.svg" width="96" alt="Easy Server Cloner">

# 🧬 Easy Server Cloner

**Copy a whole Discord server — and keep a backup you actually own.**

Roles, categories, channels, permission overwrites, emojis and stickers. Live
from server to server, or through a backup file. Self-hosted, so your token
never leaves your machine.

[![Website](https://img.shields.io/badge/website-easy--cloner.vercel.app-6F5BE8)](https://easy-cloner.vercel.app)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)](https://python.org)
[![discord.py](https://img.shields.io/badge/discord.py-v2-5865F2)](https://discordpy.readthedocs.io)
[![Tests](https://img.shields.io/badge/tests-93%20passing-57F287)](tests)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED)](docker-compose.yml)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

</div>

---

## Four commands

| Command | What it does |
|---|---|
| `/clone` | Copies one server onto another |
| `/sync` | Adds only what is missing — deletes nothing |
| `/backup` | Saves a server to a single file |
| `/restore` | Rebuilds a server from that file |
| `/preview` | Dry run: shows what would be copied and deleted |
| `/status` | Running jobs and saved checkpoints |
| `/servers` | Every server the bot can reach |
| `/help` | The quick start |

All four share one engine. Cloning is *read the blueprint from server A, apply
it to server B*; restoring is *read the blueprint from a file, apply it to
server B*. Same code, different source.

---

## What makes it safe

- **It asks before it deletes.** A confirmation dialog with real numbers, a
  60-second timeout, and a hard refusal if source and target are the same
  server — the one mistake that would destroy the original.
- **Only the person who ran the command can confirm it.**
- **It survives interruptions.** Every created object is written to a local
  checkpoint. A dropped connection costs seconds, not the whole run:
  `/clone resume:True` picks up exactly where it stopped.
- **Correct role hierarchy.** Roles are created top-down, because Discord
  inserts each new one at the bottom. Getting that backwards inverts your
  entire hierarchy — it was the original script's worst bug.
- **It paces itself.** discord.py absorbs rate limits internally, so you never
  see a 429 — only a call that took unusually long. The pacer watches for
  exactly that, widens the gap between calls, then speeds back up.
- **Nothing is ever overwritten silently.** Name collisions get numbered.
- **Your token stays yours.** Configuration lives in a local `.env`, git-ignored
  by default. There is no hosted instance and no telemetry.

---

## Quick start

```bash
git clone https://github.com/aquaxs1/Easy-Cloner.git
cd Easy-Cloner
pip install -r requirements.txt
cp .env.example .env      # then fill in the token
python main.py
```

Needs **Python 3.10+** and administrator rights on both servers — the one you
copy from and the one you copy to. The full walkthrough, including the Discord
Developer Portal steps, is in the [setup guide](https://easy-cloner.vercel.app/setup.html).

### Docker

```bash
docker compose up -d
```

---

## What gets copied

| | |
|---|---|
| **Server** | Name, icon, banner, splash, verification level, AFK/system/rules channels |
| **Roles** | Name, colour, permissions, hoist, mentionable, icons (boost level 2+) |
| **Channels** | Categories, text, voice, stage, forum, announcement — with topics, slowmode, NSFW flags, bitrate and user limits |
| **Permissions** | Every role overwrite, remapped onto the target's roles |
| **Extras** | Emojis and stickers, up to the target's limits |

### What cannot be copied

- **Members.** People have to join themselves — no tool can move them, and
  anything claiming otherwise is lying to you.
- **Messages** can only be *re-staged*, not migrated: the last N per channel are
  recreated through a webhook with the original name and avatar. Reactions,
  threads, edit history and the real authors stay behind.
- **Bots and webhooks** have to be re-invited or recreated.
- **Member-specific overwrites** are skipped — those users need not exist in the
  target.

---

## Optional extras

Both need privileged intents, enabled in the Developer Portal **and** in `.env`:

| Feature | `.env` | Portal intent |
|---|---|---|
| Message re-staging | `ENABLE_MESSAGE_CLONE=true` | Message Content |
| Role assignment | `ENABLE_MEMBER_ROLES=true` | Server Members |

---

## Layout

```
easy_server_cloner/
  bot.py          Slash commands, permission checks, live progress display
  cloner.py       The engine: applies a blueprint to a target server
  blueprint.py    Reads a server (or a file) into a portable structure
  snapshot.py     Backup files — JSON, gzip-compressed past a threshold
  state.py        Checkpoints, so an interrupted run can resume
  pacing.py       Adaptive pacing against Discord's rate limits
  embeds.py       Every embed the bot sends
  extras.py       Message re-staging and role assignment
  models.py       Clone options, progress, result report
  config.py       Environment configuration and validation
  views.py        Confirmation prompt and cancel button
tests/            93 tests
site/             Static landing page, setup guide and FAQ
```

---

## Links

- **Website** — <https://easy-cloner.vercel.app>
- **Setup guide** — <https://easy-cloner.vercel.app/setup.html>
- **FAQ & troubleshooting** — <https://easy-cloner.vercel.app/faq.html>
- **Terms of Use** — <https://easy-cloner.vercel.app/terms.html>
