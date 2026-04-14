"""Analyze a Taiko replay: match hits to notes, compute stats, detect playstyle issues."""
import bisect
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

from osr_parser import OsrReplay, HitEvent, KEY_M1, KEY_M2, KEY_K1, KEY_K2, DON_MASK, KAT_MASK
from osu_parser import BeatmapInfo, HitObject, NOTE_DON, NOTE_KAT, NOTE_ROLL, NOTE_SPIN

# ---------------------------------------------------------------------------
# Hit results
# ---------------------------------------------------------------------------

HIT_300  = "300"
HIT_100  = "100"
HIT_MISS = "miss"

# Big note threshold (both same-type keys within this ms of each other)
BIG_NOTE_WINDOW = 30


@dataclass
class NoteResult:
    note: HitObject
    result: str            # HIT_300, HIT_100, HIT_MISS
    offset: float          # hit_time - note_time in ms (+ = late, - = early); NaN for miss
    hit_time: int          # actual hit time; -1 for miss
    key_used: int          # new_keys bitmask of the triggering hit event; 0 for miss
    note_index: int        # index in the hit_objects list

    @property
    def is_miss(self):
        return self.result == HIT_MISS

    @property
    def is_good(self):
        return self.result == HIT_100

    @property
    def is_great(self):
        return self.result == HIT_300


@dataclass
class PlaystyleInfo:
    name: str              # "Full-Alt", "Semi-Alt", "Singletap", "Roll", "Unknown"
    alt_rate: float        # fraction of same-type consecutive notes that alternate hands
    left_bias: float       # fraction of all hits using left hand (0.5 = balanced)
    double_taps: List[int] # timestamps where alt player double-tapped
    layout: str            # "KDDK", "DDKK", "KDKD", "Unknown"
    notes: str             # human-readable summary
    don_finger_alt: float = 0.0   # D-D pairs where M1↔K1 alternate (useful for DDKK)
    kat_finger_alt: float = 0.0   # K-K pairs where M2↔K2 alternate (useful for DDKK)
    primary_don: str = "M1"       # "M1" or "K1" — which Don key is used more
    primary_kat: str = "K2"       # "M2" or "K2" — which Kat key is used more
    phrase_start_alt_rate: float = 0.5        # Full-Alt % = min(L_rate, R_rate)
    phrase_alt_L_rate: float = 0.5            # correct when expected hand = L
    phrase_alt_R_rate: float = 0.5            # correct when expected hand = R
    phrase_alt_best: float = 0.5              # best rolling-window Full-Alt %
    phrase_alt_worst: float = 0.5             # worst rolling-window Full-Alt %
    phrase_alt_sections: List[float] = field(default_factory=list)  # downsampled series for sparkline
    phrase_count: int = 0                     # number of detected phrases (reliability indicator)
    bridge_alt_rate: float = 0.5              # alternation rate on single notes bridging two phrases
    bridge_pair_count: int = 0                # number of bridge transitions sampled


@dataclass
class ProblemSection:
    start_ms: int
    end_ms: int
    kind: str              # "miss_cluster", "high_ur", "timing_drift", "double_tap", "alt_break"
    description: str
    severity: float        # 0-1


@dataclass
class AnalysisResult:
    note_results: List[NoteResult]
    ur: float                       # unstable rate (std_dev * 10), frame-resolution
    ur_corrected: float             # UR with frame-quantization noise removed (estimate)
    mean_offset: float              # average offset of 300s in ms
    early_rate: float               # fraction of 300s that are early
    local_ur: List[Tuple[int, float]]  # (note_index, running_ur over last 30 hits)
    playstyle: PlaystyleInfo
    problems: List[ProblemSection]
    section_stats: List[Dict]       # per-section breakdown


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _is_don_key(k: int) -> bool:
    return bool(k & DON_MASK)   # M1 or K1

def _is_kat_key(k: int) -> bool:
    return bool(k & KAT_MASK)   # M2 or K2

