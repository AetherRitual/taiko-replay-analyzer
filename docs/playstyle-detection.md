# Playstyle Detection — Specification

> **Status:** v1 — first complete spec.
> Built from a structured conversation between the project author (KDDK player, ~3000h)
> and Claude. Sections marked `[OPEN]` are decisions still pending input.

---

## §0 Purpose

This document defines what playstyle detection means for the osu!Taiko replay analyzer, independent of any specific algorithm.

The goal: every line of code in the playstyle pipeline should trace back to a paragraph of this spec. If it doesn't, either the code is wrong or the spec is incomplete.

Two layers, specified separately:

1. **Pattern Layer** (§3) — turns the raw note + hit-event stream into a per-note structured record. No classification.
2. **Classification Layer** (§4) — consumes the pattern layer's output and produces a per-density profile + a best-fit label.

Keeping them separate matters because the pattern layer is reusable (per-BPM stats, weakness detection, pattern frequency all depend on it) while the classifier is a single consumer that may evolve independently.

---

## §1 Glossary

| Term | Definition |
|------|------------|
| **Note** | A scoreable hit object: Don, Kat, big Don, or big Kat. Rolls and spinners are not "notes" for playstyle purposes. |
| **Color** | Don (red) or Kat (blue). |
| **Big note** | A finisher requiring both same-color keys within ~30ms for the bonus. See §1a. |
| **Hand** | Which physical hand pressed a key. M2+M1 = left, K1+K2 = right, regardless of layout. |
| **Finger** | A specific key (M2 / M1 / K1 / K2). Distinct from hand. |
| **Layout** | The player's key-to-color mapping: KDDK, DDKK, or KKDD. Profile-level property. |
| **Snapping** | The beat subdivision a note lands on (1/2, 1/4, 1/6, etc.), computed from the active timing point. |
| **Density bucket** | Coarse grouping of snappings: **slow** (1/1, 1/2), **main** (1/3, 1/4), **fast** (1/6, 1/8 and finer). |
| **Stream** | A maximal run of notes with no gap exceeding `beat_length/4 + 5ms` at local BPM. Minimum length 3. |
| **Main hand** | The player's preferred starting hand for patterns. Universal trait — every player has one, even strict alters. |
| **Hand-alt rate** | Fraction of consecutive same-stream note pairs where the hand changes. |
| **Same-hand run** | A sequence of consecutive notes hit with the same hand. Length ≥2. |

### §1a [OPEN] Big note treatment

When a big note is hit with both keys (`was_hit_double = true`), how does it contribute to playstyle metrics?

Options:
- Treat as `LR` and exclude from alt-rate calculations
- Count the dominant hand only
- Treat as both hands (counts toward usage of all four keys)

Currently leaning: exclude from hand-alt rate, count toward finger-usage stats for both fired keys.

### §1b [OPEN] Non-finishers hit double

Regular notes hit with both same-color keys simultaneously (legal but no bonus). Treat as §1a, or different?

---

## §2 Input Data Model

The pattern layer consumes:

- **Note sequence** from `.osu`: ordered `(time_ms, color, is_big)`. Rolls/spinners excluded.
- **Hit events** from `.osr`: ordered `(time_ms, key_bitmask)` representing rising-edge transitions.
- **Timing points** from `.osu`: needed for beat-length / BPM lookup at any time.
- **Mods** from `.osr`: affect hit windows (HR/EZ); do not affect playstyle classification.
- **Profile layout**: KDDK / DDKK / KKDD. Profile-level, immutable per-replay.

### §2.1 Hit-event → note matching

Already implemented in `analyzer.py::_match_hits_to_notes` and assumed correct. Chronological event processing; each event consumed by the earliest unmatched note of matching color whose hit window contains the event time.

Produces a `NoteResult` per note: `(note, result, offset, hit_time, key_used, note_index)`. **This is the input to the pattern layer.**

---

## §3 The Pattern Layer

