"""Shared 4-note pattern analysis — used by viewer.py and profile.py."""
import math
from collections import Counter

from osu_parser import NOTE_DON, NOTE_KAT

_DIVISORS       = [1, 2, 3, 4, 6, 8]
_DIVISOR_LABELS = {1: '1/1', 2: '1/2', 3: '1/3', 4: '1/4', 6: '1/6', 8: '1/8'}


def _beat_snap(note_time: float, tp_time: float, beat_len: float) -> int:
    """
    Return the coarsest divisor N such that the note lands on the 1/N beat grid,
    using the beat phase relative to the active timing point.
    """
    if beat_len <= 0:
        return 4
    beat_phase = (note_time - tp_time) % beat_len
    fraction   = beat_phase / beat_len          # 0.0 – <1.0
    best_div, best_err = 1, float('inf')
    for n in _DIVISORS:
        snapped = round(fraction * n) / n
        err     = abs(fraction - snapped)
        if err < best_err - 1e-9:               # prefer coarser on tie
            best_err = err
            best_div = n
    return best_div


def _gap_divisor(gap_ms: float, beat_len: float) -> int:
    """Divisor from the ratio of beat length to gap (cross-check for burst detection)."""
    if gap_ms <= 0 or beat_len <= 0:
        return 4
    ratio = beat_len / gap_ms
    return min(_DIVISORS, key=lambda d: abs(d - ratio))


def _sym(r) -> str:
    """Note symbol: 'D' / 'K' / 'Db' / 'Kb'."""
    prefix = 'D' if r.note.kind == NOTE_DON else 'K'
    return prefix + ('b' if r.note.is_big else '')


def compute_pattern_stats(analysis, beatmap,
                          mod_rate: float = 1.0,
                          min_count: int = 3) -> list:
    """
    Compute 4-note sliding-window pattern statistics.

    avg_bpm in returned dicts is *effective* BPM (raw × mod_rate).
    Returns list sorted by accuracy ascending (worst first).
    """
    notes = [r for r in analysis.note_results
             if r.note.kind in (NOTE_DON, NOTE_KAT)]
    if len(notes) < 8:
        return []

    overall_acc = (
        sum(1 for r in notes if r.is_great)
        + 0.5 * sum(1 for r in notes if r.is_good)
    ) / max(1, len(notes)) * 100

    N    = 4
    pats: dict = {}

    for i in range(len(notes) - N + 1):
        win = notes[i:i + N]

        # Skip windows that straddle a phrase boundary.
        # A gap > beat_len/4 + 5ms means these notes aren't part of the same stream.
        gaps = [win[j + 1].note.time - win[j].note.time for j in range(N - 1)]
        tp = beatmap.timing_point_at(win[0].note.time)
        if tp and tp.uninherited:
            _beat_len = tp.beat_len
        else:
            raw_bpm  = beatmap.bpm_at(win[0].note.time)
            _beat_len = 60000.0 / raw_bpm if raw_bpm > 0 else 375.0
        gap_threshold = _beat_len / 4.0 + 5.0
        if max(gaps) > gap_threshold:
            continue

        key = tuple(_sym(r) for r in win)

        if key not in pats:
            pats[key] = {'n300': 0, 'n100': 0, 'nmiss': 0,
                         'offsets': [], 'bpms': [], 'divisors': [],
                         'gap_beats_sum': [0.0] * (N - 1),
                         'gap_beats_n':   0,
                         'leads': {}}
        p = pats[key]

        # Per-window result (worst note in window)
        if any(r.is_miss for r in win):
            res = 'nmiss'
        elif any(r.is_good for r in win):
            res = 'n100'
        else:
            res = 'n300'
        p[res] += 1

        last = win[-1]
        if last.is_great and not math.isnan(last.offset):
            p['offsets'].append(last.offset)

        raw_bpm = beatmap.bpm_at(win[0].note.time)
        eff_bpm = raw_bpm * mod_rate   # effective BPM for display only
        p['bpms'].append(eff_bpm)

        gaps = [win[j + 1].note.time - win[j].note.time for j in range(N - 1)]

        # Beat-grid anchoring: use timing-point beat_len (raw, in original time space)
        # to get each note's true subdivision, then take the finest across the window.
        tp = beatmap.timing_point_at(win[0].note.time)
        if tp:
            beat_len  = tp.beat_len
            tp_time   = tp.time
            note_divs = [_beat_snap(r.note.time, tp_time, beat_len) for r in win]
            gap_divs  = [_gap_divisor(g, beat_len) for g in gaps if g > 0]
        else:
            beat_len  = 60000.0 / raw_bpm
            note_divs = []
            gap_divs  = [_gap_divisor(g, beat_len) for g in gaps if g > 0]
        all_divs = note_divs + gap_divs
        if all_divs:
            p['divisors'].append(max(all_divs))

        # Gap fractions — each inter-note gap as a fraction of one beat
        if beat_len > 0:
            for j in range(N - 1):
                p['gap_beats_sum'][j] += gaps[j] / beat_len
            p['gap_beats_n'] += 1

        # Leading 2-note context
        if i >= 2:
            lead_key = tuple('D' if r.note.kind == NOTE_DON else 'K'
                             for r in notes[i - 2:i])
            if lead_key not in p['leads']:
                p['leads'][lead_key] = {'n300': 0, 'n100': 0, 'nmiss': 0}
            p['leads'][lead_key][res] += 1

    result = []
    for key, data in pats.items():
        total = data['n300'] + data['n100'] + data['nmiss']
        if total < min_count:
            continue
        acc = (data['n300'] + 0.5 * data['n100']) / total * 100

        # Timing spread
        ur = 0.0
        if len(data['offsets']) >= 2:
            mean = sum(data['offsets']) / len(data['offsets'])
            var  = sum((x - mean) ** 2 for x in data['offsets']) / len(data['offsets'])
            ur   = math.sqrt(var) * 10

        divisors  = data['divisors']
        dom_div   = Counter(divisors).most_common(1)[0][0] if divisors else 4
        avg_bpm   = sum(data['bpms']) / len(data['bpms']) if data['bpms'] else 0.0

        n_gb = data['gap_beats_n']
        avg_gap_fracs = ([data['gap_beats_sum'][j] / n_gb for j in range(N - 1)]
                         if n_gb > 0 else [1.0 / dom_div] * (N - 1))

        worst_lead = None
        worst_lead_acc = 999.0
        for lk, ld in data['leads'].items():
            lt = ld['n300'] + ld['n100'] + ld['nmiss']
            if lt < 2:
                continue
            la = (ld['n300'] + 0.5 * ld['n100']) / lt * 100
            if la < worst_lead_acc:
                worst_lead_acc = la
                worst_lead     = lk

        result.append({
            'pattern':        key,
            'count':          total,
            'acc':            acc,
            'n300':           data['n300'],
            'n100':           data['n100'],
            'nmiss':          data['nmiss'],
            'ur':             ur,
            'avg_bpm':        avg_bpm,
            'delta':          acc - overall_acc,
            'div_label':      _DIVISOR_LABELS.get(dom_div, '1/4'),
            'divisor':        dom_div,
            'avg_gap_fracs':  avg_gap_fracs,
            'worst_lead':     worst_lead,
            'worst_lead_acc': worst_lead_acc,
        })

    result.sort(key=lambda x: (x['acc'], -x['count']))
    return result