def _key_side(k: int) -> str:
    """
    'L', 'R', or 'LR' for big notes.
    Layout (KDDK-family, left → right):
      Left side  = M2 (outer-left Kat) | M1 (inner-left Don)
      Right side = K1 (inner-right Don) | K2 (outer-right Kat)
    """
    left  = bool(k & (KEY_M1 | KEY_M2))
    right = bool(k & (KEY_K1 | KEY_K2))
    if left and right:
        return "LR"
    if left:
        return "L"
    return "R"

def _key_type(k: int) -> str:
    """'don', 'kat', 'mixed', or 'big_don'/'big_kat'."""
    has_don = bool(k & DON_MASK)
    has_kat = bool(k & KAT_MASK)
    if has_don and has_kat:
        return "mixed"
    if has_don:
        both = bool(k & KEY_M1) and bool(k & KEY_K1)
        return "big_don" if both else "don"
    if has_kat:
        both = bool(k & KEY_M2) and bool(k & KEY_K2)
        return "big_kat" if both else "kat"
    return "none"


def _filter_gameplay_hits(events: List[HitEvent]) -> List[HitEvent]:
    """Keep only hit events at t>=0 (actual gameplay, not warm-up)."""
    # Also merge near-simultaneous same-type presses for big note detection
    gameplay = [e for e in events if e.t >= -200]  # small margin for early starts
    return gameplay


def _match_hits_to_notes(events: List[HitEvent], beatmap: BeatmapInfo,
                         mods: int = 0) -> List[NoteResult]:
    """
    Match hit events to notes the way osu! does: process events in chronological
    order and assign each one to the closest unmatched note of the correct type
    within the hit window.

    Processing note-first (old approach) caused "event stealing" — an early note
    would consume an event at the edge of its window that was a far better match
    for the next note, producing phantom misses on 0-miss replays.
    """
    great_ms, good_ms = beatmap.hit_windows(mods)

    notes = [n for n in beatmap.hit_objects if n.kind in (NOTE_DON, NOTE_KAT)]
    # matched_ev[i] = (event, offset) for note i, or None
    matched_ev: List[Optional[tuple]] = [None] * len(notes)

    # Build per-type sorted index for fast range lookup
    don_indices = [i for i, n in enumerate(notes) if n.kind == NOTE_DON]
    kat_indices = [i for i, n in enumerate(notes) if n.kind == NOTE_KAT]
    don_times   = [notes[i].time for i in don_indices]
    kat_times   = [notes[i].time for i in kat_indices]

    for ev in events:  # events are already in chronological order
        kt = _key_type(ev.new_keys)
        if kt in ("don", "big_don"):
            idx_list, times_list = don_indices, don_times
        elif kt in ("kat", "big_kat"):
            idx_list, times_list = kat_indices, kat_times
        else:
            continue

        # Binary-search for notes whose window contains ev.t
        # Assign to the EARLIEST unmatched note whose hit window is still open —
        # this matches osu!'s real-time engine: hits go to the current (oldest) active note,
        # not the geometrically closest one.
        lo = bisect.bisect_left(times_list, ev.t - good_ms)
        hi = bisect.bisect_right(times_list, ev.t + good_ms)

        for pos in range(lo, hi):
            ni = idx_list[pos]
            if matched_ev[ni] is not None:
                continue
            # First eligible note wins (earliest in time)
            matched_ev[ni] = (ev, ev.t - notes[ni].time)
            break

    # Build results list in note order
    results = []
    for idx, note in enumerate(notes):
        m = matched_ev[idx]
        if m is not None:
            ev, offset = m
            if abs(offset) <= great_ms:
                result = HIT_300
            else:
                result = HIT_100
            results.append(NoteResult(note, result, offset, ev.t, ev.new_keys, idx))
        else:
            results.append(NoteResult(note, HIT_MISS, float("nan"), -1, 0, idx))

    return results


def _compute_ur(results: List[NoteResult]) -> Tuple[float, float]:
    """Return (ur, mean_offset) from all hit 300s."""
    offsets = [r.offset for r in results if r.result == HIT_300 and not math.isnan(r.offset)]
    if len(offsets) < 2:
        return 0.0, 0.0
    mean = sum(offsets) / len(offsets)
    variance = sum((x - mean) ** 2 for x in offsets) / len(offsets)
    return math.sqrt(variance) * 10.0, mean


