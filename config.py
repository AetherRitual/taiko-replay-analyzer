"""Simple key=value config file stored next to the application."""
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.txt"

_DEFAULTS = {
    "songs_folder": "",
}

_COMMENTS = {
    "songs_folder": "Path to your osu! Songs folder",
}


def _parse(path: Path) -> dict:
    result = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return result


def _profile_lines() -> list[str]:
    """Return comment lines listing current profiles."""
    try:
        from profile import ProfileManager, PROFILE_DIR
        lines = [
            "# ── Profiles " + "─" * 54,
            f"# Stored at: {PROFILE_DIR}",
            "# To delete:  python3 main.py --delete-profile \"PlayerName\"",
            "#",
        ]
        profiles = ProfileManager().list_profiles()
        if profiles:
            for p in sorted(profiles, key=lambda x: x["display_name"].lower()):
                n = len(p.get("replays", []))
                lines.append(f"#   {p['display_name']:<24} ({n} replay{'s' if n != 1 else ''})")
        else:
            lines.append("#   (no profiles yet)")
        lines.append("")
    except Exception:
        lines = []
    return lines


def _write(data: dict) -> None:
    lines = []
    for key, val in data.items():
        if key in _COMMENTS:
            lines.append(f"# {_COMMENTS[key]}")
        lines.append(f"{key} = {val}")
        lines.append("")
    lines += _profile_lines()
    text = "\n".join(lines)
    _CONFIG_PATH.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


def get(key: str) -> str:
    """Return config value, or empty string if not set."""
    data = _parse(_CONFIG_PATH)
    return data.get(key, _DEFAULTS.get(key, ""))


def set(key: str, value: str) -> None:
    """Write a single key to the config file, preserving other keys."""
    data = {**_DEFAULTS, **_parse(_CONFIG_PATH)}
    data[key] = value
    _write(data)


def refresh() -> None:
    """Rewrite config.txt to update the profiles list without changing settings."""
    data = {**_DEFAULTS, **_parse(_CONFIG_PATH)}
    _write(data)


def get_songs_folder() -> Path | None:
    """Return the configured Songs folder, or None if not set / doesn't exist."""
    val = get("songs_folder")
    if val:
        p = Path(val)
        if p.exists():
            return p
    return None