The pattern layer turns `(replay, beatmap, layout)` into a structured per-note record. No classification, no judgement.

### §3.1 The note record

For each scoreable note in the beatmap, produce:

```
NoteRecord {
  time_ms              # absolute time in song
  color                # don | kat
  is_big               # bool, finisher in chart

  snapping             # 1/1 | 1/2 | 1/3 | 1/4 | 1/6 | 1/8 | ...
  density_bucket       # slow | main | fast (derived from snapping per §3.2)

  hit_quality          # 300 | 100 | miss
  offset_ms            # signed; NaN for miss
  was_hit_double       # both same-color keys fired (regardless of is_big)

  finger               # M2 | M1 | K1 | K2 | null (miss)
  hand                 # L | R | LR | null  (derived from finger + layout per §3.4)

  in_stream            # bool
  stream_id            # int or null
  stream_position      # int, 0-indexed within stream
  is_first_in_stream   # bool
  is_last_in_stream    # bool
}
```

The note record is the atomic unit. Everything downstream operates on the note record stream.

### §3.2 Density buckets

| Bucket | Snappings | Notes |
|--------|-----------|-------|
| **slow** | 1/1, 1/2 | Singletap-typical territory |
| **main** | 1/3, 1/4 | Where most playstyle judgement happens |
| **fast** | 1/6, 1/8, 1/12, 1/16 | Bursts; often embedded inside 1/4 streams as triplets/quads |

Density is **per-note**, not per-stream. A 1/4 stream can contain 1/6 triplets — those triplet notes are tagged `fast` while surrounding notes are `main`. No smoothing, no per-stream majority vote.

### §3.3 Streams

A stream is a maximal run of notes where consecutive gaps don't exceed `beat_length / 4 + 5ms` at the local BPM (the existing rule, retained — it correctly keeps 1/6 and 1/8 bursts as part of the surrounding 1/4 stream).

**Minimum stream length: 3 notes.** Two adjacent notes is a pair, not a stream.

Notes outside any stream still appear in the note record list with `in_stream = false`. They contribute to slow-density analysis but not to alt-rate calculations.

### §3.4 Hand resolution from layout

Layout maps physical key → color. The player's hand is determined purely by physical key position (M2/M1 = left, K1/K2 = right):

| Layout | M2 | M1 | K1 | K2 |
|--------|-----|-----|-----|-----|
| KDDK   | L Kat | L Don | R Don | R Kat |
| DDKK   | L Don | L Don | R Kat | R Kat |
| KKDD   | L Kat | L Kat | R Don | R Don |

When `was_hit_double = true`, `hand = LR`.

### §3.5 Layout inference

If a profile has no declared layout, infer from confidently-classified hits in the replay by examining finger-color pairings. Specifics: TBD — pattern reader exposes an `infer_layout(records)` helper that profile creation calls when needed. Default to KDDK with a warning if inference is inconclusive.

### §3.6 What the pattern layer does NOT do

- Does not classify playstyle
- Does not compute aggregate metrics (alt rate, etc.)
- Does not detect "rolls" or "alt breaks"
- Does not judge whether a note was played well

All downstream concerns. The pattern layer produces a faithful per-note record, period.

---

## §4 The Classification Layer

### §4.1 Core principle

Playstyle is not a category. It's a per-density behavioral profile. Labels ("Full-Alt KDDK," "Roll player," etc.) are shorthand for *where in a multidimensional space* the player sits — not discrete buckets.

The classifier produces:

1. **A profile** — per-density measurements describing what the player did.
2. **A label** — derived summary string for badges and quick reference.

The profile is the truth; the label is convenience.

### §4.2 The profile

For each density bucket independently:

```
DensityProfile {
  note_count
  pair_count          # adjacent same-stream pairs
  hand_alt_rate       # % of pair_count that alternated
  finger_usage        # {M2: 0.10, M1: 0.45, K1: 0.40, K2: 0.05}
  same_hand_runs      # histogram: {2: N, 3: N, 4: N, 5+: N}
  starting_hand_bias  # % of streams starting on L vs R
}
```

