AI slop.
polluting the environment so you can see fullalt %

# osu!Taiko Replay Analyzer

Visualize and analyze osu!Taiko replays. Detects playstyle (full-alt / singletap / hybrid), tracks BPM comfort zones, shows pattern weak spots, and builds per-player profiles across many replays.

---

## Windows

1. Install **Python 3.11+** from https://www.python.org/downloads/
   — tick **"Add Python to PATH"** during setup.

2. Double-click **`run.bat`** — it sets everything up on first launch.

That's it. Every launch after that goes straight to the app.

---

## Linux

You need: **Python 3.11+** and **pygame 2.x**

Install pygame via your package manager (`python3-pygame`) or pip (`pip3 install pygame`), then run:

```bash
python3 main.py
```

---

## Setup

### Songs folder

The app needs your osu! Songs folder (or osu! lazer `files/` directory) to load beatmaps.

It tries to detect the location automatically. If that fails, it will prompt you once — enter the path and it saves to `config.txt`.

You can also set it manually by opening `config.txt` and editing the line:

```
songs_folder = C:\Users\you\AppData\Local\osu!\Songs
```

Common locations:

**Stable (Songs folder)**
- Windows: `%LOCALAPPDATA%\osu!\Songs`
- Linux: `~/.local/share/osu-wine/osu!/Songs`

**Lazer (files directory)**
- Windows: `%APPDATA%\osu\files`
- Linux: `~/.local/share/osu/files`

### Replays

To open a single replay, **put the `.osr` file in the same folder as the app** and run.
If there is only one `.osr` file it opens automatically — if there are multiple you will be asked to pick one.

To import many replays at once into profiles, use `--mass-add` (see below).

---

## Usage

```
python3 main.py [replay.osr] [options]
```

**Windows:** replace `python3` with `python`, or just use `run.bat`.

**Options**

- *(no arguments)* — auto-detects a single `.osr` in the current folder
- `replay.osr` — open a specific replay file
- `--songs <path>` — override the Songs / lazer files folder for this run
- `--portable` — run without a Songs folder (playstyle analysis only, no beatmap data)
- `--profile` — open the profile viewer
- `--profile "Name"` — open a specific player's profile directly
- `--create-profile "Name"` — create a new player profile
- `--create-profile "Name" --layout DDKK` — create profile with a specific key layout
- `--create-profile "Name" --aliases "Alt,Old"` — create profile with username aliases
- `--mass-add` — scan a folder of replays and import them into matching profiles
- `--delete-profile "Name"` — delete a player profile

---

## Profiles

Profiles store replay history and aggregate stats across sessions.

```bash
# Create
python3 main.py --create-profile "PlayerName"
python3 main.py --create-profile "PlayerName" --layout DDKK

# Import many replays at once
python3 main.py --mass-add

# Delete
python3 main.py --delete-profile "PlayerName"
```

Saved profiles are listed in `config.txt`. Profile data is stored at:
- Windows: `%APPDATA%\taiko-replay-analyzer\profiles\`
- Linux: `~/.local/share/taiko-replay-analyzer/profiles\`

---

## Key Layouts

- **KDDK** — Kat · Don · Don · Kat *(default)*
- **DDKK** — Don · Don · Kat · Kat
- **KKDD** — Kat · Kat · Don · Don

Layout is set when creating a profile. Press **[L]** in the Profile Viewer → Playstyle tab to change it at any time.

---

## Controls

**Replay Viewer**
- `Space` — play / pause
- `← →` — seek ±5 seconds
- `Shift + ← →` — seek ±30 seconds
- `D` — toggle data panel
- `P` — open player profile
- `Esc` — quit

**Profile Viewer**
- `Tab` — cycle tabs (Overview / Patterns / Playstyle)
- `↑ ↓` — scroll
- `L` — cycle key layout
- `Enter` — open selected replay
- `Esc` — back / quit

---

## Skin

The `skin/` folder holds the visual assets (notes, drum, hit results, lane background). These are standard osu! skin files — replace them with any skin you like. The app falls back to plain shapes if an asset is missing.