def aggregate_patterns(pattern_lists: list, min_count: int = 5) -> list:
    """
    Merge pattern stat dicts from multiple replays.
    pattern_lists: list of lists (one per replay) of pattern dicts.
    Returns combined list sorted by acc ascending.
    """
    merged: dict = {}
    for pat_list in pattern_lists:
        for p in pat_list:
            key = tuple(p['pattern'])
            N = len(p['pattern'])
            if key not in merged:
                merged[key] = {
                    'n300': 0, 'n100': 0, 'nmiss': 0,
                    'bpm_sum': 0.0, 'occ': 0,
                    'divisors': [],
                    'gap_beats_sum': [0.0] * (N - 1),
                    'gap_beats_n':   0,
                    'worst_lead': None, 'worst_lead_acc': 999.0,
                }
            m = merged[key]
            m['n300']    += p['n300']
            m['n100']    += p['n100']
            m['nmiss']   += p['nmiss']
            m['bpm_sum'] += p.get('avg_bpm', 0) * p['count']
            m['occ']     += p['count']
            m['divisors'].append(p.get('divisor', 4))
            # Weighted average of gap fracs
            gf = p.get('avg_gap_fracs')
            if gf and len(gf) == N - 1:
                w = p['count']
                for j in range(N - 1):
                    m['gap_beats_sum'][j] += gf[j] * w
                m['gap_beats_n'] += w
            # Keep worst lead-in context across replays
            wl_acc = p.get('worst_lead_acc', 999.0)
            if p.get('worst_lead') and wl_acc < m['worst_lead_acc']:
                m['worst_lead_acc'] = wl_acc
                m['worst_lead']     = tuple(p['worst_lead'])

    # Overall accuracy baseline
    total_n300  = sum(m['n300'] for m in merged.values())
    total_n100  = sum(m['n100'] for m in merged.values())
    total_n     = max(1, total_n300 + total_n100
                       + sum(m['nmiss'] for m in merged.values()))
    overall_acc = (total_n300 + 0.5 * total_n100) / total_n * 100

    result = []
    for key, m in merged.items():
        total = m['n300'] + m['n100'] + m['nmiss']
        if total < min_count:
            continue
        acc     = (m['n300'] + 0.5 * m['n100']) / total * 100
        dom_div = Counter(m['divisors']).most_common(1)[0][0] if m['divisors'] else 4
        avg_bpm = m['bpm_sum'] / max(1, m['occ'])
        wl      = m['worst_lead'] if m['worst_lead_acc'] < 999.0 else None
        N       = len(key)
        n_gb    = m['gap_beats_n']
        avg_gap_fracs = ([m['gap_beats_sum'][j] / n_gb for j in range(N - 1)]
                         if n_gb > 0 else [1.0 / dom_div] * (N - 1))
        result.append({
            'pattern':        list(key),
            'count':          total,
            'acc':            acc,
            'n300':           m['n300'],
            'n100':           m['n100'],
            'nmiss':          m['nmiss'],
            'avg_bpm':        avg_bpm,
            'delta':          acc - overall_acc,
            'div_label':      _DIVISOR_LABELS.get(dom_div, '1/4'),
            'divisor':        dom_div,
            'avg_gap_fracs':  avg_gap_fracs,
            'worst_lead':     wl,
            'worst_lead_acc': m['worst_lead_acc'] if wl else 100.0,
        })

    result.sort(key=lambda x: (x['acc'], -x['count']))
    return result
