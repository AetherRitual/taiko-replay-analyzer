#!/usr/bin/env python3
"""
osu!Taiko Replay Analyzer & Viewer
Usage:
    python main.py <replay.osr> [--songs <songs_folder>]
    python main.py            (auto-detects .osr in current dir)
    python main.py --mass-add [--songs <songs_folder>]
"""
import sys
import math
import argparse
from pathlib import Path

import pygame

from osr_parser import parse_osr
from osu_parser import parse_osu, find_beatmap_by_md5, detect_songs_folder, detect_lazer_folder
from analyzer import analyze
import config as cfg


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------

def print_summary(replay, beatmap, analysis):
    print(f"\n{'='*62}")
    print(f"  {beatmap.artist} - {beatmap.title}  [{beatmap.version}]")
    print(f"  Player: {replay.player_name}   Mods: {replay.mod_string}")
    print(f"{'='*62}")
    print(f"  Score:       {replay.score:,}")
    print(f"  Combo:       {replay.max_combo}x")
    print(f"  Accuracy:    {replay.accuracy:.2f}%")
    print(f"  300/100/Miss {replay.n300} / {replay.n100} / {replay.nmiss}")
    print()
    an = analysis
    # Estimate corrected UR (remove ~4.8ms frame-quantization noise in quadrature)
    measured_sd = an.ur / 10.0
    quant_sd = 16.67 / math.sqrt(12)
    corr_sd = math.sqrt(max(0, measured_sd**2 - quant_sd**2))
    corr_ur = corr_sd * 10
    print(f"  UR (frame):  {an.ur:.2f}  (~{corr_ur:.2f} est. corrected)")
    print(f"  Avg Offset:  {an.mean_offset:+.1f}ms")
    print(f"  Early Rate:  {an.early_rate*100:.0f}%")
    print()
    ps = an.playstyle
    print(f"  Playstyle:   {ps.name} ({ps.layout})")
    print(f"  Alt Rate:    {ps.alt_rate*100:.0f}%")
    print(f"  Notes:       {ps.notes}")
    if ps.double_taps:
        print(f"  Alt breaks:  {len(ps.double_taps)}")
    print()
    # Group problems for terminal display
    structural = [p for p in an.problems if p.kind not in ("double_tap",)]
    alt_breaks  = [p for p in an.problems if p.kind == "double_tap"]
    if structural:
        print(f"  Issues ({len(structural)}):")
        for p in structural[:8]:
            print(f"    [{p.start_ms/1000:.1f}s]  {p.description}")
    elif not alt_breaks:
        print("  No significant issues detected.")
    if alt_breaks and ps.name in ("Full-Alt", "Semi-Alt"):
        print(f"  Alt breaks on dense passages: {len(alt_breaks)}")
    print(f"{'='*62}\n")


# ---------------------------------------------------------------------------
# Loading screen
# ---------------------------------------------------------------------------

_font_cache = {}


def _get_font(size=20):
    if size not in _font_cache:
        # Import here — pygame must be initialised before ui_common.sysfont is called
        from ui_common import sysfont, FONT_PREF
        _font_cache[size] = sysfont(FONT_PREF, size)
    return _font_cache[size]


