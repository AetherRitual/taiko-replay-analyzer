"""Parse osu! beatmap (.osu) files and locate them by MD5."""
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Hit object helpers
# ---------------------------------------------------------------------------

NOTE_DON = "don"       # red, inner
NOTE_KAT = "kat"       # blue, outer
NOTE_ROLL = "roll"     # drum roll (slider)
NOTE_SPIN = "spin"     # denden (spinner)

# type bits
TYPE_CIRCLE  = 1
TYPE_SLIDER  = 2
TYPE_SPINNER = 8

# hitsound bits
HS_WHISTLE = 2
HS_FINISH  = 4
HS_CLAP    = 8


def _note_type(obj_type: int, hitsound: int):
    """Return (kind, is_big) for a Taiko hit object."""
    if obj_type & TYPE_SLIDER:
        return NOTE_ROLL, bool(hitsound & HS_FINISH)
    if obj_type & TYPE_SPINNER:
        return NOTE_SPIN, False
    is_kat = bool(hitsound & (HS_WHISTLE | HS_CLAP))
    is_big = bool(hitsound & HS_FINISH)
    return (NOTE_KAT if is_kat else NOTE_DON), is_big


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TimingPoint:
    time: float          # ms
    beat_len: float      # ms per beat (positive = uninherited, negative = inherited SV)
    meter: int           # beats per measure
    uninherited: bool

    @property
    def bpm(self) -> float:
        if self.uninherited:
            return 60000.0 / self.beat_len
        return 0.0

    @property
    def sv_multiplier(self) -> float:
        """Scroll velocity multiplier for inherited points."""
        if not self.uninherited:
            return -100.0 / self.beat_len
        return 1.0


@dataclass
class HitObject:
    time: int            # ms
    kind: str            # NOTE_DON / NOTE_KAT / NOTE_ROLL / NOTE_SPIN
    is_big: bool
    end_time: int = 0    # for rolls and spins


@dataclass
class BeatmapInfo:
    title: str
    artist: str
    creator: str
    version: str         # difficulty name
    beatmap_id: int
    beatmapset_id: int
    audio_file: str
    audio_lead_in: int   # ms
    od: float
    hp: float
    timing_points: List[TimingPoint]
    hit_objects: List[HitObject]
    folder: Path

    @property
    def audio_path(self) -> Path:
        return self.folder / self.audio_file

    def bpm_at(self, time_ms: float) -> float:
        """Return BPM at the given time."""
        bpm = 120.0
        for tp in self.timing_points:
            if tp.time > time_ms:
                break
            if tp.uninherited:
                bpm = tp.bpm
        return bpm

    def timing_point_at(self, time_ms: float) -> 'TimingPoint | None':
        """Return the active uninherited TimingPoint at time_ms, or None."""
        active = None
        for tp in self.timing_points:
            if tp.time > time_ms:
                break
            if tp.uninherited:
                active = tp
        return active

    def adjusted_od(self, mods: int = 0) -> float:
        od = self.od
        if mods & 0x10:  # HR
            od = min(10.0, od * 1.4)
        if mods & 0x02:  # EZ
            od = od * 0.5
        return od

    def hit_windows(self, mods: int = 0) -> tuple:
        """Return (great_ms, good_ms) hit windows, adjusted for mods.

        osu!Taiko formulas (each side, in game-clock ms):
            Great: 50 - 3 * OD
            Good:  120 - 8 * OD
        OD 10 → Great ±20ms, Good ±40ms.
        These are game-time values; replay timestamps are also in game time,
        so no rate adjustment is needed for DT/HT.
        """
        od = self.adjusted_od(mods)
        great = max(1.0, 50.0 - 3.0 * od)
        # osu!stable enforces a minimum good window of 50ms regardless of OD,
        # confirmed empirically (OD10 replays show hits at ±48ms scored as 100).
        good  = max(50.0, 120.0 - 8.0 * od)
        return great, good


# ---------------------------------------------------------------------------
# Null beatmap (portable / no-songs-folder mode)
# ---------------------------------------------------------------------------