Replay-level:

```
PlaystyleProfile {
  layout                    # KDDK | DDKK | KKDD
  main_hand                 # L | R | balanced
  by_density: {slow, main, fast}
  cross_stream_continuity   # % of stream→stream transitions maintaining hand alt
                            # measured only on streams ≥4 notes
  unused_keys               # fingers with <5% usage at any density
}
```

### §4.3 Labels

Format: `"{layout} {main_density_style}, {modifiers}"`.

Examples this should produce:

| Profile shape | Label |
|---------------|-------|
| Alts cleanly at main density, all 4 keys used, balanced hands | `KDDK Alt` |
| Same as above + slow bucket uses only K1+K2 | `KDDK Alt, right-hand singletap on slow` |
| All 4 keys used at main density but low hand-alt rate, long same-hand runs | `KDDK Roll` |
| ≤2 keys used at main density | `KDDK Singletap` |
| Finger-alts within color, hand-alt across color | `DDKK Alt` |
| Alt at main density, low alt rate at fast density | `KDDK Alt, rolls fast bursts` |
| Good within-stream alt, poor cross-stream continuity | `KDDK Alt, resets at stream starts` |

**Main label component** (drives the primary style word):

| Condition (at main density bucket) | Component |
|-------------------------------------|-----------|
| Hand-alt rate ≥ ALT_THRESHOLD AND all 4 keys ≥ MIN_KEY_USAGE | **Alt** |
| ≥1 of 4 keys < MIN_KEY_USAGE | **Singletap** |
| All 4 keys ≥ MIN_KEY_USAGE AND hand-alt rate < ALT_THRESHOLD | **Roll** |

Thresholds in §4.6.

**Modifier candidates:**

- `right-hand singletap on slow` / `left-hand singletap on slow` / `M1+K2 singletap on slow` — slow bucket uses ≤2 fingers
- `rolls fast bursts` — fast bucket alt rate noticeably below main bucket
- `resets at stream starts` — cross-stream continuity below threshold despite good within-stream alt
- `left-hand-dominant` / `right-hand-dominant` — strong main-hand bias
- `mixed style` — main density profile doesn't cleanly satisfy any of Alt/Roll/Singletap

### §4.4 Worked examples

**Project author's playstyle** — KDDK, alts 1/4, singletaps 1/2 with K1+K2, prefers L start.
Profile: layout=KDDK, main_density=Alt with all 4 keys, slow=K1+K2 only, main_hand=L.
Label: `KDDK Alt, right-hand singletap on slow, left-hand-dominant`.

**Pure roll player** (kdkkdkkd → LLLRRRLL, finger seq M2-M1-M2-K2-K1-K2-M2-M1).
Profile: all 4 keys used, low hand-alt rate at main density, same-hand-run histogram dominated by length 3+.
Label: `KDDK Roll`.

**DDKK alt mistaken for KDDK roll** — the case experienced players misclassify by eye.
Discrimination: DDKK alt has highly skewed `finger_usage` per color (M1 dominant for Dons, M2 dominant for Kats). KDDK roll has roughly even M1/K1 usage on Dons, M2/K2 on Kats. **Looking at finger usage per color resolves the confusion that hand sequences alone cannot.**

**Full-alt with occasional burst rolls.**
Profile: main density alt rate high, fast density alt rate lower.
Label: `KDDK Alt, rolls fast bursts`.

**Alter who resets to main hand each stream.**
Profile: high within-stream alt rate, low cross-stream continuity.
Label: `KDDK Alt, resets at stream starts`. **No separate "Semi-Alt" category needed** — it's an Alt with a modifier.

### §4.5 What the classifier doesn't do

- **Quality judgement.** Never says one playstyle is better than another.
- **Mid-song style switching detection.** Profile aggregates over the whole map. If this becomes a real issue, add per-section profiling later.
- **Recommendations.** No "you should switch to alt at this BPM."

