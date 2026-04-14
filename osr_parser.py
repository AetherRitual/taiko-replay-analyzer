"""Parse osu! replay (.osr) files."""
import struct
import lzma
from dataclasses import dataclass
from typing import List

# Key bitmask constants (Taiko mode)
# Empirically verified: M1+K1 = Don (centre), M2+K2 = Kat (rim)
KEY_M1 = 1   # Don  key A  (M1 = osu! "Mouse 1")
KEY_M2 = 2   # Kat  key A  (M2 = osu! "Mouse 2")
KEY_K1 = 4   # Don  key B  (K1 = osu! "Keyboard 1")
KEY_K2 = 8   # Kat  key B  (K2 = osu! "Keyboard 2")

# Grouped by note type
DON_MASK = KEY_M1 | KEY_K1   # 5
KAT_MASK = KEY_M2 | KEY_K2   # 10

# Mods
MOD_HIDDEN   = 1 << 3
MOD_HARDROCK = 1 << 4
MOD_DOUBLETIME = 1 << 6
MOD_HALFTIME = 1 << 8
MOD_NIGHTCORE = 1 << 9
MOD_FLASHLIGHT = 1 << 10

MOD_NAMES = {
    MOD_HIDDEN: "HD", MOD_HARDROCK: "HR", MOD_DOUBLETIME: "DT",
    MOD_HALFTIME: "HT", MOD_NIGHTCORE: "NC", MOD_FLASHLIGHT: "FL",
}


@dataclass
class ReplayFrame:
    t: int        # absolute time in ms (sum of deltas)
    x: float
    y: float
    keys: int     # current key bitmask


@dataclass
class HitEvent:
    """A detected key press event (transition 0→1)."""
    t: int
    new_keys: int   # which keys newly pressed this event
    all_keys: int   # all keys currently held


@dataclass
class OsrReplay:
    mode: int        # 1 = Taiko
    version: int
    beatmap_md5: str
    player_name: str
    replay_md5: str
    n300: int
    n100: int
    n50: int
    ngeki: int       # max 300s in Taiko
    nkatu: int       # 200s in Taiko
    nmiss: int
    score: int
    max_combo: int
    perfect: bool
    mods: int
    life_bar: str
    timestamp: int
    frames: List[ReplayFrame]
    hit_events: List[HitEvent]
    score_id: int

    @property
    def mod_string(self) -> str:
        parts = [name for mask, name in MOD_NAMES.items() if self.mods & mask]
        return "+".join(parts) if parts else "NM"

    @property
    def mod_rate(self) -> float:
        """Audio/gameplay speed multiplier from mods."""
        if self.mods & (MOD_DOUBLETIME | MOD_NIGHTCORE):
            return 1.5
        if self.mods & MOD_HALFTIME:
            return 0.75
        return 1.0

    @property
    def accuracy(self) -> float:
        total = self.n300 + self.n100 + self.nmiss
        if total == 0:
            return 100.0
        return (self.n300 + 0.5 * self.n100) / total * 100.0


def _read_uleb128(data: bytes, offset: int):
    result, shift = 0, 0
    while True:
        b = data[offset]; offset += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, offset


def _read_osu_str(data: bytes, offset: int):
    flag = data[offset]; offset += 1
    if flag == 0x00:
        return "", offset
    if flag != 0x0B:
        raise ValueError(f"Unknown string flag {flag:#x} at {offset-1}")
    length, offset = _read_uleb128(data, offset)
    s = data[offset:offset + length].decode("utf-8", errors="replace")
    return s, offset + length


def _extract_hit_events(frames: List[ReplayFrame]) -> List[HitEvent]:
    """Detect key press transitions (0→1) in the frame sequence."""
    events = []
    prev_keys = 0
    for f in frames:
        new = f.keys & ~prev_keys
        if new:
            events.append(HitEvent(f.t, new, f.keys))
        prev_keys = f.keys
    return events


def parse_osr(path: str) -> OsrReplay:
    with open(path, "rb") as _f:
        data = _f.read()
    o = 0

    mode = data[o]; o += 1
    ver = struct.unpack_from("<i", data, o)[0]; o += 4
    bmd5, o = _read_osu_str(data, o)
    pname, o = _read_osu_str(data, o)
    rmd5, o = _read_osu_str(data, o)

    n300, n100, n50 = struct.unpack_from("<HHH", data, o); o += 6
    ngeki, nkatu, nmiss = struct.unpack_from("<HHH", data, o); o += 6
    score = struct.unpack_from("<i", data, o)[0]; o += 4
    mcombo = struct.unpack_from("<H", data, o)[0]; o += 2
    perfect = bool(data[o]); o += 1
    mods = struct.unpack_from("<i", data, o)[0]; o += 4

    lifebar, o = _read_osu_str(data, o)
    tstamp = struct.unpack_from("<q", data, o)[0]; o += 8

    rlen = struct.unpack_from("<i", data, o)[0]; o += 4
    compressed = data[o:o + rlen]; o += rlen

    raw = lzma.decompress(compressed, format=lzma.FORMAT_ALONE)

    frames = []
    abs_t = 0
    for chunk in raw.decode("utf-8", errors="replace").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("|")
        if len(parts) < 4:
            continue
        td = int(parts[0])
        if td == -12345:
            continue
        abs_t += td
        frames.append(ReplayFrame(abs_t, float(parts[1]), float(parts[2]), int(parts[3])))

    hit_events = _extract_hit_events(frames)

    score_id = 0
    if o + 8 <= len(data):
        score_id = struct.unpack_from("<q", data, o)[0]

    return OsrReplay(
        mode, ver, bmd5, pname, rmd5,
        n300, n100, n50, ngeki, nkatu, nmiss,
        score, mcombo, perfect, mods, lifebar, tstamp,
        frames, hit_events, score_id
    )