def _compute_local_ur(results: List[NoteResult], window: int = 30) -> List[Tuple[int, float]]:
    """Running UR over a sliding window of `window` 300 hits."""
    great_offsets = [(r.note_index, r.offset) for r in results
                     if r.result == HIT_300 and not math.isnan(r.offset)]
    if len(great_offsets) < 2:
        return []

    local_ur = []
    for i in range(len(great_offsets)):
        start = max(0, i - window + 1)
        window_data = [o for _, o in great_offsets[start:i + 1]]
        if len(window_data) < 2:
            local_ur.append((great_offsets[i][0], 0.0))
            continue
        mean = sum(window_data) / len(window_data)
        var = sum((x - mean) ** 2 for x in window_data) / len(window_data)
        local_ur.append((great_offsets[i][0], math.sqrt(var) * 10.0))
    return local_ur


# ---------------------------------------------------------------------------
# Phrase detection helpers
# ---------------------------------------------------------------------------

def _stream_gap_threshold(beatmap, time_ms: float) -> float:
    """
    Max gap (ms) between consecutive notes to still be in the same stream/phrase.
    Uses beat_len/4 + 5ms (the discord suggestion) at the current BPM.
    Falls back to 150ms in portable mode (no beatmap timing data).
    """
    bpm = beatmap.bpm_at(time_ms)
    if bpm > 0:
        return 60000.0 / bpm / 4.0 + 5.0
    return 150.0   # ~100 BPM 1/4 note — portable fallback


def _split_into_phrases(notes, beatmap, min_phrase_notes: int = 2):
    """
    Split a sorted list of NoteResults into phrases.
    A new phrase begins whenever the gap to the next note exceeds
    beat_len/4 + 5ms at the current BPM.
    """
    if not notes:
        return []
    phrases = []
    current = [notes[0]]
    for i in range(1, len(notes)):
        gap = notes[i].note.time - notes[i - 1].note.time
        threshold = _stream_gap_threshold(beatmap, notes[i - 1].note.time)
        if gap > threshold:
            if len(current) >= min_phrase_notes:
                phrases.append(current)
            current = [notes[i]]
        else:
            current.append(notes[i])
    if len(current) >= min_phrase_notes:
        phrases.append(current)
    return phrases


# ---------------------------------------------------------------------------
# Playstyle detection — helpers
# ---------------------------------------------------------------------------

def _count_sides(all_note_hits: List[NoteResult]) -> Tuple[int, int]:
    """Count individual L and R hits (big notes excluded — they don't bias a side)."""
    left_count = right_count = 0
    for r in all_note_hits:
        s = _key_side(r.key_used)
        if s == "L":   left_count  += 1
        elif s == "R": right_count += 1
    return left_count, right_count


def _compute_global_alt(phrases) -> Tuple[float, int, int, List[int]]:
    """
    L-R alternation rate across all within-phrase note pairs.
    Returns (alt_rate, alts, pairs, double_tap_times).
    """
    alts = pairs = 0
    double_taps: List[int] = []
    for phrase in phrases:
        for i in range(1, len(phrase)):
            ps = _key_side(phrase[i - 1].key_used)
            cs = _key_side(phrase[i].key_used)
            if ps == "LR" or cs == "LR":
                continue
            pairs += 1
            if ps != cs:
                alts += 1
            elif phrase[i].note.time - phrase[i - 1].note.time < 250:
                double_taps.append(phrase[i].hit_time)
    return alts / max(1, pairs), alts, pairs, double_taps


def _compute_don_stream_alt(phrases) -> Tuple[float, int, int]:
    """
    Alternation rate on adjacent D-D pairs within phrases.
    Returns (alt_rate, alts, total).
    """
    alts = total = 0
    for phrase in phrases:
        prev_was_don = False
        prev_don     = None
        for r in phrase:
            is_don = r.note.kind == NOTE_DON and _is_don_key(r.key_used)
            if is_don:
                if prev_was_don and prev_don is not None:
                    ps = _key_side(prev_don.key_used)
                    cs = _key_side(r.key_used)
                    if ps not in ("LR",) and cs not in ("LR",):
                        total += 1
                        if ps != cs:
                            alts += 1
                prev_don     = r
                prev_was_don = True
            else:
                prev_was_don = False   # Kat breaks Don-Don adjacency
    return alts / max(1, total), alts, total


