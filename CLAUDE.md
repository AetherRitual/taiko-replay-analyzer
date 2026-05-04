# CLAUDE.md

Context for Claude Code sessions on this repo. Read this first, every session.

## What this project is

`taiko-replay-analyzer` — a pygame-based desktop tool for analyzing osu!Taiko `.osr` replays. Parses replays + beatmaps, computes hit accuracy / UR / playstyle / pattern stats, and visualizes everything in a real-time replay viewer with a per-player profile system.

Single-developer hobby project. Python 3.11+, pygame 2.x, no other runtime deps.

## Current state

The codebase is mid-rewrite. The replay parsing, beatmap parsing, hit-window math, and UR computation are correct as far as we know. The **playstyle detection and pattern analysis are being rewritten from scratch** because the original implementation was assembled by trial-and-error threshold tuning and produces unreliable classifications, especially for non-KDDK layouts.

A formal spec for the rewrite exists at `docs/playstyle-detection.md`. **Read it before doing anything in `analyzer.py` or pattern-related code.** Every change to playstyle / pattern code must trace back to a section of the spec.

## What we are NOT doing right now

- **Not restructuring the file layout.** All modules stay flat in the repo root for now. Restructuring happens after the calculations are correct.
- **Not modifying `analyzer.py`'s existing classifier.** The new pattern reader is built alongside it. Cutover happens once the new path works end-to-end.
- **Not touching `viewer.py` or `profile_viewer.py`.** UI changes come later.
- **Not adding new features.** Fix the math first.

## Repo layout (current, flat)

```
main.py              # CLI entry point + pygame init
osr_parser.py        # .osr binary parser (correct, leave alone)
osu_parser.py        # .osu beatmap parser (correct, leave alone)
analyzer.py          # current playstyle/UR/problem analysis (BEING REPLACED)
pattern_analysis.py  # 4-note pattern stats for UI (kept; partially reusable)
profile.py           # player profile persistence (note: shadows stdlib `profile`)
profile_viewer.py    # profile UI (don't touch)
viewer.py            # replay viewer UI (don't touch)
skin.py              # skin asset loading
ui_common.py         # shared font/color constants
config.py            # config.txt key=value reader
mass_add.py          # batch profile import
run.bat              # Windows launcher
skin/                # default skin assets
docs/                # specs and architecture notes
tests/               # unit tests + fixtures (NEW — being built out)
```

## Conventions

- Python 3.11+. Use modern syntax: `X | None`, `list[int]`, etc.
- Type hints on public functions and dataclasses. Internal helpers can skip them when obvious.
- `dataclass` for data containers. `@dataclass(frozen=True)` for immutable records.
- No external deps beyond pygame for runtime. Test deps (pytest) are fine.
- Avoid local imports inside function bodies — they usually mean a module structure problem. If you hit a circular import, surface it for discussion rather than papering over it.
- Format with whatever; don't bikeshed.

## How to work in this repo

**Before writing code:** read the relevant spec section. If the spec is silent or ambiguous on what you're building, **stop and ask** — don't guess. Spec gaps are decisions that need a human, not problems to solve creatively.

**Tests first when possible.** New computational code lands with unit tests against fixtures. Pure functions that don't touch pygame should be testable without pygame.

**Small commits.** One logical change per commit. Keep them reviewable.

**Don't add config flags or modes "in case we need them later."** YAGNI hard.

**Match existing style.** When editing a file, look at how similar things are done elsewhere first.

## Domain notes (important for playstyle work)

- **Layout** = the player's key mapping. Three layouts supported: KDDK (default), DDKK, KKDD. The keys M2/M1/K1/K2 are physical positions left-to-right; the layout determines which keys play which color.
- **Hand** = which physical hand pressed a key. M2+M1 = left, K1+K2 = right, regardless of layout.
- **Finger** = the specific key pressed (one of M2/M1/K1/K2). Distinct from hand.
- **Snapping** = the beat subdivision a note lands on (1/2, 1/4, 1/6, etc.).
- **Density bucket** = grouped snappings: slow (1/1, 1/2), main (1/3, 1/4), fast (1/6+).
- **Stream** = a maximal run of notes with no gap larger than `beat_length/4 + 5ms`. Minimum length 3 notes.

The existing code has bugs around DDKK/KKDD layouts because it hardcodes KDDK assumptions in hand resolution. The rewrite fixes this by making layout an explicit input to the pattern reader.

## The spec is the source of truth

If the spec at `docs/playstyle-detection.md` and the existing code disagree, **the spec wins** and the code is wrong. If the spec is unclear, ask the human — don't pick an interpretation.