def loading_screen(screen, message, sub=""):
    screen.fill((18, 20, 28))
    f = _get_font(22)
    w, h = screen.get_size()
    img = f.render(message, True, (220, 225, 240))
    screen.blit(img, img.get_rect(center=(w // 2, h // 2)))
    if sub:
        fs = _get_font(14)
        imgs = fs.render(sub, True, (120, 128, 160))
        screen.blit(imgs, imgs.get_rect(center=(w // 2, h // 2 + 30)))
    pygame.display.flip()
    pygame.event.pump()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_profile_loop(screen, profile: dict, songs_folder: Path,
                      initial_replay=None, initial_beatmap=None,
                      initial_analysis=None) -> None:
    """
    Profile-view ↔ replay-view loop.
    If initial_* objects are given, the first iteration opens the replay viewer
    and the profile view is accessible via P. Otherwise starts in profile view.
    """
    from profile_viewer import ProfileViewer
    from viewer import Viewer

    cur_r = initial_replay
    cur_b = initial_beatmap
    cur_a = initial_analysis
    show_profile = (cur_r is None and profile is not None)

    while True:
        if show_profile and profile:
            pygame.display.set_caption("Taiko Replay Analyzer — Profile View")
            pv  = ProfileViewer(profile, songs_folder, screen)
            rec = pv.run()
            show_profile = False

            if rec is None:
                # ESC from profile view
                if cur_r is None:
                    break   # no replay to fall back to → exit
                # otherwise fall through to re-show the current replay
            else:
                r, b, a, _ = _load_replay_full(screen, Path(rec["osr_path"]), songs_folder)
                if r is not None:
                    cur_r, cur_b, cur_a = r, b, a
                # if load failed, stay with current

        if cur_r is None:
            break

        pygame.display.set_caption(
            f"Taiko Replay — {cur_b.artist} - {cur_b.title} "
            f"[{cur_b.version}]  |  {cur_r.player_name}")
        viewer = Viewer(cur_r, cur_b, cur_a, existing_screen=screen, profile=profile)
        viewer.run()

        if getattr(viewer, "_open_profile", False):
            show_profile = True
        else:
            break   # normal quit


def _load_replay_full(screen, osr_path: Path, songs_folder: Path):
    """Parse, find beatmap, analyze. Returns (replay, beatmap, analysis, osu_path)."""
    loading_screen(screen, "Loading replay...", osr_path.name)
    replay = parse_osr(str(osr_path))

    loading_screen(screen, "Searching for beatmap...", f"MD5: {replay.beatmap_md5}")

    def _cb(done, total):
        pct = int(done / max(1, total) * 100)
        loading_screen(screen, f"Scanning beatmaps... {pct}%", f"{done}/{total}")

    osu_path = find_beatmap_by_md5(songs_folder, replay.beatmap_md5, _cb)
    if not osu_path:
        return None, None, None, None

    loading_screen(screen, "Loading beatmap...", osu_path.name)
    beatmap = parse_osu(osu_path)
    loading_screen(screen, "Analyzing...")
    analysis = analyze(replay, beatmap)
    return replay, beatmap, analysis, osu_path


# ---------------------------------------------------------------------------
# Profile command handlers (no pygame needed)
# ---------------------------------------------------------------------------

def _cmd_create_profile(args) -> None:
    from profile import ProfileManager
    pm   = ProfileManager()
    name = args.create_profile.strip()
    new_aliases = [a.strip() for a in args.aliases.split(",")
                   if a.strip()] if args.aliases else []

    existing = pm.find_profile(name)
    if existing:
        print(f"\n  Profile already exists: '{existing['display_name']}'")
        print(f"  Current aliases: {', '.join(existing['aliases'])}")
        added = []
        for alias in new_aliases:
            if alias not in existing["aliases"]:
                pm.add_alias(existing, alias)
                added.append(alias)
        if added:
            print(f"  Added aliases: {', '.join(added)}")
        else:
            print("  No new aliases to add.")
    else:
        layout  = args.layout.upper() if args.layout else "KDDK"
        profile = pm.create_profile(name, layout=layout)
        for alias in new_aliases:
            if alias and alias not in profile["aliases"]:
                pm.add_alias(profile, alias)
        print(f"  Layout:  {profile['layout']}")
        print(f"  Aliases: {', '.join(profile['aliases'])}")


def _cmd_delete_profile(args) -> None:
    from profile import ProfileManager
    pm      = ProfileManager()
    name    = args.delete_profile.strip()
    profile = pm.find_profile(name)
    if not profile:
        print(f"\n  No profile found for '{name}'")
        return
    print(f"\n  Profile:  {profile['display_name']}")
    print(f"  Aliases:  {', '.join(profile['aliases'])}")
    print(f"  Replays:  {len(profile.get('replays', []))}")
    if input("  Delete this profile? [y/N]: ").strip().lower() == "y":
        pm.delete_profile(profile)
        cfg.refresh()
        print("  Deleted.")
    else:
        print("  Cancelled.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="osu!Taiko Replay Analyzer")
    parser.add_argument("replay",      nargs="?", help="Path to .osr replay file")
    parser.add_argument("--songs",     help="Path to osu! Songs folder")
    parser.add_argument("--mass-add",  action="store_true",
                        help="Batch-import .osr files from a folder into profiles")
    parser.add_argument("--profile",   nargs="?", const="",
                        help="Open profile viewer directly (optionally specify player name)")
    parser.add_argument("--create-profile", metavar="NAME",
                        help="Create a player profile (no replay needed)")
    parser.add_argument("--aliases", metavar="ALIAS1,ALIAS2,...",
                        help="Comma-separated aliases for --create-profile")
    parser.add_argument("--layout", default="KDDK",
                        help="Player layout for --create-profile: KDDK (default), DDKK, or KKDD")
    parser.add_argument("--delete-profile", metavar="NAME",
                        help="Delete a player profile by name or alias")
    parser.add_argument("--portable", action="store_true",
                        help="Portable mode: analyze replay without a Songs folder")
    args = parser.parse_args()

    # --- Create-profile mode (no pygame needed) ---
    if args.create_profile:
        _cmd_create_profile(args)
        return

    # --- Delete-profile mode (no pygame needed) ---
    if args.delete_profile:
        _cmd_delete_profile(args)
        return

    # Keep config.txt profile list up to date
    cfg.refresh()

    # --- Init pygame (needed before any mode) ---
    pygame.init()
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
    screen = pygame.display.set_mode((1920, 1080), pygame.RESIZABLE | pygame.DOUBLEBUF)
    pygame.display.set_caption("Taiko Replay Analyzer")

    # --- Profile-only mode ---
    if args.profile is not None:
        songs_folder = Path(args.songs) if args.songs else cfg.get_songs_folder() or detect_songs_folder()
        if not songs_folder or not songs_folder.exists():
            songs_folder = Path(".")   # no songs = replay loading disabled, but browsing still works

        from profile import ProfileManager
        pm = ProfileManager()
        profiles = pm.list_profiles()
        if not profiles:
            print("\n  No profiles found. Run a replay first to create one.")
            pygame.quit()
            return

        # Pick profile
        profile = None
        if args.profile:   # name argument given
            profile = pm.find_profile(args.profile)
            if not profile:
                print(f"  Profile not found: '{args.profile}'")

        if not profile:
            print("\n  Profiles:")
            for i, p in enumerate(profiles):
                n = len(p.get("replays", []))
                print(f"    [{i+1}] {p['display_name']}  ({n} replay{'s' if n != 1 else ''})")
            raw = input(f"  Choose [1]: ").strip()
            try:
                profile = profiles[int(raw) - 1]
            except Exception:
                profile = profiles[0]

        _run_profile_loop(screen, profile, songs_folder)
        pygame.quit()
        return

    # --- Resolve beatmap search root (stable Songs folder or lazer files/) ---
    portable = args.portable
    if not portable and not args.mass_add:
        songs_folder = (Path(args.songs) if args.songs
                        else cfg.get_songs_folder() or detect_songs_folder()
                             or detect_lazer_folder())
        if songs_folder and songs_folder.exists():
            if "lazer" in str(songs_folder).lower() or not list(songs_folder.rglob("*.osu"))[:1]:
                print(f"Using osu! lazer files: {songs_folder}")
        else:
            print("Could not auto-detect osu! Songs folder or lazer files directory.")
            print("  Stable Songs folder: e.g. C:\\Users\\you\\AppData\\Local\\osu!\\Songs")
            print("  Lazer files folder:  e.g. ~/.local/share/osu/files")
            sf = input("Enter path (or press Enter for portable mode): ").strip().strip('"\'')
            if sf:
                songs_folder = Path(sf)
                if not songs_folder.exists():
                    print(f"Error: path not found: {songs_folder}")
                    sys.exit(1)
                cfg.set("songs_folder", str(songs_folder))
            else:
                portable = True
                songs_folder = None
    elif args.mass_add:
        songs_folder = (Path(args.songs) if args.songs
                        else cfg.get_songs_folder() or detect_songs_folder()
                             or detect_lazer_folder())
        if not songs_folder or not songs_folder.exists():
            print("Could not auto-detect osu! Songs folder or lazer files directory.")
            sf = input("Enter path to Songs folder or lazer files/ directory: ").strip().strip('"\'')
            songs_folder = Path(sf)
            if not songs_folder.exists():
                print(f"Error: path not found: {songs_folder}")
                sys.exit(1)
            cfg.set("songs_folder", str(songs_folder))

    # --- Mass-add mode ---
    if args.mass_add:
        from mass_add import run_mass_add
        run_mass_add(songs_folder, screen)
        pygame.quit()
        return

    # --- Replay file ---
    replay_path = args.replay
    if not replay_path:
        osr_files = list(Path(".").glob("*.osr"))
        if len(osr_files) == 1:
            replay_path = str(osr_files[0])
            print(f"Auto-detected replay: {replay_path}")
        elif osr_files:
            print("Multiple .osr files found:")
            for i, f in enumerate(osr_files):
                print(f"  [{i}] {f.name}")
            idx = input("Enter number: ").strip()
            replay_path = str(osr_files[int(idx)])
        else:
            replay_path = input("Enter path to .osr replay file: ").strip().strip('"\'')

    replay_path = Path(replay_path)
    if not replay_path.exists():
        print(f"Error: {replay_path} not found")
        sys.exit(1)

    # --- Parse replay ---
    loading_screen(screen, "Parsing replay...")
    print(f"Parsing: {replay_path.name}")
    replay_obj = parse_osr(str(replay_path))
    if replay_obj.mode != 1:
        print(f"Warning: mode={replay_obj.mode}, expected Taiko (1)")

    # --- Portable mode: skip beatmap lookup ---
    if portable:
        from osu_parser import NullBeatmap
        from analyzer import analyze_portable
        loading_screen(screen, "Analyzing (portable mode)...")
        beatmap_obj  = NullBeatmap()
        analysis_obj = analyze_portable(replay_obj)
        osu_path     = None
        print(f"Portable mode — playstyle analysis only")
        print(f"Player: {replay_obj.player_name}  Mods: {replay_obj.mod_string}")
        ps = analysis_obj.playstyle
        print(f"Playstyle: {ps.name}  alt={ps.alt_rate*100:.0f}%  "
              f"don_fa={ps.don_finger_alt*100:.0f}%  kat_fa={ps.kat_finger_alt*100:.0f}%")
    else:
        # --- Full mode: find and load beatmap ---
        loading_screen(screen, "Searching for beatmap...", f"MD5: {replay_obj.beatmap_md5}")
        print(f"Searching beatmap MD5: {replay_obj.beatmap_md5}")

        def status_cb(done, total):
            pct = int(done / max(1, total) * 100)
            loading_screen(screen, f"Scanning beatmaps... {pct}%", f"{done}/{total} files")

        osu_path = find_beatmap_by_md5(songs_folder, replay_obj.beatmap_md5, status_cb)
        if not osu_path:
            # Beatmap not found — offer portable mode fallback
            loading_screen(screen, "Beatmap not found — check terminal")
            print(f"\nBeatmap not found for MD5: {replay_obj.beatmap_md5}")
            ans = input("  Continue in portable mode? [Y/n]: ").strip().lower()
            if ans == "n":
                pygame.quit()
                sys.exit(1)
            from osu_parser import NullBeatmap
            from analyzer import analyze_portable
            loading_screen(screen, "Analyzing (portable mode)...")
            beatmap_obj  = NullBeatmap()
            analysis_obj = analyze_portable(replay_obj)
            osu_path     = None
            portable     = True
        else:
            print(f"Found: {osu_path.name}")
            loading_screen(screen, "Loading beatmap...", osu_path.name)
            beatmap_obj = parse_osu(osu_path)
            print(f"Beatmap: {beatmap_obj.artist} - {beatmap_obj.title} [{beatmap_obj.version}]")
            loading_screen(screen, "Analyzing replay...")
            analysis_obj = analyze(replay_obj, beatmap_obj)
            print_summary(replay_obj, beatmap_obj, analysis_obj)

    # --- Profile (terminal) ---
    loading_screen(screen, "Check terminal for profile options...")
    from profile import prompt_profile
    profile = prompt_profile(replay_obj, beatmap_obj, analysis_obj,
                             osr_path=replay_path, osu_path=osu_path)

    # --- Offer profile overview ---
    start_in_profile = False
    if profile and len(profile.get("replays", [])) >= 2:
        print()
        print("  [v] View current replay   [p] Open profile overview")
        choice = input("  Choice [v]: ").strip().lower()
        start_in_profile = (choice == "p")

    _run_profile_loop(
        screen, profile, songs_folder,
        initial_replay   = None if start_in_profile else replay_obj,
        initial_beatmap  = None if start_in_profile else beatmap_obj,
        initial_analysis = None if start_in_profile else analysis_obj,
    )
    pygame.quit()


if __name__ == "__main__":
    main()