def _compute_phrase_alt_rates(phrases, all_note_hits, beatmap) -> Tuple:
    """
    Cross-phrase starting-hand alternation.

    For each phrase, look up the last note played before it (within 2 measures).
    Split by EXPECTED hand (L or R) and measure accuracy in each bucket separately.
    This removes the structural bias from even/odd phrase lengths:
      - A true full-alt player scores high on BOTH buckets
      - A resetter scores ~100% on their preferred hand and ~0% on the other

    Returns (L_rate, R_rate, pair_results, enough_phrases)
    where L_rate/R_rate are None if fewer than 4 samples in that bucket.
    """
    exp_L_total = exp_L_correct = 0
    exp_R_total = exp_R_correct = 0
    pair_results: List[int] = []   # 1/0 per phrase (for rolling window/sparkline)

    for phrase in phrases:
        if len(phrase) < 4:
            continue
        first_hand = None
        for r in phrase:
            s = _key_side(r.key_used)
            if s != "LR":
                first_hand = s
                break
        if first_hand is None:
            continue
        phrase_start_time = phrase[0].note.time
        bpm_here = beatmap.bpm_at(phrase_start_time)
        max_gap  = max(2000.0, 60000.0 / bpm_here * 8) if bpm_here > 0 else 4000.0
        last_before_hand = None
        for r in reversed(all_note_hits):
            if r.note.time >= phrase_start_time:
                continue
            if phrase_start_time - r.note.time > max_gap:
                break
            s = _key_side(r.key_used)
            if s != "LR":
                last_before_hand = s
                break
        if last_before_hand is None:
            continue
        expected = "R" if last_before_hand == "L" else "L"
        correct  = (first_hand == expected)
        pair_results.append(1 if correct else 0)
        if expected == "L":
            exp_L_total += 1
            if correct: exp_L_correct += 1
        else:
            exp_R_total += 1
            if correct: exp_R_correct += 1

    L_rate = exp_L_correct / exp_L_total if exp_L_total >= 4 else None
    R_rate = exp_R_correct / exp_R_total if exp_R_total >= 4 else None
    enough_phrases = (exp_L_total >= 4 and exp_R_total >= 4)
    return L_rate, R_rate, pair_results, enough_phrases