### §4.6 [OPEN] Numerical thresholds

Set against labeled corpus or by feel + validation:

| Threshold | Initial guess | Used for |
|-----------|--------------|----------|
| `ALT_THRESHOLD` | 0.85 | Hand-alt rate above this = Alt |
| `MIN_KEY_USAGE` | 0.05 | Below this = key is "unused" |
| `MIN_NOTES_PER_BUCKET` | 20 | Below this, skip bucket from label |
| `MIN_STREAM_FOR_CONTINUITY` | 4 | Streams shorter than this don't count for continuity |
| `CONTINUITY_THRESHOLD` | 0.7 | Below this triggers `resets at stream starts` |
| `BURST_DEGRADATION` | 0.15 | (main_alt − fast_alt) above this triggers `rolls fast bursts` |
| `MAIN_HAND_BIAS` | 0.65 | Above this triggers `*-hand-dominant` |

These are starting guesses. Validation against real replays will adjust them.

---

## §5 [REMOVED]

(Original §5 covered classifier shape choice — superseded by §4.)

---

## §6 Layout Handling

Layout is **profile-level**. Pattern reader takes it as input and resolves hand/finger correctly per §3.4. After that, the classifier is layout-agnostic — it operates on hands and fingers without caring which is which color.

### §6.1 [OPEN] Other layouts

Currently: KDDK, DDKK, KKDD. KDKD (cross-mirror) and arbitrary custom mappings exist but are rare. Not supporting them in v1.

### §6.2 Layout inference

Per §3.5: inferred from finger-color pairings on confidently-classified hits, falling back to KDDK on inconclusive data. Profile creation calls this; user can override.

---

## §7 Edge Cases

| Case | Behavior |
|------|----------|
| Map with `note_count < MIN_NOTES_PER_BUCKET` at all densities | `Unknown — insufficient data` |
| Map with no streams (all isolated notes) | Profile reports slow-density behavior only; label = `KDDK Singletap` if applicable |
| Map with only one color | Profile reports per-color stats omitted for absent color; label still possible |
| DT/HT replays | No effect on classification (player's style invariant under rate). Snappings and stream gaps computed in game-time space. |
| Replays with miss% > 30% | Still classified, but label gets `[low-quality play]` annotation since data may be unreliable |
| Extreme BPM swings within a map | Each note classified at its local BPM/snapping; aggregation happens per density bucket |

---

## §8 Pattern Layer — Other Consumers

The pattern layer is built so the classifier isn't its only client. Future consumers (some already partially implemented):

1. **Per-BPM accuracy** — group notes by BPM bucket, compute hit accuracy per bucket
2. **Pattern frequency** — count occurrences of N-gram color patterns (DKDK, DDKK, etc.)
3. **Pattern weakness** — accuracy per N-gram, identify low-accuracy patterns
4. **Stream-length comfort** — accuracy as a function of stream length
5. **Burst detection** — locate fast-density notes embedded in slower streams

The note record (§3.1) carries enough information to support all of these without re-parsing.

---

## §9 Out of Scope

- UR / accuracy / hit-window math (separate, correct as far as we know)
- Rendering / UI
- Profile aggregation across replays (separate layer that consumes both pattern and classifier output)
- Difficulty / star rating estimation

---

## §10 Open Questions

Tracked in one place:

1. §1a — Big note hand assignment
2. §1b — Non-finisher double-key hits
3. §3.5 — Layout inference algorithm specifics
4. §4.6 — All numerical thresholds (need labeled corpus or feel-based + validation)
5. §6.1 — Whether to support more layouts later

None of these block the pattern layer build. They block the classifier in §4 and can be addressed once the pattern reader is producing correct output.

---

## §11 Process

For any new addition:

1. State the behavior in plain language in this spec
2. Update the spec section
3. **Then** design the algorithm
4. **Then** write the code
5. **Then** add tests

Resist algorithm-first thinking. The previous implementation went algorithm-first and produced unreliable code that was hard to defend.