class NullBeatmap:
    """Drop-in replacement for BeatmapInfo when no .osu file is available.
    All attributes return safe empty/default values so the viewer can run."""
    title         = ""
    artist        = ""
    creator       = ""
    version       = ""
    beatmap_id    = 0
    beatmapset_id = 0
    audio_file    = ""
    audio_lead_in = 0
    od            = 5.0
    hp            = 5.0
    timing_points = []
    hit_objects   = []
    notes         = []           # viewer uses beatmap.notes in some places
    folder        = Path(".")

    @property
    def audio_path(self) -> Path:
        return Path(".")

    def bpm_at(self, time_ms: float) -> float:
        return 0.0

    def timing_point_at(self, time_ms: float):
        return None

    def adjusted_od(self, mods: int = 0) -> float:
        return self.od

    def hit_windows(self, mods: int = 0) -> tuple:
        return (50.0, 120.0)     # OD 5 defaults


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _parse_section(text: str, section: str) -> dict:
    """Extract key:value pairs from a named section."""
    result = {}
    in_section = False
    for line in text.splitlines():
        line = line.strip()
        if line == f"[{section}]":
            in_section = True
            continue
        if in_section:
            if line.startswith("["):
                break
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip()
    return result


def _parse_list_section(text: str, section: str) -> List[str]:
    lines = []
    in_section = False
    for line in text.splitlines():
        line = line.strip()
        if line == f"[{section}]":
            in_section = True
            continue
        if in_section:
            if line.startswith("["):
                break
            if line and not line.startswith("//"):
                lines.append(line)
    return lines


def parse_osu(path: Path) -> BeatmapInfo:
    text = path.read_text(encoding="utf-8", errors="replace")

    gen  = _parse_section(text, "General")
    meta = _parse_section(text, "Metadata")
    diff = _parse_section(text, "Difficulty")

    # Timing points
    tp_lines = _parse_list_section(text, "TimingPoints")
    timing_points = []
    for line in tp_lines:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        t    = float(parts[0])
        bl   = float(parts[1])
        meter = int(parts[2]) if len(parts) > 2 else 4
        uninherited = bool(int(parts[6])) if len(parts) > 6 else True
        timing_points.append(TimingPoint(t, bl, meter, uninherited))

    # Hit objects
    obj_lines = _parse_list_section(text, "HitObjects")
    hit_objects = []
    for line in obj_lines:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        obj_time = int(parts[2])
        obj_type = int(parts[3])
        hitsound = int(parts[4])
        kind, is_big = _note_type(obj_type, hitsound)
        end_time = obj_time
        if kind == NOTE_ROLL:
            # slider: extra field has length; compute end_time from timing
            # Simplified: read end time from param 5 if available
            # Format for slider: x,y,time,type,hitSound,curveType|...,slides,length,...
            # For taiko sliders end time is approximate; will compute properly below
            try:
                slides = int(parts[6]) if len(parts) > 6 else 1
                length = float(parts[7]) if len(parts) > 7 else 0
                # Find beat length at this time
                beat_len = 500.0  # fallback 120bpm
                sv = 1.0
                for tp in timing_points:
                    if tp.time > obj_time:
                        break
                    if tp.uninherited:
                        beat_len = tp.beat_len
                    else:
                        sv = tp.sv_multiplier
                # velocity = (100 * sv_base * sv) / beat_len  pixels/ms? no...
                # end_time = time + (length / (100 * sv_base * sv)) * beat_len * slides
                sv_base = 1.4  # SliderMultiplier from Difficulty (not always 1.4, but good default)
                # Will be re-computed properly if needed
                end_time = int(obj_time + (length / (100 * sv_base * sv)) * beat_len * slides)
            except Exception:
                end_time = obj_time + 1000
        elif kind == NOTE_SPIN:
            # spinner: endTime is in parts[5]
            try:
                end_time = int(parts[5])
            except Exception:
                end_time = obj_time + 1000
        hit_objects.append(HitObject(obj_time, kind, is_big, end_time))

    # Re-compute slider end times with proper SliderMultiplier
    slider_mult = float(diff.get("SliderMultiplier", "1.4"))
    for obj in hit_objects:
        if obj.kind == NOTE_ROLL:
            # Recalculate with correct SliderMultiplier
            line = obj_lines[hit_objects.index(obj)]
            parts = line.split(",")
            try:
                slides = int(parts[6])
                length = float(parts[7])
                beat_len = 500.0
                sv = 1.0
                for tp in timing_points:
                    if tp.time > obj.time:
                        break
                    if tp.uninherited:
                        beat_len = tp.beat_len
                    else:
                        sv = tp.sv_multiplier
                obj.end_time = int(obj.time + (length / (100 * slider_mult * sv)) * beat_len * slides)
            except Exception:
                pass

    return BeatmapInfo(
        title=meta.get("Title", "Unknown"),
        artist=meta.get("Artist", "Unknown"),
        creator=meta.get("Creator", "Unknown"),
        version=meta.get("Version", "Unknown"),
        beatmap_id=int(meta.get("BeatmapID", "0")),
        beatmapset_id=int(meta.get("BeatmapSetID", "0")),
        audio_file=gen.get("AudioFilename", "audio.mp3"),
        audio_lead_in=int(gen.get("AudioLeadIn", "0")),
        od=float(diff.get("OverallDifficulty", "5")),
        hp=float(diff.get("HPDrainRate", "5")),
        timing_points=timing_points,
        hit_objects=hit_objects,
        folder=path.parent,
    )