def _rolling_phrase_alt(pair_results: List[int], window: int = 10) -> Tuple[float, float, List[float]]:
    """
    Compute rolling-window Full-Alt % from per-phrase correct/incorrect data.
    Returns (best, worst, sections_for_sparkline).
    """
    if len(pair_results) >= window:
        rolling = [
            sum(pair_results[i:i + window]) / window
            for i in range(len(pair_results) - window + 1)
        ]
        step = max(1, len(rolling) // 30)
        return max(rolling), min(rolling), rolling[::step]
    if pair_results:
        avg = sum(pair_results) / len(pair_results)
        return avg, avg, [avg]
    return 0.5, 0.5, []


def _compute_bridge_alt(all_note_hits: List[NoteResult], beatmap) -> Tuple[float, int]:
    """
    Alternation rate on bridge notes — single notes between two phrases.
    Both surrounding gaps must exceed the stream threshold but be under 1.5 beats
    (otherwise it's a full rest, not a bridge).
    Returns (bridge_alt_rate, bridge_pair_count).
    """
    alts = total = 0
    for i in range(1, len(all_note_hits) - 1):
        t_prev = all_note_hits[i - 1].note.time
        t_cur  = all_note_hits[i].note.time
        t_next = all_note_hits[i + 1].note.time
        gap_before = t_cur  - t_prev
        gap_after  = t_next - t_cur
        bpm = beatmap.bpm_at(t_cur)
        beat_len = 60000.0 / bpm if bpm > 0 else 500.0
        thresh   = beat_len / 4.0 + 5.0
        if not (gap_before > thresh and gap_after > thresh):
            continue
        if gap_before > beat_len * 1.5 or gap_after > beat_len * 1.5:
            continue
        ps = _key_side(all_note_hits[i - 1].key_used)
        cs = _key_side(all_note_hits[i].key_used)
        ns = _key_side(all_note_hits[i + 1].key_used)
        if ps != "LR" and cs != "LR":  # A→B
            total += 1
            if ps != cs: alts += 1
        if cs != "LR" and ns != "LR":  # B→C
            total += 1
            if cs != ns: alts += 1
    return alts / max(1, total), total


def _compute_finger_alt(phrases) -> Tuple[float, float]:
    """
    DDKK-compatible metrics: finger alternation within Don/Kat note type pairs.
    Returns (don_finger_alt, kat_finger_alt).
    """
    dd_alts = dd_pairs = kk_alts = kk_pairs = 0
    for phrase in phrases:
        for i in range(1, len(phrase)):
            cur  = phrase[i]
            prev = phrase[i - 1]
            if (cur.note.kind == NOTE_DON and _is_don_key(cur.key_used) and
                    prev.note.kind == NOTE_DON and _is_don_key(prev.key_used)):
                dd_pairs += 1
                if (cur.key_used & (KEY_M1 | KEY_K1)) != (prev.key_used & (KEY_M1 | KEY_K1)):
                    dd_alts += 1
            elif (cur.note.kind == NOTE_KAT and _is_kat_key(cur.key_used) and
                  prev.note.kind == NOTE_KAT and _is_kat_key(prev.key_used)):
                kk_pairs += 1
                if (cur.key_used & (KEY_M2 | KEY_K2)) != (prev.key_used & (KEY_M2 | KEY_K2)):
                    kk_alts += 1
    return dd_alts / max(1, dd_pairs), kk_alts / max(1, kk_pairs)


def _compute_kat_alt(phrases) -> float:
    """K-K alternation rate within phrases (display metric)."""
    alts = pairs = 0
    for phrase in phrases:
        kat_phrase = [r for r in phrase
                      if r.note.kind == NOTE_KAT and _is_kat_key(r.key_used)]
        for i in range(1, len(kat_phrase)):
            ps = _key_side(kat_phrase[i - 1].key_used)
            cs = _key_side(kat_phrase[i].key_used)
            if ps != "LR" and cs != "LR":
                pairs += 1
                if ps != cs: alts += 1
    return alts / max(1, pairs)


def _classify_playstyle(global_alt_rate, don_stream_rate, don_stream_tot,
                        don_balance, enough_phrases, phrase_start_alt_rate,
                        kat_alt_rate) -> Tuple[str, str]:
    """
    Map numeric metrics to a playstyle name and human-readable notes string.

    Signals used:
      global_alt_rate       – L-R rhythm across consecutive notes within phrases
      don_stream_rate       – alternation on adjacent D-D pairs within phrases
      don_balance           – how evenly both Don keys are used
      phrase_start_alt_rate – do consecutive phrases start on alternating hands?
    """
    balanced = don_balance >= 0.25
    is_full_alt = (don_stream_rate >= 0.88 and global_alt_rate >= 0.80 and balanced
                   and (not enough_phrases or phrase_start_alt_rate >= 0.65))
    is_roll     = (don_stream_rate >= 0.75 and global_alt_rate < 0.75
                   and don_stream_tot >= 20)

    if is_full_alt:
        name  = "Full-Alt"
        notes = f"Alternates {global_alt_rate*100:.0f}% globally."
    elif is_roll:
        name  = "Roll"
        notes = f"Rolling — alts Don streams but global rhythm only {global_alt_rate*100:.0f}%."
    elif global_alt_rate >= 0.52 or don_stream_rate >= 0.52:
        name = "Semi-Alt"
        if enough_phrases and phrase_start_alt_rate < 0.4:
            notes = f"Alternates {global_alt_rate*100:.0f}% within phrases but resets at phrase starts."
        else:
            notes = f"Alternates {global_alt_rate*100:.0f}% — mixes alt with singletap."
    elif global_alt_rate >= 0.25 or don_stream_rate >= 0.25:
        name  = "Singletap"
        notes = f"Alternates {global_alt_rate*100:.0f}% globally — mostly singletapping."
    else:
        name  = "Roll"
        notes = f"Very low alternation ({global_alt_rate*100:.0f}%) — rolling or no pattern."

    notes += f"  Kat: {kat_alt_rate*100:.0f}%.  Don balance: {don_balance*100:.0f}%."
    return name, notes


# ---------------------------------------------------------------------------
# Playstyle detection — main entry
# ---------------------------------------------------------------------------

def _detect_playstyle(results: List[NoteResult], beatmap: BeatmapInfo) -> PlaystyleInfo:
    """Analyze the key sequence to determine playstyle."""
    hits = [r for r in results if not r.is_miss and r.key_used != 0]
    if len(hits) < 10:
        return PlaystyleInfo("Unknown", 0, 0.5, [], "Unknown", "Not enough data")

    m1_don = sum(1 for r in hits if r.note.kind == NOTE_DON and (r.key_used & KEY_M1))
    k1_don = sum(1 for r in hits if r.note.kind == NOTE_DON and (r.key_used & KEY_K1))
    m2_kat = sum(1 for r in hits if r.note.kind == NOTE_KAT and (r.key_used & KEY_M2))
    k2_kat = sum(1 for r in hits if r.note.kind == NOTE_KAT and (r.key_used & KEY_K2))

    all_note_hits = sorted(
        [r for r in hits if r.note.kind in (NOTE_DON, NOTE_KAT)
         and (_is_don_key(r.key_used) or _is_kat_key(r.key_used))],
        key=lambda r: r.note.time
    )
    phrases = _split_into_phrases(all_note_hits, beatmap)

    left_count, right_count = _count_sides(all_note_hits)

    global_alt_rate, _, _, double_taps        = _compute_global_alt(phrases)
    don_stream_rate, _, don_stream_tot        = _compute_don_stream_alt(phrases)
    phrase_alt_L_rate, phrase_alt_R_rate, \
        pair_results, enough_phrases          = _compute_phrase_alt_rates(
                                                    phrases, all_note_hits, beatmap)
    bridge_alt_rate, bridge_pair_count        = _compute_bridge_alt(all_note_hits, beatmap)
    don_finger_alt, kat_finger_alt            = _compute_finger_alt(phrases)
    kat_alt_rate                              = _compute_kat_alt(phrases)

    phrase_alt_best, phrase_alt_worst, \
        phrase_alt_sections                   = _rolling_phrase_alt(pair_results)

    # Combined Full-Alt %: min of both buckets
    if phrase_alt_L_rate is not None and phrase_alt_R_rate is not None:
        phrase_start_alt_rate = min(phrase_alt_L_rate, phrase_alt_R_rate)
    elif phrase_alt_L_rate is not None:
        phrase_start_alt_rate = phrase_alt_L_rate
    elif phrase_alt_R_rate is not None:
        phrase_start_alt_rate = phrase_alt_R_rate
    else:
        phrase_start_alt_rate = 0.5

    don_balance = min(m1_don, k1_don) / max(1, m1_don + k1_don)
    total_sided = left_count + right_count
    left_bias   = left_count / total_sided if total_sided > 0 else 0.5

    name, notes = _classify_playstyle(
        global_alt_rate, don_stream_rate, don_stream_tot,
        don_balance, enough_phrases, phrase_start_alt_rate,
        kat_alt_rate,
    )

    return PlaystyleInfo(name, global_alt_rate, left_bias, double_taps, "KDDK", notes,
                         don_finger_alt=don_finger_alt,
                         kat_finger_alt=kat_finger_alt,
                         primary_don="M1" if m1_don >= k1_don else "K1",
                         primary_kat="K2" if k2_kat >= m2_kat else "M2",
                         phrase_start_alt_rate=phrase_start_alt_rate,
                         phrase_alt_L_rate=phrase_alt_L_rate if phrase_alt_L_rate is not None else 0.5,
                         phrase_alt_R_rate=phrase_alt_R_rate if phrase_alt_R_rate is not None else 0.5,
                         phrase_alt_best=phrase_alt_best,
                         phrase_alt_worst=phrase_alt_worst,
                         phrase_alt_sections=phrase_alt_sections,
                         phrase_count=len(phrases),
                         bridge_alt_rate=bridge_alt_rate,
                         bridge_pair_count=bridge_pair_count)


# ---------------------------------------------------------------------------
# Problem detection
# ---------------------------------------------------------------------------

def _find_problems(results: List[NoteResult], beatmap: BeatmapInfo,
                   playstyle: PlaystyleInfo, ur: float, mean_offset: float) -> List[ProblemSection]:
    problems = []

    # 1. Miss clusters (2+ misses within 4 seconds)
    misses = [r for r in results if r.is_miss]
    for i in range(len(misses)):
        cluster = [misses[i]]
        for j in range(i + 1, len(misses)):
            if misses[j].note.time - misses[i].note.time <= 4000:
                cluster.append(misses[j])
            else:
                break
        if len(cluster) >= 2:
            start = cluster[0].note.time
            end = cluster[-1].note.time
            if not any(p.start_ms == start for p in problems):
                problems.append(ProblemSection(
                    start, end, "miss_cluster",
                    f"{len(cluster)} misses in {(end-start)/1000:.1f}s",
                    min(1.0, len(cluster) / 5.0)
                ))

    # 2. 100-clusters (3+ goods within 2 seconds)
    goods = [r for r in results if r.is_good]
    for i in range(len(goods)):
        cluster = [goods[i]]
        for j in range(i + 1, len(goods)):
            if goods[j].note.time - goods[i].note.time <= 2000:
                cluster.append(goods[j])
            else:
                break
        if len(cluster) >= 3:
            start = cluster[0].note.time
            end = cluster[-1].note.time
            if not any(p.start_ms == start for p in problems):
                problems.append(ProblemSection(
                    start, end, "miss_cluster",
                    f"{len(cluster)} 100s in {(end-start)/1000:.1f}s - timing issues",
                    0.5
                ))

    # 3. High local UR spikes (detected during local_ur computation)
    local_ur = _compute_local_ur(results)
    if local_ur:
        avg_ur = sum(u for _, u in local_ur) / len(local_ur)
        spike_threshold = max(ur * 1.8, avg_ur * 2.2, 12.0)
        i = 0
        while i < len(local_ur):
            ni, u = local_ur[i]
            if u > spike_threshold:
                # Find extent of spike
                j = i
                while j < len(local_ur) and local_ur[j][1] > spike_threshold:
                    j += 1
                start_note = results[local_ur[i][0]].note.time
                end_note = results[local_ur[min(j, len(local_ur)-1)][0]].note.time
                problems.append(ProblemSection(
                    start_note, end_note, "high_ur",
                    f"UR spike: {u:.1f} (avg {avg_ur:.1f})",
                    min(1.0, u / (spike_threshold * 2))
                ))
                i = j + 1
            else:
                i += 1

    # 4. Consistent timing offset
    if abs(mean_offset) > 8:
        direction = "late" if mean_offset > 0 else "early"
        problems.append(ProblemSection(
            0, results[-1].note.time if results else 0,
            "timing_drift",
            f"Consistently hitting {direction} by {abs(mean_offset):.1f}ms avg",
            min(1.0, abs(mean_offset) / 30.0)
        ))

    # 5. Double-taps for full-alt players
    if playstyle.name in ("Full-Alt", "Semi-Alt") and playstyle.double_taps:
        for t in playstyle.double_taps:
            problems.append(ProblemSection(
                t, t + 200, "double_tap",
                f"Alt break (double-tap) at {t/1000:.2f}s",
                0.4
            ))

    # Sort by time
    problems.sort(key=lambda p: p.start_ms)
    return problems


# ---------------------------------------------------------------------------
# Section stats
# ---------------------------------------------------------------------------

def _compute_section_stats(results: List[NoteResult], beatmap: BeatmapInfo, n_sections: int = 8):
    if not results:
        return []
    start_t = results[0].note.time
    end_t = results[-1].note.time
    if end_t == start_t:
        return []
    section_len = (end_t - start_t) / n_sections
    stats = []
    for i in range(n_sections):
        s = start_t + i * section_len
        e = s + section_len
        sect = [r for r in results if s <= r.note.time < e]
        if not sect:
            continue
        hits_300 = sum(1 for r in sect if r.is_great)
        hits_100 = sum(1 for r in sect if r.is_good)
        misses   = sum(1 for r in sect if r.is_miss)
        offsets  = [r.offset for r in sect if r.is_great and not math.isnan(r.offset)]
        sec_ur = 0.0
        if len(offsets) >= 2:
            mean = sum(offsets) / len(offsets)
            sec_ur = math.sqrt(sum((x-mean)**2 for x in offsets)/len(offsets)) * 10
        stats.append({
            "start": s, "end": e,
            "n300": hits_300, "n100": hits_100, "nmiss": misses,
            "ur": sec_ur,
            "acc": (hits_300 + 0.5*hits_100) / max(1, len(sect)) * 100,
        })
    return stats


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze(replay: OsrReplay, beatmap: BeatmapInfo) -> AnalysisResult:
    events = _filter_gameplay_hits(replay.hit_events)
    results = _match_hits_to_notes(events, beatmap, replay.mods)
    ur, mean_offset = _compute_ur(results)
    local_ur = _compute_local_ur(results)
    early_rate = sum(1 for r in results if r.is_great and r.offset < 0) / max(1, len([r for r in results if r.is_great]))
    playstyle = _detect_playstyle(results, beatmap)
    problems = _find_problems(results, beatmap, playstyle, ur, mean_offset)
    section_stats = _compute_section_stats(results, beatmap)

    # Corrected UR: subtract frame-quantization noise (~4.8ms sd @ 60fps) in quadrature
    quant_sd = 16.67 / math.sqrt(12)
    measured_sd = ur / 10.0
    corr_sd = math.sqrt(max(0.0, measured_sd**2 - quant_sd**2))
    ur_corrected = corr_sd * 10.0

    return AnalysisResult(
        note_results=results,
        ur=ur,
        ur_corrected=ur_corrected,
        mean_offset=mean_offset,
        early_rate=early_rate,
        local_ur=local_ur,
        playstyle=playstyle,
        problems=problems,
        section_stats=section_stats,
    )


def analyze_portable(replay: OsrReplay) -> AnalysisResult:
    """
    Analyze a replay without a beatmap (portable mode).
    Only playstyle metrics are available; UR, patterns and note results are empty.
    Note type (Don/Kat) is inferred from the key bits instead of the beatmap.
    """
    from osu_parser import NullBeatmap

    ALL_KEYS = KEY_M1 | KEY_M2 | KEY_K1 | KEY_K2

    # Build minimal mock NoteResult objects from raw key-press events.
    # We only need: note.kind, note.time, hit_time, key_used, is_miss.
    @dataclass
    class _MockNote:
        time: int
        kind: str

    @dataclass
    class _MockResult:
        note: object
        hit_time: int
        key_used: int
        result: str = HIT_300
        offset: float = 0.0
        note_index: int = 0
        @property
        def is_miss(self):  return False
        @property
        def is_great(self): return True
        @property
        def is_good(self):  return False

    mock_results = []
    for e in replay.hit_events:
        k = e.new_keys & ALL_KEYS
        if not k:
            continue
        is_don = bool(k & DON_MASK) and not bool(k & KAT_MASK)
        is_kat = bool(k & KAT_MASK) and not bool(k & DON_MASK)
        if not (is_don or is_kat):
            continue
        kind = NOTE_DON if is_don else NOTE_KAT
        mock_results.append(_MockResult(
            note=_MockNote(time=e.t, kind=kind),
            hit_time=e.t,
            key_used=k,
        ))

    playstyle = _detect_playstyle(mock_results, NullBeatmap())

    return AnalysisResult(
        note_results=[],
        ur=0.0,
        ur_corrected=0.0,
        mean_offset=0.0,
        early_rate=0.0,
        local_ur=[],
        playstyle=playstyle,
        problems=[],
        section_stats=[],
    )
