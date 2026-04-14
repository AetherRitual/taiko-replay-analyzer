"""Batch-import multiple .osr replay files into player profiles."""
from pathlib import Path


def mass_add_replays(osr_paths: list, songs_folder: Path,
                     status_fn=None) -> dict:
    """
    Parse and add a list of .osr files to profiles.
    status_fn(i, total, filename) is called before each file if provided.
    Returns {player_name: {"added": N, "skipped": N, "errors": N}}.
    """
    from osr_parser import parse_osr
    from osu_parser import parse_osu, find_beatmap_by_md5
    from analyzer import analyze
    from profile import ProfileManager

    pm    = ProfileManager()
    stats: dict = {}
    total = len(osr_paths)

    for i, osr_path in enumerate(osr_paths):
        osr_path = Path(osr_path)
        player   = "?"
        try:
            if status_fn:
                status_fn(i, total, osr_path.name)

            replay = parse_osr(str(osr_path))
            player = replay.player_name

            if player not in stats:
                stats[player] = {"added": 0, "skipped": 0, "errors": 0}

            osu_path = find_beatmap_by_md5(songs_folder, replay.beatmap_md5)
            if not osu_path:
                print(f"  ⚠  Beatmap not found for {osr_path.name}")
                stats[player]["errors"] += 1
                continue

            beatmap  = parse_osu(osu_path)
            analysis = analyze(replay, beatmap)

            profile = pm.find_profile(player)
            if profile is None:
                profile = pm.create_profile(player)

            record = pm.build_record(replay, beatmap, analysis,
                                     osr_path=osr_path, osu_path=osu_path)
            if pm.add_replay(profile, record):
                stats[player]["added"] += 1
            else:
                stats[player]["skipped"] += 1

        except Exception as exc:
            if player not in stats:
                stats[player] = {"added": 0, "skipped": 0, "errors": 0}
            stats[player]["errors"] += 1
            print(f"  ✗  Error on {osr_path.name}: {exc}")

    return stats


def run_mass_add(songs_folder: Path, screen=None) -> None:
    """Interactive terminal flow for mass-adding replays."""
    print("\n  Mass-add replays to profiles")
    print("  ──────────────────────────────────────")
    folder_str = input("  Folder containing .osr files: ").strip().strip('"\'')
    osr_folder = Path(folder_str)
    if not osr_folder.exists():
        print(f"  Folder not found: {osr_folder}")
        return

    osr_files = list(osr_folder.glob("*.osr"))
    if not osr_files:
        osr_files = list(osr_folder.rglob("*.osr"))  # try recursively

    if not osr_files:
        print(f"  No .osr files found in {osr_folder}")
        return

    print(f"  Found {len(osr_files)} replay file(s).")
    ans = input("  Proceed? [Y/n]: ").strip().lower()
    if ans == "n":
        return

    from main import loading_screen as _ls

    def status(i, total, name):
        pct = int((i + 1) / max(1, total) * 100)
        print(f"  [{i+1}/{total}]  {name}", end='\r', flush=True)
        if screen:
            _ls(screen, f"Mass-adding replays... {i+1}/{total}  ({pct}%)", name)

    result = mass_add_replays(osr_files, songs_folder, status_fn=status)
    print()  # newline after \r progress

    print("\n  Done!")
    print("  ─────────────────────────────────────")
    for player, s in sorted(result.items()):
        print(f"  {player}:  {s['added']} added  "
              f"{s['skipped']} already present  {s['errors']} errors")
    print()
