# 🧬 Easy Server Cloner

Ein Discord-Bot, der die komplette Struktur eines Servers auf einen anderen kopiert –
Rollen, Kategorien, Channels, Berechtigungen, Emojis und Sticker. Bedient wird er über
Slash-Commands mit Embeds, Sicherheitsabfrage und Live-Fortschrittsanzeige.

> Hervorgegangen aus einem einzelnen `discord_cloner.py`-Skript, das den Token im Code
> stehen hatte und beim Start sofort losgelöscht hat. Diese Version fragt vorher.

---

## ⚡ Schnellstart

```bash
git clone https://github.com/aquaxs1/Easy-Cloner.git
cd Easy-Cloner

pip install -r requirements.txt

cp .env.example .env      # dann DISCORD_TOKEN eintragen
python main.py
```

Danach im Discord:

```
/servers    → IDs von Quell- und Ziel-Server ablesen
/preview    → Trockenlauf, es wird nichts verändert
/clone      → klonen und bestätigen
```

---

## 🔑 Wo liegt der Token – und wie tausche ich ihn aus?

Der Token steht **ausschließlich in der Datei `.env`**, niemals im Python-Code:

```ini
# .env
DISCORD_TOKEN=dein.bot.token
```

Die `.env` ist über die `.gitignore` vom Repository ausgeschlossen und landet damit nie
auf GitHub. Zum Austauschen einfach die Zeile ersetzen und den Bot neu starten.

**Neuen Token erzeugen:**

