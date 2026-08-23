# 🧬 Easy Server Cloner

Ein Discord-Bot, der die komplette Struktur eines Servers kopiert – Rollen,
Kategorien, Channels, Berechtigungen, Emojis und Sticker. Live von Server zu Server
oder über eine Backup-Datei. Bedient wird er über Slash-Commands mit Embeds,
Sicherheitsabfrage und Live-Fortschrittsanzeige.

> Hervorgegangen aus einem einzelnen `discord_cloner.py`-Skript, das den Token im
> Code stehen hatte und beim Start sofort losgelöscht hat. Diese Version fragt vorher.

---

## 📖 Inhalt

- [Der komplette Guide](#-der-komplette-guide-von-null-bis-geklont) — von null bis geklont
- [Wo liegt der Token](#-wo-liegt-der-token--und-wie-tausche-ich-ihn-aus)
- [Befehle](#-befehle)
- [Konfiguration](#️-konfiguration)
- [Was kopiert wird](#-was-der-bot-kopiert)
- [Docker](#-docker)
- [Häufige Probleme](#-häufige-probleme)

---

# 🚀 Der komplette Guide: von null bis geklont

## Schritt 1 — Voraussetzungen

Du brauchst **Python 3.10 oder neuer**. Prüfen:

```bash
python --version      # Windows
python3 --version     # macOS / Linux
```

Kommt eine Fehlermeldung oder eine Version unter 3.10, hole dir Python von
[python.org/downloads](https://www.python.org/downloads/).

> **Windows:** Beim Installieren unbedingt **„Add Python to PATH"** ankreuzen.

Außerdem brauchst du auf **beiden** Servern — Quelle und Ziel — Administrator-Rechte.
Wenn du den Ziel-Server noch nicht hast: in Discord links unten auf **`+`** → **Server
erstellen** → **Selbst erstellen**. Ein leerer Server ist als Ziel ideal.

---

## Schritt 2 — Bot im Developer Portal anlegen

1. Öffne das [Discord Developer Portal](https://discord.com/developers/applications)
   und melde dich mit deinem Discord-Konto an
2. Oben rechts **New Application**
3. Name eingeben, z. B. `Easy Server Cloner` → Haken bei den Bedingungen → **Create**

Du landest auf der Übersichtsseite deiner Anwendung.

---

## Schritt 3 — Token erzeugen

1. Links im Menü auf **Bot**
2. Beim Feld *Token* auf **Reset Token** → **Yes, do it!**
3. Discord fragt ggf. nach deinem 2FA-Code
4. **Copy** drücken — der Token ist jetzt in der Zwischenablage

> ⚠️ **Der Token wird nur dieses eine Mal angezeigt.** Verlierst du ihn, machst du
> einfach ein neues *Reset Token*. Teile ihn mit niemandem: Wer den Token hat, hat die
> volle Kontrolle über den Bot.

Auf derselben Seite noch zwei Einstellungen:

- **Public Bot** → **ausschalten**. Sonst kann jeder deinen Cloner-Bot einladen.
- **Privileged Gateway Intents** → alles **aus lassen**. Die brauchst du nur für
  optionale Zusatzfunktionen ([siehe unten](#optional-nachrichten-und-rollen-zuweisungen)).

Änderungen mit **Save Changes** bestätigen.

---

## Schritt 4 — Bot auf deine Server einladen

1. Links im Menü **OAuth2** → Abschnitt **OAuth2 URL Generator**
2. Bei **Scopes** ankreuzen:
   - ✅ `bot`
   - ✅ `applications.commands`
3. Darunter erscheint **Bot Permissions**. Kreuze an:
   - ✅ **Administrator**
4. Ganz unten die **Generated URL** kopieren

Diese URL im Browser öffnen:

- Server auswählen → **Weiter** → **Autorisieren**
- **Das Ganze zweimal machen:** einmal für den **Quell-Server**, einmal für den **Ziel-Server**

> **Warum Administrator?** Der Bot muss Rollen mit beliebigen Rechten anlegen können.
> Discord erlaubt einem Bot nur, Rechte zu vergeben, die er selbst besitzt — mit
> weniger als Administrator kämen deine Rollen unvollständig an.

---

## Schritt 5 — Bot-Rolle nach oben schieben

Das ist der Schritt, den fast alle vergessen, und er entscheidet über das Ergebnis.

**Discord erlaubt einem Bot niemals, Rollen über seiner eigenen anzulegen.** Steht die
Bot-Rolle unten, landen *alle* kopierten Rollen darunter — die Rangfolge ist dann
platt gedrückt.

Auf dem **Ziel-Server**:

1. **Servereinstellungen** → **Rollen**
2. Die Rolle `Easy Server Cloner` mit der Maus **ganz nach oben** ziehen
3. Bestätigen

```
   Vorher                    Nachher
   ──────────                ──────────
   Admin                     🤖 Easy Server Cloner   ← ganz oben
   Moderator                 Admin
   Mitglied                  Moderator
   🤖 Easy Server Cloner     Mitglied
```

---

## Schritt 6 — Projekt installieren

```bash
git clone https://github.com/aquaxs1/Easy-Cloner.git
cd Easy-Cloner

pip install -r requirements.txt
```

<details>
<summary>Empfohlen: virtuelle Umgebung (hält dein System sauber)</summary>

```bash
python -m venv .venv

source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows

pip install -r requirements.txt
```
</details>

---

## Schritt 7 — Token eintragen

```bash
cp .env.example .env      # macOS / Linux
copy .env.example .env    # Windows
```

Öffne die neue Datei `.env` in einem Editor und ersetze die Platzhalter-Zeile:

```ini
DISCORD_TOKEN=MTIzNDU2Nzg5.GaBcDe.dein-echter-token-hier
```

Trage gleich noch deine eigene User-ID ein, damit niemand sonst den Bot bedienen kann:

```ini
OWNER_IDS=123456789012345678
```

<details>
<summary>Wie finde ich meine User-ID?</summary>

1. Discord → **Einstellungen** (Zahnrad) → **Erweitert** → **Entwicklermodus** einschalten
2. Rechtsklick auf deinen eigenen Namen → **Benutzer-ID kopieren**
</details>

Und für sofort verfügbare Befehle beim Testen noch die ID deines Ziel-Servers:

```ini
DEV_GUILD_ID=987654321098765432
```

> Ohne `DEV_GUILD_ID` registriert Discord die Slash-Commands global — das dauert
> **bis zu einer Stunde**. Mit gesetzter ID sind sie **sofort** da.
> Server-ID: Rechtsklick auf das Server-Symbol → **Server-ID kopieren**.

---

## Schritt 8 — Bot starten

```bash
python main.py
```

Wenn alles stimmt:

```
  ______                     _____ _
 |  ____|                   / ____| |
 | |__   __ _ ___ _   _    | |    | | ___  _ __   ___ _ __
 |  __| / _` / __| | | |   | |    | |/ _ \| '_ \ / _ \ '__|
 | |___| (_| \__ \ |_| |   | |____| | (_) | | | |  __/ |
 |______\__,_|___/\__, |    \_____|_|\___/|_| |_|\___|_|
                   __/ |     Easy Server Cloner v2.1.0
                  |___/      Discord Server Structure Cloner

14:32:01  INFO     easy_server_cloner.bot       Slash-Commands für Test-Server … registriert.
14:32:02  INFO     easy_server_cloner.bot       Eingeloggt als Easy Server Cloner#1234
14:32:02  INFO     easy_server_cloner.bot       Aktiv auf 2 Server(n).
```

**Das Fenster muss offen bleiben** — solange es läuft, ist der Bot online.
Zum Beenden `Strg + C`. (Dauerbetrieb: siehe [Docker](#-docker).)

---

## Schritt 9 — Erster Klon

Gehe in Discord auf einen deiner Server und tippe `/`. Die Befehle des Bots erscheinen.

### 9a. Überblick verschaffen

```
/servers
```

Zeigt alle Server, auf denen der Bot ist. 👑 heißt bereit, ⚠️ heißt fehlende Rechte.

### 9b. Erst sichern

```
/backup
```

Wähle im Feld *guild* deinen **Quell-Server** aus der Vorschlagsliste — IDs tippen
musst du nie, der Bot schlägt dir alle Server vor. Du bekommst eine Datei zurück.
**Heb sie auf.** Mit `/restore` baust du daraus jederzeit alles wieder auf.

### 9c. Trockenlauf

```
/preview     source: <Quell-Server>     target: <Ziel-Server>
```

Zeigt genau, was kopiert und was im Ziel gelöscht würde, plus eine Zeitschätzung.
**Verändert nichts.** Lies das in Ruhe durch.

### 9d. Klonen

```
/clone       source: <Quell-Server>     target: <Ziel-Server>
```

Es erscheint eine Sicherheitsabfrage mit allen Zahlen. Erst nach Klick auf
**🧬 Jetzt klonen** passiert etwas.

Danach läuft eine Live-Anzeige mit Fortschrittsbalken. **🛑 Abbrechen** hältst du den
Vorgang jederzeit an — der Zwischenstand bleibt erhalten.

### 9e. Nacharbeiten

Nach dem Klon bleiben drei Dinge, die kein Bot übernehmen kann:

1. **Rollen-Rangfolge prüfen.** Alle kopierten Rollen liegen unter der Bot-Rolle.
   Schiebe sie in **Servereinstellungen → Rollen** an die richtige Stelle.
2. **Mitglieder einladen.** Nutzer müssen selbst beitreten.
3. **Community-Funktionen aktivieren**, falls dein Quell-Server einer war —
   Regel-Channel, Onboarding, Bühnen-Channels.

**Fertig.** 🎉

---

## Optional: Nachrichten und Rollen-Zuweisungen

Zwei Zusatzfunktionen brauchen **privilegierte Intents**. Nur einschalten, wenn du sie
wirklich willst — beide machen den Vorgang deutlich langsamer.

### Nachrichten nachbauen

Stellt die letzten *n* Nachrichten je Channel per Webhook nach, mit Original-Name und
-Avatar.

> ⚠️ Das ist eine **Rekonstruktion, keine Migration.** Die Nachrichten sehen echt aus,
> stammen aber vom Webhook. Reaktionen, Threads und Bearbeitungsverläufe bleiben zurück.

**Einrichten:**

1. Developer Portal → dein Bot → **Bot** → **Message Content Intent** einschalten → **Save**
2. In der `.env`: `ENABLE_MESSAGE_CLONE=true`
3. Bot neu starten
4. `/clone messages:100`

### Rollen an Mitglieder verteilen

Gibt Mitgliedern, die auf **beiden** Servern sind, im Ziel dieselben Rollen.

1. Developer Portal → **Bot** → **Server Members Intent** einschalten → **Save**
2. In der `.env`: `ENABLE_MEMBER_ROLES=true`
3. Bot neu starten
4. `/clone member_roles:True`

> Steht ein Schalter in der `.env` auf `true`, der passende Intent im Portal aber auf
> aus, **verweigert Discord den Login komplett**. Immer beides gemeinsam umlegen.

---

# 🔑 Wo liegt der Token – und wie tausche ich ihn aus?

Der Token steht **ausschließlich in der Datei `.env`**, niemals im Python-Code:

```ini
# .env
DISCORD_TOKEN=dein.bot.token
```

Die `.env` ist über die `.gitignore` vom Repository ausgeschlossen und landet damit nie
auf GitHub. Zum Austauschen einfach die Zeile ersetzen und den Bot neu starten.

**Neuen Token erzeugen:** Developer Portal → dein Bot → **Bot** → **Reset Token**

Alternativ ohne Datei, direkt als Umgebungsvariable:

```bash
export DISCORD_TOKEN="dein.bot.token"    # Linux / macOS
$env:DISCORD_TOKEN="dein.bot.token"      # Windows PowerShell
```

> ⚠️ **Der Token aus dem alten Skript ist verbrannt.** Er stand im Klartext in der Datei
> und muss per *Reset Token* ersetzt werden.

---

# 📋 Befehle

| Befehl | Beschreibung |
|---|---|
| `/clone` | Server auf einen anderen kopieren — mit Bestätigung, Live-Fortschritt und Abbruch |
| `/sync` | Nur ergänzen, was fehlt. Löscht nichts, überschreibt keine Einstellungen |
| `/backup` | Server als Datei sichern |
| `/restore` | Backup-Datei auf einen Server anwenden |
| `/preview` | Trockenlauf: was würde kopiert, was gelöscht, wie lange dauert es |
| `/servers` | Alle Server des Bots mit IDs und Rechte-Status |
| `/status` | Laufende Vorgänge und offene Zwischenstände |
| `/help` | Übersicht im Chat |

### Optionen von `/clone`

| Option | Standard | Wirkung |
|---|---|---|
| `source` | aus `.env` | Quell-Server (Auswahlliste) |
| `target` | aus `.env` | Ziel-Server (Auswahlliste) |
| `wipe_target` | `True` | Ziel-Server vorher leeren |
| `resume` | `False` | Abgebrochenen Lauf fortsetzen statt neu beginnen |
| `roles` | `True` | Rollen mitkopieren |
| `channels` | `True` | Kategorien und Channels mitkopieren |
| `emojis` | `True` | Emojis mitkopieren |
| `stickers` | `True` | Sticker mitkopieren |
| `settings` | `True` | Name, Icon, Banner und Grundeinstellungen übernehmen |
| `messages` | `0` | Nachrichten je Channel nachbauen (braucht Message-Content-Intent) |
| `member_roles` | `False` | Rollen an gemeinsame Mitglieder verteilen (braucht Members-Intent) |

---

# ⚙️ Konfiguration

Alle Werte kommen aus der `.env` (Vorlage: `.env.example`):

| Variable | Pflicht | Standard | Bedeutung |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | – | Bot-Token |
| `OWNER_IDS` | – | leer | Komma-getrennte User-IDs, die den Bot bedienen dürfen. Leer = jeder Server-Admin |
| `DEV_GUILD_ID` | – | leer | Commands nur auf diesem Server registrieren (sofort statt bis zu 1 h) |
| `SOURCE_GUILD_ID` | – | leer | Vorbelegung für `/clone` und `/preview` |
| `TARGET_GUILD_ID` | – | leer | Vorbelegung für `/clone` und `/preview` |
| `CLONE_DELAY` | – | `0.6` | Start-Pause zwischen API-Aufrufen (Minimum 0.25); passt sich automatisch an |
| `EMBED_COLOR` | – | `0x5865F2` | Akzentfarbe der Embeds |
| `LOG_LEVEL` | – | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ENABLE_MESSAGE_CLONE` | – | `false` | Nachrichten-Nachbau (braucht Message-Content-Intent) |
| `ENABLE_MEMBER_ROLES` | – | `false` | Rollen-Zuweisung (braucht Members-Intent) |

---

# ✅ Was der Bot kopiert

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
- *Optional:* Nachrichten (Webhook-Nachbau) und Rollen-Zuweisungen

## ❌ Was technisch nicht geht

Diese Dinge kann **kein** Bot über die Discord-API kopieren:

- **Mitglieder** – Nutzer müssen selbst beitreten
- **Echte Nachrichten** – nur als Webhook-Nachbau, ohne Reaktionen und Threads
- **Bots und Webhooks** – müssen neu eingeladen bzw. angelegt werden
- **Server-Boosts, Vanity-URL, Audit-Log**
- **Rollen oberhalb der Bot-Rolle** – Discord verbietet das grundsätzlich

---

# 🐳 Docker

Für Dauerbetrieb, ohne dass ein Terminal offen bleiben muss:

```bash
cp .env.example .env      # Token eintragen
docker compose up -d
```

```bash
docker compose logs -f    # Logs ansehen
docker compose restart    # Neu starten (z. B. nach .env-Änderung)
docker compose down       # Stoppen
```

Die Ordner `backups/` und `state/` sind als Volume eingehängt und überleben jeden
Neustart. Der Container läuft als eigener Benutzer, nicht als root.

---

# 🧪 Tests

```bash
pip install pytest
python -m pytest -q
```

Abgedeckt: Rollen-Rangfolge, Overwrite-Mapping, Bitrate-Begrenzung, Fortsetzbarkeit,
Backup-Serialisierung, adaptive Taktung, Webhook-Nachbau, Rollen-Zuweisung,
Konfigurationsprüfung und die Discord-Größenlimits aller Embeds.

---

# 🗂️ Projektstruktur

```
Easy-Cloner/
├── main.py                       Startpunkt
├── easy_server_cloner/
│   ├── bot.py                    Slash-Commands, Rechteprüfung, Live-Anzeige
│   ├── blueprint.py              Server-Bauplan (Live-Server ↔ JSON)
│   ├── cloner.py                 Klon-Engine, wendet einen Bauplan an
│   ├── snapshot.py               Backup-Dateien lesen und schreiben
│   ├── state.py                  Zwischenstände für /clone resume
│   ├── pacing.py                 Adaptive Rate-Limit-Taktung
│   ├── extras.py                 Nachrichten-Nachbau, Rollen-Zuweisung
│   ├── embeds.py                 Alle Embeds an einer Stelle
│   ├── views.py                  Bestätigungs- und Abbruch-Buttons
│   ├── models.py                 Optionen, Fortschritt, Ergebnisbericht
│   ├── config.py                 .env-Auswertung und Validierung
│   └── logging_setup.py          Farbiges Konsolen-Logging
├── tests/
├── site/                         Website (statisches HTML, siehe unten)
├── vercel.json                   Liefert site/ aus, ohne Build-Schritt
├── Dockerfile · docker-compose.yml
├── .env.example
└── requirements.txt
```

**Der zentrale Gedanke:** `blueprint.py` beschreibt einen Server unabhängig davon, woher
er stammt. `cloner.py` wendet so einen Bauplan an. Dadurch sind Klonen, Sichern,
Wiederherstellen und Abgleichen **derselbe Code** — nur die Quelle des Bauplans
unterscheidet sich.

---

# 🌐 Website

Im Ordner [`site/`](site/) liegt die Projektseite: Landing Page mit klickbarer
Slideshow, Setup-Anleitung, Befehlsreferenz, FAQ und Nutzungsbedingungen. Statisches
HTML, CSS und JavaScript — kein Framework, kein Build-Schritt.

**Auf Vercel deployen:** [vercel.com/new](https://vercel.com/new) → Repository
importieren → **Root Directory unverändert lassen** → Deploy. Das `vercel.json` im
Repository-Root erledigt den Rest.

> `site` erscheint **nicht** in Vercels Root-Directory-Auswahl. Vercel listet dort nur
> Ordner mit einem erkennbaren Projekt-Manifest (`package.json` & Co.), und diese Seite
> ist reines HTML. Das ist kein Fehler — lass die Auswahl einfach auf dem Standard.

**Lokal ansehen:**

```bash
cd site && python3 -m http.server 8000
```

Details in [`site/README.md`](site/README.md).

---

# 🛟 Häufige Probleme

| Symptom | Ursache und Lösung |
|---|---|
| `Login fehlgeschlagen` | Token falsch oder zurückgesetzt → neuen Token in die `.env` |
| `PrivilegedIntentsRequired` | `ENABLE_*` in der `.env` steht auf `true`, der Intent im Developer Portal aber auf aus |
| Slash-Commands fehlen | Globale Commands brauchen bis zu 1 h. Für sofort: `DEV_GUILD_ID` setzen |
| `Dem Bot fehlen folgende Rechte` | Bot-Rolle braucht **Administrator** auf dem Ziel-Server |
| Rollen sitzen alle unten | Bot-Rolle stand zu weit unten → nach oben schieben, `/clone` erneut |
| Bühnen-Channels / Foren fehlen | Der Ziel-Server muss ein **Community-Server** sein |
| Banner wurde nicht übernommen | Banner braucht Server-Boost-Level 2 |
| Vorgang bricht mittendrin ab | `/clone resume:True` — der Zwischenstand ist gespeichert |
| Backup zu groß zum Hochladen | `/backup include_images:False` — die Vollversion liegt in `backups/` |
| „Discord bremst" in der Anzeige | Normal bei großen Servern. Der Bot drosselt selbst und läuft weiter |
| `command not found: python` | Unter macOS/Linux `python3` statt `python` verwenden |

---

# ⚖️ Nutzungshinweis

Gedacht für **eigene Server**: Backups, Staging-Umgebungen, Vorlagen. Der Bot braucht
Administrator-Rechte auf beiden Servern – ohne Zustimmung der Server-Inhaber lässt sich
damit nichts anfangen. Bitte respektiere die
[Discord-Nutzungsbedingungen](https://discord.com/terms).
