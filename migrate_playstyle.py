"""
One-shot migration: recompute playstyle fields for all existing profile records
that have osr_path + osu_path stored, using the current analyzer code.

Run: python3 migrate_playstyle.py [profile_name]
If no profile_name given, migrates all profiles.
"""
import json
import sys
from pathlib import Path

from osr_parser import parse_osr, KEY_M1, KEY_M2, KEY_K1, KEY_K2
from osu_parser import parse_osu
from analyzer import analyze
from profile import ProfileManager, PROFILE_DIR


def migrate_profile(profile: dict, pm: ProfileManager, dry_run: bool = False):
    display = profile["display_name"]
    replays = profile.get("replays", [])
    updated = 0
    errors  = 0

    for i, rec in enumerate(replays):
        osr_path = rec.get("osr_path")
        osu_path = rec.get("osu_path")
        if not osr_path or not osu_path:
            print(f"  [{i+1}/{len(replays)}] SKIP (no paths): {rec.get('beatmap_title','?')}")
            continue

        osr_path = Path(osr_path)
        osu_path = Path(osu_path)
        if not osr_path.exists():
            print(f"  [{i+1}/{len(replays)}] MISSING osr: {osr_path.name}")
            errors += 1
            continue
        if not osu_path.exists():
            print(f"  [{i+1}/{len(replays)}] MISSING osu: {osu_path.name}")
            errors += 1
            continue

        try:
            replay   = parse_osr(str(osr_path))
            beatmap  = parse_osu(osu_path)
            analysis = analyze(replay, beatmap)
            ps       = analysis.playstyle

            key_counts = {"M1": 0, "M2": 0, "K1": 0, "K2": 0}
            for r in analysis.note_results:
                if r.key_used & KEY_M1: key_counts["M1"] += 1
                if r.key_used & KEY_M2: key_counts["M2"] += 1
                if r.key_used & KEY_K1: key_counts["K1"] += 1
                if r.key_used & KEY_K2: key_counts["K2"] += 1

            new_ps = {
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
            old_psa = rec["playstyle"].get("phrase_start_alt_rate", "?")
            new_psa = new_ps["phrase_start_alt_rate"]
            new_L   = new_ps["phrase_alt_L_rate"]
            new_R   = new_ps["phrase_alt_R_rate"]

            if not dry_run:
                rec["playstyle"] = new_ps

            print(f"  [{i+1}/{len(replays)}] {osr_path.name[:60]}"
                  f"  psa {old_psa:.3f}->{new_psa:.3f}  L={new_L:.2f} R={new_R:.2f}")
            updated += 1

        except Exception as exc:
            print(f"  [{i+1}/{len(replays)}] ERROR {osr_path.name}: {exc}")
            errors += 1

    if not dry_run and updated:
        pm.save(profile)
        print(f"\n  Saved {display}: {updated} updated, {errors} errors")
    else:
        print(f"\n  {display} (dry-run): {updated} would update, {errors} errors")

    return updated, errors


def main():
    pm = ProfileManager()

    target = sys.argv[1] if len(sys.argv) > 1 else None

    if target:
        profile = pm.find_profile(target)
        if profile is None:
            # Try direct filename match
            path = PROFILE_DIR / f"{target}.json"
            if path.exists():
                profile = pm._load(path)
        if profile is None:
            print(f"Profile not found: {target}")
            sys.exit(1)
        profiles = [profile]
    else:
        profiles = pm.list_profiles()

    print(f"Migrating {len(profiles)} profile(s)...\n")
    total_updated = total_errors = 0
    for p in profiles:
        u, e = migrate_profile(p, pm)
        total_updated += u
        total_errors  += e

    print(f"\nDone. Total: {total_updated} updated, {total_errors} errors.")


if __name__ == "__main__":
    main()
