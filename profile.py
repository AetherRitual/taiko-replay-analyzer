"""Player profile system — stores per-player replay stats across sessions."""
import json
import math
import datetime
import sys
from pathlib import Path

if sys.platform == "win32":
    import os
    PROFILE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "taiko-replay-analyzer" / "profiles"
else:
    PROFILE_DIR = Path.home() / ".local" / "share" / "taiko-replay-analyzer" / "profiles"


class ProfileManager:
    def __init__(self):
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------

    def _profile_path(self, display_name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in display_name)
        return PROFILE_DIR / f"{safe}.json"

    def _load(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_path"] = path
            return data
        except Exception:
            return None

    def save(self, profile: dict) -> None:
        path = profile.get("_path") or self._profile_path(profile["display_name"])
        profile["_path"] = path
        out = {k: v for k, v in profile.items() if not k.startswith("_")}
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def find_profile(self, player_name: str) -> dict | None:
        """Return profile whose display_name or any alias matches player_name (case-insensitive)."""
        needle = player_name.lower()
        for p in sorted(PROFILE_DIR.glob("*.json")):
            data = self._load(p)
            if data and needle in [a.lower() for a in data.get("aliases", [])]:
                return data
        return None

    def list_profiles(self) -> list[dict]:
        result = []
        for p in sorted(PROFILE_DIR.glob("*.json")):
            data = self._load(p)
            if data:
                result.append(data)
        return result

    # ------------------------------------------------------------------
    # Create / modify
    # ------------------------------------------------------------------

    def create_profile(self, display_name: str, layout: str = "KDDK") -> dict:
        profile = {
            "display_name": display_name,
            "aliases": [display_name],
            "layout": layout,
            "created": datetime.date.today().isoformat(),
            "replays": [],
        }
        self.save(profile)
        print(f"  Profile created: {PROFILE_DIR / (profile['_path'].name)}")
        return profile

    def add_alias(self, profile: dict, alias: str) -> None:
        if alias not in profile["aliases"]:
            profile["aliases"].append(alias)
            self.save(profile)
            print(f"  Added alias '{alias}' to profile '{profile['display_name']}'")

    def delete_profile(self, profile: dict) -> None:
        path = profile.get("_path") or self._profile_path(profile["display_name"])
        if path.exists():
            path.unlink()

    def add_replay(self, profile: dict, record: dict) -> bool:
        """Append record to profile; return False if already present (by replay_md5)."""
        md5 = record.get("replay_md5", "")
        if md5 and any(r.get("replay_md5") == md5 for r in profile["replays"]):
            return False
        profile["replays"].append(record)
        self.save(profile)
        return True

    # ------------------------------------------------------------------
    # Build a replay record from parsed objects
    # ------------------------------------------------------------------

    def build_record(self, replay, beatmap, analysis,
                     osr_path=None, osu_path=None) -> dict:
        from osu_parser import NOTE_DON, NOTE_KAT
        from osr_parser import KEY_M1, KEY_M2, KEY_K1, KEY_K2

        mod_rate = replay.mod_rate   # 1.5 for DT, 0.75 for HT, 1.0 otherwise

        # BPM accuracy buckets — stored as *effective* BPM (raw × mod_rate)
        notes = [r for r in analysis.note_results
                 if r.note.kind in (NOTE_DON, NOTE_KAT)]
        buckets: dict = {}
        for r in notes:
            raw_bpm = beatmap.bpm_at(r.note.time)
            eff_bpm = round(raw_bpm * mod_rate / 10) * 10
            if eff_bpm not in buckets:
                buckets[eff_bpm] = {"n300": 0, "n100": 0, "nmiss": 0}
            b = buckets[eff_bpm]
            if r.is_great:   b["n300"] += 1
            elif r.is_good:  b["n100"] += 1
            else:            b["nmiss"] += 1

        bpm_acc = []
        for bpm, d in sorted(buckets.items()):
            total = d["n300"] + d["n100"] + d["nmiss"]
            if total >= 3:
                acc = (d["n300"] + 0.5 * d["n100"]) / total * 100
                bpm_acc.append({"bpm": bpm, "acc": round(acc, 2), "total": total,
                                "n300": d["n300"], "n100": d["n100"], "nmiss": d["nmiss"]})

        # Difficulty weight — use OD as proxy for map difficulty.
        # Higher OD = higher expected skill → more weight in aggregations.
        od     = getattr(beatmap, "od", 5.0)
        weight = round(max(0.1, od / 10.0), 3)   # 0.0–1.0

        # Pattern summary (top/worst 20 patterns for cross-replay aggregation)
        try:
            from pattern_analysis import compute_pattern_stats
            all_pats = compute_pattern_stats(analysis, beatmap, mod_rate=mod_rate)
            # Store serialisable subset
            patterns = [
                {"pattern":        list(p["pattern"]),
                 "count":          p["count"],
                 "n300":           p["n300"], "n100": p["n100"], "nmiss": p["nmiss"],
                 "avg_bpm":        round(p["avg_bpm"], 1),
                 "divisor":        p["divisor"],
                 "div_label":      p["div_label"],
                 "worst_lead":     list(p["worst_lead"]) if p["worst_lead"] else None,
                 "worst_lead_acc": round(p["worst_lead_acc"], 2)}
                for p in all_pats[:40]   # worst 40 (already sorted worst-first)
            ]
        except Exception:
            patterns = []

        # Playstyle
        ps = analysis.playstyle
        key_counts = {"M1": 0, "M2": 0, "K1": 0, "K2": 0}
        for r in analysis.note_results:
            if r.key_used & KEY_M1: key_counts["M1"] += 1
            if r.key_used & KEY_M2: key_counts["M2"] += 1
            if r.key_used & KEY_K1: key_counts["K1"] += 1
            if r.key_used & KEY_K2: key_counts["K2"] += 1

        playstyle = {
            "name":                  ps.name,
            "alt_rate":              round(ps.alt_rate, 4),
            "left_bias":             round(ps.left_bias, 4),
            "double_tap_count":      len(ps.double_taps),
            "layout":                ps.layout,
            "key_counts":            key_counts,
            "don_finger_alt":        round(ps.don_finger_alt, 4),
            "kat_finger_alt":        round(ps.kat_finger_alt, 4),
            "primary_don":           ps.primary_don,
            "primary_kat":           ps.primary_kat,
            "phrase_start_alt_rate": round(ps.phrase_start_alt_rate, 4),
            "phrase_alt_L_rate":     round(ps.phrase_alt_L_rate, 4),
            "phrase_alt_R_rate":     round(ps.phrase_alt_R_rate, 4),
            "phrase_alt_best":       round(ps.phrase_alt_best, 4),
            "phrase_alt_worst":      round(ps.phrase_alt_worst, 4),
            "phrase_count":          ps.phrase_count,
            "bridge_alt_rate":       round(ps.bridge_alt_rate, 4),
            "bridge_pair_count":     ps.bridge_pair_count,
        }

        # Replay date from Windows FILETIME
        try:
            unix_ts  = (replay.timestamp - 621_355_968_000_000_000) / 10_000_000
            date_str = datetime.datetime.utcfromtimestamp(unix_ts).strftime("%Y-%m-%d")
        except Exception:
            date_str = "unknown"

        record = {
            "replay_md5":    replay.replay_md5,
            "date":          date_str,
            "beatmap_md5":   replay.beatmap_md5,
            "beatmap_title": f"{beatmap.artist} - {beatmap.title} [{beatmap.version}]",
            "mods":          replay.mod_string,
            "score":         replay.score,
            "accuracy":      round(replay.accuracy, 4),
            "max_combo":     replay.max_combo,
            "n300":          replay.n300,
            "n100":          replay.n100,
            "nmiss":         replay.nmiss,
            "ur":            round(analysis.ur, 2),
            "ur_corrected":  round(analysis.ur_corrected, 2),
            "mean_offset":   round(analysis.mean_offset, 2),
            "bpm_acc":       bpm_acc,
            "diff_weight":   weight,
            "patterns":      patterns,
            "playstyle":     playstyle,
        }
        if osr_path:
            record["osr_path"] = str(osr_path)
        if osu_path:
            record["osu_path"] = str(osu_path)
        return record

    # ------------------------------------------------------------------
    # Aggregation helpers (used by viewer)
    # ------------------------------------------------------------------

    def aggregated_bpm_acc(self, profile: dict, min_total: int = 20) -> list[dict]:
        """
        Merge BPM comfortability buckets across all replays, weighted by
        diff_weight (OD-based difficulty proxy: 0.0–1.0).
        BPM values are already stored as effective BPM (DT applied per replay).

        Comfortability — strict formula:
            only 300s count positively; 100s are near-neutral; misses penalise hard.
            comfort = (n300 - 2 × nmiss) / total × 100, clamped [0, 100]
        """
        merged: dict = {}
        for rep in profile.get("replays", []):
            w = max(0.1, rep.get("diff_weight", 0.5))
            for b in rep.get("bpm_acc", []):
                bpm = b["bpm"]
                if bpm not in merged:
                    merged[bpm] = {"w_n300": 0.0, "w_n100": 0.0,
                                   "w_nmiss": 0.0, "w_total": 0.0}
                m = merged[bpm]
                m["w_n300"]  += b["n300"]  * w
                m["w_n100"]  += b["n100"]  * w
                m["w_nmiss"] += b["nmiss"] * w
                m["w_total"] += (b["n300"] + b["n100"] + b["nmiss"]) * w

        result = []
        for bpm, m in sorted(merged.items()):
            if m["w_total"] < min_total:
                continue
            # Comfortability: only greats count; misses penalise ×2
            comfort = max(0.0, (m["w_n300"] - 2.0 * m["w_nmiss"]) / m["w_total"] * 100)
            # Raw acc for reference
            acc     = (m["w_n300"] + 0.5 * m["w_n100"]) / m["w_total"] * 100
            total   = int(m["w_total"])
            result.append({
                "bpm":       bpm,
                "acc":       acc,
                "comfort":   round(comfort, 2),
                "total":     total,
                "n300":      int(m["w_n300"]),
                "n100":      int(m["w_n100"]),
                "nmiss":     int(m["w_nmiss"]),
            })
        return result

    def aggregated_patterns(self, profile: dict, min_count: int = 5) -> list:
        """Merge 4-note pattern stats across all stored replays, preserving lead-in data."""
        from pattern_analysis import aggregate_patterns
        all_lists = [rep.get("patterns", []) for rep in profile.get("replays", [])]
        converted = []
        for pat_list in all_lists:
            converted.append([
                {**p,
                 "pattern":        tuple(p["pattern"]),
                 "delta":          0.0,
                 "ur":             0.0,
                 "worst_lead":     tuple(p["worst_lead"]) if p.get("worst_lead") else None,
                 "worst_lead_acc": p.get("worst_lead_acc", 100.0)}
                for p in pat_list
            ])
        return aggregate_patterns(converted, min_count=min_count)

    def ur_trend(self, profile: dict, last_n: int = 20) -> list[dict]:
        """Return the last N replays sorted by date with UR values."""
        reps = sorted(profile.get("replays", []), key=lambda r: r.get("date", ""))
        return reps[-last_n:]

    def acc_trend(self, profile: dict, last_n: int = 20) -> list[dict]:
        reps = sorted(profile.get("replays", []), key=lambda r: r.get("date", ""))
        return reps[-last_n:]

    def aggregated_playstyle(self, profile: dict) -> dict | None:
        """
        Aggregate playstyle stats across all stored replays.
        Returns a summary dict or None if no playstyle data present.
        """
        reps  = [r for r in profile.get("replays", []) if r.get("playstyle")]
        if not reps:
            return None

        chrono = sorted(reps, key=lambda r: r.get("date", ""))

        name_counts: dict = {}
        alt_rates        = []
        left_biases      = []
        dt_counts        = []
        key_totals       = {"M1": 0, "M2": 0, "K1": 0, "K2": 0}
        don_finger_alts  = []
        kat_finger_alts  = []
        phrase_alt_rates = []

        for rep in reps:
            ps = rep["playstyle"]
            name_counts[ps["name"]] = name_counts.get(ps["name"], 0) + 1
            alt_rates.append(ps["alt_rate"])
            left_biases.append(ps["left_bias"])
            dt_counts.append(ps.get("double_tap_count", 0))
            for k in key_totals:
                key_totals[k] += ps.get("key_counts", {}).get(k, 0)
            if "don_finger_alt" in ps:
                don_finger_alts.append(ps["don_finger_alt"])
            if "kat_finger_alt" in ps:
                kat_finger_alts.append(ps["kat_finger_alt"])
            # Only include replays with enough phrases for a reliable reading
            if ps.get("phrase_count", 0) >= 4:
                phrase_alt_rates.append(ps["phrase_start_alt_rate"])


        dominant   = max(name_counts, key=name_counts.get)
        total_keys = max(1, sum(key_totals.values()))

        return {
            "n_replays":          len(reps),
            "dominant_name":      dominant,
            "name_counts":        dict(sorted(name_counts.items(),
                                              key=lambda x: -x[1])),
            "avg_alt_rate":       sum(alt_rates)  / len(alt_rates),
            "avg_left_bias":      sum(left_biases) / len(left_biases),
            "avg_dt_count":       sum(dt_counts)  / len(dt_counts),
            "key_fracs":          {k: v / total_keys for k, v in key_totals.items()},
            # DDKK-specific aggregates
            "avg_don_finger_alt": (sum(don_finger_alts) / len(don_finger_alts)
                                   if don_finger_alts else None),
            "avg_kat_finger_alt": (sum(kat_finger_alts) / len(kat_finger_alts)
                                   if kat_finger_alts else None),
            # Phrase-start alternation (KDDK only; null when not enough data)
            "avg_phrase_start_alt_rate": (sum(phrase_alt_rates) / len(phrase_alt_rates)
                                          if phrase_alt_rates else None),
            "avg_phrase_alt_L_rate": (
                sum(r["playstyle"].get("phrase_alt_L_rate", 0.5) for r in reps
                    if r["playstyle"].get("phrase_count", 0) >= 4)
                / max(1, sum(1 for r in reps if r["playstyle"].get("phrase_count", 0) >= 4))
                if phrase_alt_rates else None),
            "avg_phrase_alt_R_rate": (
                sum(r["playstyle"].get("phrase_alt_R_rate", 0.5) for r in reps
                    if r["playstyle"].get("phrase_count", 0) >= 4)
                / max(1, sum(1 for r in reps if r["playstyle"].get("phrase_count", 0) >= 4))
                if phrase_alt_rates else None),
            "avg_phrase_alt_best":  (sum(r["playstyle"].get("phrase_alt_best", 0)
                                         for r in reps if r["playstyle"].get("phrase_count", 0) >= 4)
                                     / max(1, sum(1 for r in reps
                                                  if r["playstyle"].get("phrase_count", 0) >= 4))
                                     if phrase_alt_rates else None),
            "avg_phrase_alt_worst": (sum(r["playstyle"].get("phrase_alt_worst", 0)
                                         for r in reps if r["playstyle"].get("phrase_count", 0) >= 4)
                                     / max(1, sum(1 for r in reps
                                                  if r["playstyle"].get("phrase_count", 0) >= 4))
                                     if phrase_alt_rates else None),
            # chronological series for trend charts
            "alt_rate_series":           [r["playstyle"]["alt_rate"]  for r in chrono],
            "left_bias_series":          [r["playstyle"]["left_bias"] for r in chrono],
            "don_finger_alt_series":     [r["playstyle"].get("don_finger_alt", 0)
                                          for r in chrono],
            "kat_finger_alt_series":     [r["playstyle"].get("kat_finger_alt", 0)
                                          for r in chrono],
            "phrase_start_alt_series":   [r["playstyle"].get("phrase_start_alt_rate", 0.5)
                                          for r in chrono
                                          if r["playstyle"].get("phrase_count", 0) >= 4],
            "phrase_alt_L_series":       [r["playstyle"].get("phrase_alt_L_rate", 0.5)
                                          for r in chrono
                                          if r["playstyle"].get("phrase_count", 0) >= 4],
            "phrase_alt_R_series":       [r["playstyle"].get("phrase_alt_R_rate", 0.5)
                                          for r in chrono
                                          if r["playstyle"].get("phrase_count", 0) >= 4],
        }


# ---------------------------------------------------------------------------
# Terminal interaction helper (called from main.py)
# ---------------------------------------------------------------------------

def prompt_profile(replay, beatmap, analysis,
                   osr_path=None, osu_path=None) -> dict | None:
    """
    Interactive terminal flow to find/create a player profile and save this replay.
    Returns the profile dict (with _path set) or None if the user skips.
    """
    pm = ProfileManager()
    player = replay.player_name
    profile = pm.find_profile(player)

    print()
    if profile:
        n = len(profile["replays"])
        print(f"  Profile found: {profile['display_name']}  ({n} replay{'s' if n != 1 else ''} stored)")
        ans = input("  Add this replay to profile? [Y/n]: ").strip().lower()
        if ans == "n":
            return profile          # return profile for viewer display, but don't save

        record = pm.build_record(replay, beatmap, analysis,
                                  osr_path=osr_path, osu_path=osu_path)
        added  = pm.add_replay(profile, record)
        if added:
            print(f"  Saved. Profile now has {len(profile['replays'])} replay(s).")
        else:
            print("  This replay is already in the profile.")
        return profile

    else:
        print(f"  No profile found for player: '{player}'")
        existing = pm.list_profiles()

        options = []
        if existing:
            print("  Existing profiles:")
            for i, p in enumerate(existing):
                aliases = ", ".join(p["aliases"])
                n = len(p["replays"])
                print(f"    [{i+1}] {p['display_name']}  (aliases: {aliases},  {n} replay(s))")
            options_str = f"1–{len(existing)}, "
        else:
            options_str = ""

        print(f"  [{len(existing)+1}] Create new profile for '{player}'")
        print(f"  [s] Skip")

        ans = input(f"  Choice [{len(existing)+1}]: ").strip().lower()

        if ans == "s":
            return None

        # Numeric choice
        try:
            idx = int(ans) - 1
        except ValueError:
            idx = len(existing)   # default = create new

        if 0 <= idx < len(existing):
            # Add as alias to existing profile
            profile = existing[idx]
            pm.add_alias(profile, player)
        else:
            # Create new profile — ask for layout
            layout_ans = input("  Layout [KDDK/ddkk/kkdd, default KDDK]: ").strip().upper()
            layout = layout_ans if layout_ans in ("KDDK", "DDKK", "KKDD") else "KDDK"
            profile = pm.create_profile(player, layout=layout)

        record = pm.build_record(replay, beatmap, analysis,
                                  osr_path=osr_path, osu_path=osu_path)
        pm.add_replay(profile, record)
        print(f"  Saved. Profile now has {len(profile['replays'])} replay(s).")
        return profile