# ---------------------------------------------------------------------------
# MD5 cache
# ---------------------------------------------------------------------------

_CACHE_PATH = Path(__file__).parent / "beatmap_cache.json"
# First 18 bytes of every .osu file
_OSU_MAGIC  = b"osu file format v"


def _load_md5_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_md5_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# MD5 search
# ---------------------------------------------------------------------------

def find_beatmap_by_md5(search_root: Path, target_md5: str,
                         status_cb=None) -> Optional[Path]:
    """Find the .osu file whose content MD5 matches target_md5.

    Works for both osu! stable (Songs folder with *.osu files) and osu! lazer
    (content-addressable files/ directory where beatmaps have no extension).
    Results are cached in beatmap_cache.json so subsequent lookups are instant.
    """
    # Cache hit
    cache = _load_md5_cache()
    if target_md5 in cache:
        cached = Path(cache[target_md5])
        if cached.exists():
            return cached
        # Stale entry — remove and fall through to rescan
        del cache[target_md5]
        _save_md5_cache(cache)

    # Collect candidates: prefer *.osu (stable); if none found, scan all files
    # (lazer stores beatmaps as hash-named blobs with no extension)
    osu_files = list(search_root.rglob("*.osu"))
    if osu_files:
        candidates = osu_files
        check_magic = False   # stable files are always .osu — no need to sniff
    else:
        candidates = [f for f in search_root.rglob("*") if f.is_file()]
        check_magic = True    # skip non-.osu blobs cheaply via magic bytes

    total = len(candidates)
    for i, f in enumerate(candidates):
        if status_cb and i % 100 == 0:
            status_cb(i, total)
        try:
            if check_magic:
                # Read just the header to skip audio/image blobs quickly
                with open(f, "rb") as fh:
                    if fh.read(len(_OSU_MAGIC)) != _OSU_MAGIC:
                        continue
            content = f.read_bytes()
            if hashlib.md5(content).hexdigest() == target_md5:
                cache[target_md5] = str(f)
                _save_md5_cache(cache)
                return f
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------

def detect_lazer_folder() -> Optional[Path]:
    """Return the osu! lazer files/ directory if it exists."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        candidates = [Path(appdata) / "osu" / "files"] if appdata else []
    elif sys.platform == "darwin":
        candidates = [
            Path.home() / "Library" / "Application Support" / "osu" / "files"
        ]
    else:
        candidates = [Path.home() / ".local" / "share" / "osu" / "files"]
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    return None


def detect_songs_folder() -> Optional[Path]:
    """Try common osu! stable install locations."""
    candidates = [
        # Windows
        Path.home() / "AppData" / "Local" / "osu!" / "Songs",
        Path.home() / "AppData" / "Roaming" / "osu!" / "Songs",
        # Linux (wine / native)
        Path.home() / ".local/share/osu-wine/osu!/Songs",
        Path.home() / ".local/share/osu!/Songs",
        Path.home() / "osu!/Songs",
        Path("/opt/osu!/Songs"),
    ]
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    return None