1. [Discord Developer Portal](https://discord.com/developers/applications) öffnen
2. Anwendung auswählen → Reiter **Bot**
3. **Reset Token** → neuen Token kopieren
4. In die `.env` eintragen, `python main.py` neu starten

Alternativ ohne Datei, direkt als Umgebungsvariable:

```bash
export DISCORD_TOKEN="dein.bot.token"    # Linux / macOS
$env:DISCORD_TOKEN="dein.bot.token"      # Windows PowerShell
```

> ⚠️ **Der Token aus dem alten Skript ist verbrannt.** Er stand im Klartext in der Datei
> und muss im Developer Portal per *Reset Token* ersetzt werden. Ein Token, der einmal
> geteilt wurde, gibt jedem volle Kontrolle über den Bot.

---

## 🤖 Bot einrichten

1. Im [Developer Portal](https://discord.com/developers/applications) eine **New Application** anlegen
2. Reiter **Bot** → **Add Bot** → **Reset Token** → Token in die `.env`
3. Reiter **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: **Administrator**
4. Die erzeugte URL öffnen und den Bot auf **beide** Server einladen – Quelle *und* Ziel
5. Im Ziel-Server die **Bot-Rolle möglichst weit nach oben** schieben

Privilegierte Intents (Message Content, Server Members) werden **nicht** benötigt.

---

## 📋 Befehle

| Befehl | Beschreibung |
|---|---|
| `/help` | Übersicht und Schnellstart |
| `/servers` | Alle Server des Bots mit IDs und Rechte-Status |
| `/preview` | Trockenlauf: was würde kopiert, was gelöscht, wie lange dauert es |
| `/clone` | Klon-Vorgang mit Bestätigung, Live-Fortschritt und Abbruch-Knopf |
| `/status` | Zeigt laufende Vorgänge |

### Optionen von `/clone`

| Option | Standard | Wirkung |
|---|---|---|
| `source` | aus `.env` | ID des Quell-Servers |
| `target` | aus `.env` | ID des Ziel-Servers |
| `wipe_target` | `True` | Ziel-Server vorher leeren |
| `roles` | `True` | Rollen mitkopieren |
| `channels` | `True` | Kategorien und Channels mitkopieren |
| `emojis` | `True` | Emojis mitkopieren |
| `stickers` | `True` | Sticker mitkopieren |
| `settings` | `True` | Name, Icon, Banner und Grundeinstellungen übernehmen |

---

## ⚙️ Konfiguration

Alle Werte kommen aus der `.env` (siehe `.env.example`):

| Variable | Pflicht | Standard | Bedeutung |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | – | Bot-Token |
| `OWNER_IDS` | – | leer | Komma-getrennte User-IDs, die den Bot bedienen dürfen. Leer = jeder Server-Admin |
| `DEV_GUILD_ID` | – | leer | Commands nur auf diesem Server registrieren (sofort statt bis zu 1 h) |
| `SOURCE_GUILD_ID` | – | leer | Vorbelegung für `/clone` und `/preview` |
| `TARGET_GUILD_ID` | – | leer | Vorbelegung für `/clone` und `/preview` |
| `CLONE_DELAY` | – | `0.6` | Pause zwischen API-Aufrufen in Sekunden (Minimum 0.25) |
| `EMBED_COLOR` | – | `0x5865F2` | Akzentfarbe der Embeds |
| `LOG_LEVEL` | – | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## ✅ Was der Bot kopiert

- Server-Name, Icon, Banner, Splash, Beschreibung
- Verifizierungsstufe, Benachrichtigungen, Inhaltsfilter, AFK-Timeout
- Rollen inkl. Rechte, Farbe, Hoist, Erwähnbarkeit, Rollen-Icon – **in korrekter Rangfolge**
- Rechte der `@everyone`-Rolle
- Kategorien inkl. Berechtigungs-Overrides
- Text-Channels: Thema, Slowmode, NSFW, Auto-Archivierung, Ankündigungs-Modus
- Voice-Channels: Bitrate (am Limit des Ziel-Servers), Nutzerlimit, Region, Videoqualität
- Bühnen-Channels und Foren inkl. Forum-Tags *(Community-Server vorausgesetzt)*
- Channel-Berechtigungen (Rollen-Overwrites)
- AFK-Channel und System-Channel inkl. Flags
- Emojis und Sticker

## ❌ Was technisch nicht geht

Diese Dinge kann **kein** Bot über die Discord-API kopieren:

- **Nachrichten und Verläufe** – auch mit Webhooks nur als Nachbau, nie als Original
- **Mitglieder** – Nutzer müssen selbst beitreten
- **Rollen-Zuweisungen** an Mitglieder
- **Bots und Webhooks** – müssen neu eingeladen bzw. angelegt werden
- **Server-Boosts, Vanity-URL, Audit-Log**
- **Rollen oberhalb der Bot-Rolle** – Discord verbietet das grundsätzlich

---

## 🧪 Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

Die Tests decken u. a. die Rollen-Rangfolge, das Overwrite-Mapping, die
Bitrate-Begrenzung, die Konfigurationsprüfung und die Discord-Größenlimits der Embeds ab.

---

## 🗂️ Projektstruktur

```
Easy-Cloner/
├── main.py                       Startpunkt
├── easy_server_cloner/
│   ├── bot.py                    Slash-Commands, Rechteprüfung, Live-Anzeige
│   ├── cloner.py                 Klon-Engine (kennt kein Discord-UI)
│   ├── embeds.py                 Alle Embeds an einer Stelle
│   ├── views.py                  Bestätigungs- und Abbruch-Buttons
│   ├── models.py                 Optionen, Fortschritt, Ergebnisbericht
│   ├── config.py                 .env-Auswertung und Validierung
│   └── logging_setup.py          Farbiges Konsolen-Logging
├── tests/
├── .env.example
└── requirements.txt
```

---

## 🛟 Häufige Probleme

| Symptom | Ursache und Lösung |
|---|---|
| `Login fehlgeschlagen` | Token falsch oder zurückgesetzt → neuen Token in die `.env` |
| Slash-Commands fehlen | Globale Commands brauchen bis zu 1 h. Für sofortige Registrierung `DEV_GUILD_ID` setzen |
| `Dem Bot fehlen folgende Rechte` | Bot-Rolle braucht **Administrator** auf dem Ziel-Server |
| Rollen sitzen alle unten | Discord erlaubt keine Rollen über der Bot-Rolle → Bot-Rolle nach oben schieben und erneut klonen |
| Bühnen-Channels / Foren fehlen | Der Ziel-Server muss ein **Community-Server** sein |
| Banner wurde nicht übernommen | Banner braucht Server-Boost-Level 2 |
| Vorgang wirkt langsam | Discords Rate-Limits. `CLONE_DELAY` niedriger setzen geht, macht es aber oft langsamer |

---

## ⚖️ Nutzungshinweis

Gedacht für **eigene Server**: Backups, Staging-Umgebungen, Vorlagen. Der Bot braucht
Administrator-Rechte auf beiden Servern – ohne Zustimmung der Server-Inhaber lässt sich
damit nichts anfangen. Bitte respektiere die
[Discord-Nutzungsbedingungen](https://discord.com/terms).
