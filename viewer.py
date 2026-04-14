"""Pygame-based real-time osu!Taiko replay viewer — skin-aware, resizable."""
import math
import datetime
import pygame
import pygame.gfxdraw

from ui_common import sysfont as _sysfont, FONT_PREF as _FONT_PREF, FONT_MONO_PREF as _FONT_MONO_PREF

from osr_parser import OsrReplay, KEY_M1, KEY_M2, KEY_K1, KEY_K2
from osu_parser import BeatmapInfo, NOTE_DON, NOTE_KAT, NOTE_ROLL, NOTE_SPIN
from analyzer import AnalysisResult, HIT_300, HIT_100, HIT_MISS
from skin import SkinLoader, DEFAULT_SKIN

# ---------------------------------------------------------------------------
# Base (virtual) resolution — 1920×1080 target
# All layout constants in these virtual units; vx/vy/vs scale to actual pixels.
# ---------------------------------------------------------------------------
BASE_W = 1920
BASE_H = 1080

# ---------------------------------------------------------------------------
# Two-column layout
# ---------------------------------------------------------------------------
COL_SPLIT   = 1200   # x: left gameplay/data column ends here
ROW_SPLIT   = 352    # y: gameplay area ends / data area begins (left col only)

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
BG          = (18,  20,  28)
PANEL_BG    = (22,  24,  36)
LANE_BG_COL = (24,  26,  36)
LINE_COL    = (55,  60,  88)
SEP_COL     = (40,  44,  65)
TEXT_COL    = (220, 225, 240)
DIM_TEXT    = (120, 128, 160)
DON_COL     = (255,  75,  75)
KAT_COL     = ( 90, 160, 255)
DON_BIG_COL = (255,  90,  90)   # same red family as DON_COL, not orange
KAT_BIG_COL = ( 50, 200, 255)
H300_COL    = ( 80, 230, 100)
H100_COL    = (240, 220,  50)
MISS_COL    = (210,  55,  55)
ROLL_COL    = (230, 200,  50)
SPIN_COL    = (160, 100, 255)
TIMELINE_BG = ( 30,  33,  50)
GOLD        = (230, 185,  50)
ACCENT      = (100, 190, 255)

# ---------------------------------------------------------------------------
# Gameplay area layout (left column, top section — 0..COL_SPLIT × 0..ROW_SPLIT)
# ROW_SPLIT is kept tight: just header + lane + key indicators + a little padding.
# ---------------------------------------------------------------------------
HEADER_H    = 50     # song-info bar height
LANE_CY     = 185    # lane centre Y  (HEADER_H + 50 + LANE_HALF)
LANE_HALF   = 85     # lane half-height → lane from Y=100 to Y=270
HIT_X       = 160    # judgment zone X
NOTE_R      = 44     # small note radius  (38 × 1.15)
NOTE_R_BIG  = 60     # big note radius    (52 × 1.15)
APPROACH_MS = 2000   # ms of notes visible ahead
RIGHT_EDGE  = 1185   # rightmost X where notes are rendered

# ---------------------------------------------------------------------------
# Data area layout (left column, bottom section — 0..COL_SPLIT × ROW_SPLIT..BASE_H)
# ---------------------------------------------------------------------------
TAB_H       = 34               # tab bar height
TAB_CONTENT = ROW_SPLIT + TAB_H + 8  # first usable y inside tab content

HIT_ERR_Y   = TAB_CONTENT + 10
HIT_ERR_H   = 88
LOCAL_UR_Y  = TAB_CONTENT + 116
LOCAL_UR_H  = 75
COMBO_G_Y   = TAB_CONTENT + 210
COMBO_G_H   = 65
TIMELINE_Y  = TAB_CONTENT + 296
TIMELINE_H  = 44
TIMELINE_X  = 55
TIMELINE_W  = COL_SPLIT - 55   # 1145

# ---------------------------------------------------------------------------
# Right stats panel (right column — COL_SPLIT..BASE_W × 0..BASE_H)
# ---------------------------------------------------------------------------
PANEL_X     = COL_SPLIT + 24   # 1224
PANEL_Y     = 20
PANEL_W     = BASE_W - PANEL_X - 20   # 676
PANEL_H     = BASE_H - 40             # 1040

SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


# ---------------------------------------------------------------------------
# Hit flash
# ---------------------------------------------------------------------------

class HitFlash:
    LIFE = 420  # ms

    def __init__(self, t: int, result: str, offset: float):
        self.t = t
        self.result = result
        self.offset = offset

    def alive(self, now: int) -> bool:
        return now - self.t < self.LIFE

    def alpha(self, now: int) -> float:
        return max(0.0, 1.0 - (now - self.t) / self.LIFE)


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------

class Viewer:
    def __init__(self, replay: OsrReplay, beatmap: BeatmapInfo,
                 analysis: AnalysisResult, existing_screen=None,
                 skin_path=DEFAULT_SKIN, profile=None):

        if existing_screen is None:
            pygame.init()
            pygame.mixer.pre_init(44100, -16, 2, 512)
            pygame.mixer.init()
            self.screen = pygame.display.set_mode(
                (BASE_W, BASE_H), pygame.RESIZABLE | pygame.DOUBLEBUF)
        else:
            self.screen = pygame.display.set_mode(
                existing_screen.get_size(),
                pygame.RESIZABLE | pygame.DOUBLEBUF)

        self.W, self.H = self.screen.get_size()
        pygame.display.set_caption(
            f"Taiko Replay — {beatmap.artist} - {beatmap.title} "
            f"[{beatmap.version}]  |  {replay.player_name}")

        self.clock    = pygame.time.Clock()
        self.replay   = replay
        self.beatmap  = beatmap
        self.analysis = analysis
        self.skin     = SkinLoader(skin_path)
        self.profile  = profile   # may be None
        # Portable mode: beatmap has no notes/audio (NullBeatmap)
        self.portable = not bool(beatmap.hit_objects)

        self._build_fonts()

        # Game state
        self.game_time    = -2000.0
        self.playing      = False
        self.speed_idx    = 3          # index into SPEEDS → 1.0×
        self.dragging     = False

        # Audio
        self.audio_loaded    = False
        self.audio_start_gt  = float(-beatmap.audio_lead_in)
        self._audio_seek_ms  = 0
        self._sync_cooldown  = 0.0

        # Replay frame cursor
        self.current_keys  = 0
        self._frame_cursor = 0

        # Hit flashes
        self.flashes: list[HitFlash] = []
        self._flash_idx = 0

        # UI
        self.problem_scroll = 0
        self._open_profile  = False

        # Volume
        self._volume       = 0.8
        self._vol_dragging = False
        self._vol_rect     = None
        pygame.mixer.music.set_volume(self._volume)

        # Scroll speed (how fast notes appear to approach, independent of audio)
        self._scroll_speed = 1.5   # default; higher = notes arrive sooner = busier visual

        # Interactive button rects (populated each draw call)
        self._btn_play    = None
        self._btn_spd_dn  = None
        self._btn_spd_up  = None
        self._btn_scr_dn  = None
        self._btn_scr_up  = None

        # Pre-compute note-result lookup by note time
        self.results_by_time: dict = {}
        for r in analysis.note_results:
            self.results_by_time[r.note.time] = r

        # Pre-computed summary data
        self._key_counts     = self._compute_key_counts()
        self._combo_data     = self._compute_combo_data()
        self._pattern_data   = self._compute_pattern_stats()
        self._bpm_acc_data   = self._build_bpm_acc_data()   # profile-aware

        # Tab state (0 = Data, 1 = Pattern Analysis)
        self._active_tab  = 0
        self._tab_rects   = []

        self.song_end_ms = self._calc_song_end()
        self._load_audio()

    # ------------------------------------------------------------------
    # Coordinate helpers (virtual → screen pixels)
    # ------------------------------------------------------------------

    def vx(self, x: float) -> int:
        return int(x * self.W / BASE_W)

    def vy(self, y: float) -> int:
        return int(y * self.H / BASE_H)

    def vs(self, s: float) -> int:
        return max(1, int(s * min(self.W / BASE_W, self.H / BASE_H)))

    def _note_x(self, note_time: int) -> int:
        dt             = note_time - self.game_time
        effective_ms   = APPROACH_MS / max(0.1, self._scroll_speed)
        frac           = dt / effective_ms
        return self.vx(HIT_X + frac * (RIGHT_EDGE - HIT_X))

    def _timeline_x(self, t_ms: float) -> int:
        frac = max(0.0, min(1.0, t_ms / max(1, self.song_end_ms)))
        return self.vx(TIMELINE_X) + int(frac * self.vx(TIMELINE_W))

    def _timeline_to_ms(self, px: int) -> float:
        frac = (px - self.vx(TIMELINE_X)) / max(1, self.vx(TIMELINE_W))
        return max(0.0, min(self.song_end_ms, frac * self.song_end_ms))

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------

    def _build_fonts(self):
        s = min(self.W / BASE_W, self.H / BASE_H)
        self.f_xs   = _sysfont(_FONT_PREF, max(11, int(13 * s)))
        self.f_sm   = _sysfont(_FONT_PREF, max(14, int(16 * s)))
        self.f_md   = _sysfont(_FONT_PREF, max(16, int(20 * s)))
        self.f_lg   = _sysfont(_FONT_PREF, max(18, int(26 * s)), bold=True)
        self.f_xl   = _sysfont(_FONT_PREF, max(22, int(36 * s)), bold=True)
        self.f_mono = _sysfont(_FONT_MONO_PREF, max(14, int(16 * s)))

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _compute_key_counts(self) -> dict:
        counts = {KEY_M1: 0, KEY_K1: 0, KEY_M2: 0, KEY_K2: 0}
        for ev in self.replay.hit_events:
            for k in (KEY_M1, KEY_K1, KEY_M2, KEY_K2):
                if ev.new_keys & k:
                    counts[k] += 1
        return counts

    def _compute_combo_data(self) -> list:
        """Returns list of (note_time_ms, combo) for all scored notes."""
        data = []
        combo = 0
        for r in self.analysis.note_results:
            if r.is_miss:
                combo = 0
            else:
                combo += 1
            data.append((r.note.time, combo))
        return data

    def _compute_pattern_stats(self) -> list:
        """Delegate to shared pattern_analysis module."""
        from pattern_analysis import compute_pattern_stats
        return compute_pattern_stats(
            self.analysis, self.beatmap,
            mod_rate=self.replay.mod_rate)

    def _build_bpm_acc_data(self) -> tuple[list, int]:
        """
        Return (bpm_acc_list, replay_count).
        Uses aggregated profile data when available (≥2 replays), else single-replay data.
        """
        if self.profile:
            from profile import ProfileManager
            pm   = ProfileManager()
            data = pm.aggregated_bpm_acc(self.profile, min_total=10)
            n    = len(self.profile.get("replays", []))
            if len(data) >= 2:
                return data, n

        # Fall back to this replay only — use effective BPM (raw × mod_rate)
        from osu_parser import NOTE_DON, NOTE_KAT
        rate  = self.replay.mod_rate
        notes = [r for r in self.analysis.note_results
                 if r.note.kind in (NOTE_DON, NOTE_KAT)]
        buckets: dict = {}
        for r in notes:
            raw_bpm = self.beatmap.bpm_at(r.note.time)
            eff_bpm = round(raw_bpm * rate / 10) * 10
            if eff_bpm not in buckets:
                buckets[eff_bpm] = {"n300": 0, "n100": 0, "nmiss": 0}
            b = buckets[eff_bpm]
            if r.is_great:   b["n300"] += 1
            elif r.is_good:  b["n100"] += 1
            else:            b["nmiss"] += 1
        result = []
        for bpm, d in sorted(buckets.items()):
            total = d["n300"] + d["n100"] + d["nmiss"]
            if total >= 3:
                acc = (d["n300"] + 0.5 * d["n100"]) / total * 100
                result.append({"bpm": bpm, "acc": acc, "total": total, **d})
        return result, 1

    def _replay_date(self) -> str:
        try:
            unix_ts = (self.replay.timestamp - 621_355_968_000_000_000) / 10_000_000
            return datetime.datetime.utcfromtimestamp(unix_ts).strftime("%Y-%m-%d")
        except Exception:
            return "—"

    def _grade(self) -> tuple:
        acc  = self.replay.accuracy
        miss = self.replay.nmiss
        if acc >= 100.0:                          return "SS", (255, 215,  50)
        if acc >= 95.0 and miss == 0:             return "S",  (230, 185,  50)
        if acc >= 90.0 or (acc >= 80.0 and miss == 0): return "A", ( 80, 220, 100)
        if acc >= 80.0:                           return "B",  (100, 160, 255)
        if acc >= 70.0:                           return "C",  (200, 130, 255)
        return "D", (210, 60, 60)

    def _calc_song_end(self) -> int:
        if self.beatmap.hit_objects:
            return max(o.end_time for o in self.beatmap.hit_objects) + 3000
        return 120000

    def _load_audio(self):
        p = self.beatmap.audio_path
        if p.exists():
            try:
                pygame.mixer.music.load(str(p))
                self.audio_loaded = True
            except Exception as e:
                print(f"[audio] {e}")

    # ------------------------------------------------------------------
    # Audio control
    # ------------------------------------------------------------------

    def _audio_ms(self) -> int:
        return int(self.game_time - self.audio_start_gt)

    def _start_audio(self):
        if not self.audio_loaded:
            return
        pos = self._audio_ms()
        if pos < 0:
            pygame.mixer.music.stop()
            return
        if self._speed() == 1.0:
            try:
                pygame.mixer.music.play(start=pos / 1000.0)
                self._audio_seek_ms  = pos
                self._sync_cooldown  = 250.0
            except Exception:
                pass
        else:
            pygame.mixer.music.stop()

    def _stop_audio(self):
        pygame.mixer.music.stop()

    def _speed(self) -> float:
        return SPEEDS[self.speed_idx]

    # ------------------------------------------------------------------
    # Seek
    # ------------------------------------------------------------------

    def _seek(self, new_time: float):
        was_playing = self.playing
        self.playing = False
        self._stop_audio()
        self.game_time = new_time
        self.flashes   = []
        self._sync_cooldown = 0.0

        self._frame_cursor = 0
        self.current_keys  = 0
        for i, f in enumerate(self.replay.frames):
            if f.t <= new_time:
                self.current_keys  = f.keys
                self._frame_cursor = i + 1
            else:
                break

        self._flash_idx = 0
        for i, r in enumerate(self.analysis.note_results):
            if r.note.kind not in (NOTE_DON, NOTE_KAT):
                self._flash_idx = i + 1
                continue
            check_t = r.hit_time if not r.is_miss else r.note.time
            if check_t <= new_time:
                self._flash_idx = i + 1
            else:
                break

        if was_playing:
            self.playing = True
            self._start_audio()

    # ------------------------------------------------------------------
    # Frame advance
    # ------------------------------------------------------------------

    def _advance(self, dt_real: float):
        if not self.playing:
            return

        dt_game = dt_real * self._speed() * 1000.0
        self.game_time += dt_game

        if self._sync_cooldown > 0:
            self._sync_cooldown -= dt_game

        # Audio sync (1× only, after cooldown)
        if self.audio_loaded and self._speed() == 1.0 and self._sync_cooldown <= 0:
            pos = pygame.mixer.music.get_pos()
            if pos >= 0 and pygame.mixer.music.get_busy():
                abs_audio = self._audio_seek_ms + pos
                audio_gt  = abs_audio + self.audio_start_gt
                drift     = self.game_time - audio_gt
                if abs(drift) > 60:
                    self.game_time = audio_gt + drift * 0.1

        # Auto-start audio when we cross the audio start
        if self.audio_loaded and self._speed() == 1.0:
            apos = self._audio_ms()
            if apos >= 0 and not pygame.mixer.music.get_busy() and self.playing:
                try:
                    pygame.mixer.music.play(start=apos / 1000.0)
                    self._audio_seek_ms = apos
                    self._sync_cooldown = 250.0
                except Exception:
                    pass

        # Advance frame cursor
        while self._frame_cursor < len(self.replay.frames):
            f = self.replay.frames[self._frame_cursor]
            if f.t <= self.game_time:
                self.current_keys  = f.keys
                self._frame_cursor += 1
            else:
                break

        # Spawn hit flashes
        while self._flash_idx < len(self.analysis.note_results):
            r = self.analysis.note_results[self._flash_idx]
            if r.note.kind not in (NOTE_DON, NOTE_KAT):
                self._flash_idx += 1
                continue
            check_t = r.hit_time if not r.is_miss else r.note.time
            if check_t <= self.game_time:
                self.flashes.append(HitFlash(int(self.game_time), r.result, r.offset))
                self._flash_idx += 1
            else:
                break

        self.flashes = [f for f in self.flashes if f.alive(int(self.game_time))]

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    def _txt(self, surf, text, x, y, font=None, color=TEXT_COL, anchor="topleft"):
        font = font or self.f_md
        img  = font.render(str(text), True, color)
        rect = img.get_rect(**{anchor: (x, y)})
        surf.blit(img, rect)
        return rect

    def _aa_circle(self, surf, cx, cy, r, color):
        if r < 1:
            return
        pygame.gfxdraw.aacircle(surf, cx, cy, r, color)
        pygame.gfxdraw.filled_circle(surf, cx, cy, r, color)

    def _note_colors(self, kind: str, is_big: bool):
        if kind == NOTE_DON:
            return (DON_BIG_COL if is_big else DON_COL), (255, 230, 190)
        return (KAT_BIG_COL if is_big else KAT_COL), (200, 230, 255)

    # ------------------------------------------------------------------
    # Left column top: gameplay area
    # ------------------------------------------------------------------

    def _draw_gameplay(self, surf):
        """Draw the top-left gameplay section: header bar + lane."""
        col_w = self.vx(COL_SPLIT)
        row_h = self.vy(ROW_SPLIT)

        # -- Song/player header bar (top of gameplay area) --
        hh = self.vy(HEADER_H)
        pygame.draw.rect(surf, PANEL_BG, (0, 0, col_w, hh))
        pygame.draw.line(surf, LINE_COL, (0, hh), (col_w, hh), 1)

        bm, rep = self.beatmap, self.replay
        rate     = rep.mod_rate
        rate_str = f"  {rate:.2f}×" if rate != 1.0 else ""
        title    = f"{bm.artist} - {bm.title}  [{bm.version}]"
        right    = f"{rep.player_name}  {rep.mod_string}{rate_str}"
        title_y = self.vy(8)
        self._txt(surf, title, self.vs(14), title_y, self.f_lg)
        self._txt(surf, right, col_w - self.vs(14), title_y,
                  self.f_md, DIM_TEXT, anchor="topright")
        if rate != 1.0:
            warn = f"⚠ Audio plays at 1× (map is {rate:.2f}×)"
            self._txt(surf, warn, self.vs(14), self.vy(36),
                      self.f_sm, (255, 180, 50))

        # -- Lane background (only within the gameplay column) --
        cy  = self.vy(LANE_CY)
        lh  = self.vs(LANE_HALF)
        hx  = self.vx(HIT_X)
        lane_rect = pygame.Rect(0, cy - lh - self.vs(12),
                                col_w, lh * 2 + self.vs(24))

        bg_surf = self.skin.bar_right(col_w, lane_rect.height)
        if bg_surf:
            surf.blit(bg_surf, (0, lane_rect.y))
        else:
            pygame.draw.rect(surf, LANE_BG_COL, lane_rect)

        # Bar-left overlay (drum zone background)
        bl_surf = self.skin.bar_left(int(hx * 1.35), lane_rect.height)
        if bl_surf:
            surf.blit(bl_surf, (0, lane_rect.y))

        pygame.draw.line(surf, LINE_COL, (0, cy - lh), (col_w, cy - lh), 1)
        pygame.draw.line(surf, LINE_COL, (0, cy + lh), (col_w, cy + lh), 1)

        # Clip rolls and notes to the play area (prevent rendering beyond RIGHT_EDGE)
        old_clip = surf.get_clip()
        surf.set_clip(pygame.Rect(0, 0, self.vx(RIGHT_EDGE), self.vy(ROW_SPLIT)))

        if self.portable:
            self._txt(surf, "Portable mode — no beatmap loaded",
                      col_w // 2, cy, self.f_md, DIM_TEXT, anchor="center")
            self._txt(surf, "Playstyle analysis available in the data panel  [D]",
                      col_w // 2, cy + self.vs(28), self.f_sm, (100, 110, 140), anchor="center")
        else:
            self._draw_rolls(surf, cy, lh)
            self._draw_notes(surf, cy, lh)

        surf.set_clip(old_clip)

        # Drum hit zone indicator (drawn on top, not clipped)
        self._draw_drum(surf, hx, cy, lh)

        # Key press indicators — below the lane's dark border
        self._draw_keys(surf, hx, cy + lh + self.vs(48))

        # BPM / time label
        if not self.portable:
            bpm     = self.beatmap.bpm_at(self.game_time)
            bpm_eff = bpm * rate
            bpm_str = (f"{bpm_eff:.0f} BPM" if rate == 1.0
                       else f"{bpm:.0f}×{rate}={bpm_eff:.0f} BPM")
            self._txt(surf, bpm_str, hx, cy + lh + self.vs(8),
                      self.f_sm, DIM_TEXT, anchor="center")

        gt_s  = self.game_time / 1000.0
        tot_s = self.song_end_ms / 1000.0
        self._txt(surf, f"{gt_s:.1f}s / {tot_s:.0f}s",
                  col_w // 2, cy + lh + self.vs(8), self.f_sm, DIM_TEXT, anchor="center")

        spd = self._speed()
        if spd != 1.0:
            self._txt(surf, f"{spd:.2f}×",
                      col_w // 2 + self.vx(160), cy + lh + self.vs(8),
                      self.f_sm, GOLD, anchor="center")

    # ------------------------------------------------------------------
    # Rolls / spins
    # ------------------------------------------------------------------

    def _draw_rolls(self, surf, cy, lh):
        for obj in self.beatmap.hit_objects:
            if obj.kind not in (NOTE_ROLL, NOTE_SPIN):
                continue
            x1 = self._note_x(obj.time)
            x2 = self._note_x(obj.end_time)
            if x2 < 0 or x1 > self.vx(RIGHT_EDGE):
                continue
            x_start = max(x1, self.vx(HIT_X))
            w = max(0, x2 - x_start)
            if w == 0:
                continue
            h  = self.vs(34) if obj.kind == NOTE_ROLL else self.vs(44)
            ry = cy - h // 2

            if obj.kind == NOTE_ROLL:
                rs = self.skin.roll_surface(w, h)
                if rs:
                    surf.blit(rs, (x_start, ry))
                else:
                    pygame.draw.rect(surf, (100, 80, 20),
                                     (x_start, ry, w, h), border_radius=self.vs(6))
                    pygame.draw.rect(surf, ROLL_COL,
                                     (x_start, ry, w, h), 2, border_radius=self.vs(6))
            else:
                pygame.draw.rect(surf, (60, 40, 100),
                                 (x_start, ry, w, h), border_radius=self.vs(8))
                pygame.draw.rect(surf, SPIN_COL,
                                 (x_start, ry, w, h), 2, border_radius=self.vs(8))
                self._txt(surf, "DENDEN", x_start + w // 2, cy,
                          self.f_sm, SPIN_COL, anchor="center")

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    def _draw_notes(self, surf, cy, lh):
        r_small = self.vs(NOTE_R)
        r_big   = self.vs(NOTE_R_BIG)
        col_px  = self.vx(COL_SPLIT)
        visible = [
            obj for obj in self.beatmap.hit_objects
            if obj.kind in (NOTE_DON, NOTE_KAT)
            and -r_big <= self._note_x(obj.time) <= col_px + r_big
        ]

        for obj in reversed(visible):
            x   = self._note_x(obj.time)
            r   = r_big if obj.is_big else r_small
            col, border_col = self._note_colors(obj.kind, obj.is_big)

            res         = self.results_by_time.get(obj.time)
            already_hit = res and not res.is_miss and res.hit_time <= self.game_time

            if already_hit:
                continue   # note is gone after being hit

            sk = self.skin.note_surface(obj.kind, obj.is_big, r * 2)
            if sk:
                surf.blit(sk, (x - r, cy - r))
            else:
                if obj.is_big:
                    self._aa_circle(surf, x, cy, r + self.vs(5), border_col)
                self._aa_circle(surf, x, cy, r, col)
                hi_r = max(1, r - self.vs(10))
                hi_c = tuple(min(255, c + 60) for c in col)
                self._aa_circle(surf, x, cy - r // 5, hi_r, hi_c)

            # Offset label near hit zone
            if res and not res.is_miss and not math.isnan(res.offset):
                if abs(x - self.vx(HIT_X)) < self.vx(90):
                    off_col = H100_COL if res.is_good else (
                        (200, 180, 255) if res.offset < 0 else (255, 200, 130))
                    self._txt(surf, f"{res.offset:+.0f}ms", x, cy - r - self.vs(18),
                              self.f_sm, off_col, anchor="center")

        self._draw_flashes(surf, cy)

    # ------------------------------------------------------------------
    # Hit flashes
    # ------------------------------------------------------------------

    def _draw_flashes(self, surf, cy):
        if not self.flashes:
            return
        flash   = self.flashes[-1]
        gt      = int(self.game_time)
        alpha   = flash.alpha(gt)
        hx      = self.vx(HIT_X)
        label_y = cy - self.vs(NOTE_R_BIG) - self.vs(55)

        if flash.result == HIT_300:
            text, col = "300", H300_COL
        elif flash.result == HIT_100:
            text, col = "100", H100_COL
        else:  # HIT_MISS
            text, col = "MISS", MISS_COL

        img = self.f_xl.render(text, True, col)
        img.set_alpha(int(255 * alpha))
        surf.blit(img, img.get_rect(center=(hx, label_y)))

    # ------------------------------------------------------------------
    # Drum (left edge, half-circle images, large)
    # ------------------------------------------------------------------

    def _draw_drum(self, surf, cx, cy, lh):
        """Draw the hit zone indicator and key-press flashes."""
        nr = int(self.vs(NOTE_R_BIG) * 0.75)

        # Judgment zone ring — sized to match big note radius
        pygame.gfxdraw.aacircle(surf, cx, cy, nr + self.vs(6), (*LINE_COL, 180))
        pygame.gfxdraw.aacircle(surf, cx, cy, nr + self.vs(3), (*LINE_COL, 120))

        # Active key-press flash
        r_flash = max(6, nr // 2)
        any_don = self.current_keys & (KEY_M1 | KEY_K1)
        any_kat = self.current_keys & (KEY_M2 | KEY_K2)

        if any_don:
            # filled inner don flash
            don_col = (*DON_BIG_COL, 200) if (self.current_keys & KEY_M1 and
                                              self.current_keys & KEY_K1) else (*DON_COL, 180)
            pygame.gfxdraw.aacircle(surf, cx, cy, r_flash, don_col)
            pygame.gfxdraw.filled_circle(surf, cx, cy, r_flash, don_col)
        if any_kat:
            # outer kat ring flash
            kat_col = (*KAT_BIG_COL, 200) if (self.current_keys & KEY_M2 and
                                              self.current_keys & KEY_K2) else (*KAT_COL, 170)
            pygame.gfxdraw.aacircle(surf, cx, cy, nr + self.vs(4), kat_col)

    # ------------------------------------------------------------------
    # Key indicators (below lane)
    # ------------------------------------------------------------------

    def _draw_keys(self, surf, cx, ky):
        labels = [
            (KEY_M2, "K", KAT_COL,  cx - self.vs(66)),
            (KEY_M1, "D", DON_COL,  cx - self.vs(22)),
            (KEY_K1, "D", DON_COL,  cx + self.vs(22)),
            (KEY_K2, "K", KAT_COL,  cx + self.vs(66)),
        ]
        kr = self.vs(16)
        for mask, label, color, kx in labels:
            pressed = bool(self.current_keys & mask)
            bg = color if pressed else (50, 55, 75)
            self._aa_circle(surf, kx, ky, kr, bg)
            self._txt(surf, label, kx, ky, self.f_sm,
                      TEXT_COL if pressed else DIM_TEXT, anchor="center")

    # ------------------------------------------------------------------
    # Left column bottom: data area (offset graph + section bars + timeline + controls)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Tab bar
    # ------------------------------------------------------------------

    _TAB_LABELS = ["Data", "Pattern Analysis"]

    def _draw_tabs(self, surf):
        col_w = self.vx(COL_SPLIT)
        ty    = self.vy(ROW_SPLIT)
        th    = self.vs(TAB_H)

        # Slight background strip for the tab bar
        pygame.draw.rect(surf, (20, 22, 33), (0, ty, col_w, th))
        pygame.draw.line(surf, LINE_COL, (0, ty), (col_w, ty), 1)

        self._tab_rects = []
        tx = self.vs(TIMELINE_X)
        for i, label in enumerate(self._TAB_LABELS):
            ls  = self.f_md.render(label, True, TEXT_COL)
            tw  = ls.get_width() + self.vs(24)
            rect = pygame.Rect(tx, ty + self.vs(5), tw, th - self.vs(10))

            active = (i == self._active_tab)
            bg_col  = (38, 42, 62) if active else (24, 26, 38)
            bdr_col = ACCENT if active else LINE_COL
            txt_col = TEXT_COL if active else DIM_TEXT

            pygame.draw.rect(surf, bg_col,  rect, border_radius=self.vs(5))
            pygame.draw.rect(surf, bdr_col, rect, 1, border_radius=self.vs(5))

            limg = self.f_md.render(label, True, txt_col)
            surf.blit(limg, limg.get_rect(center=rect.center))

            self._tab_rects.append(rect)
            tx += tw + self.vs(6)

        # Bottom border of tab bar = top of content
        pygame.draw.line(surf, LINE_COL,
                         (0, ty + th), (col_w, ty + th), 1)

    # ------------------------------------------------------------------
    # Data area router
    # ------------------------------------------------------------------

    def _draw_data(self, surf):
        self._draw_tabs(surf)
        if self._active_tab == 0:
            self._draw_data_tab(surf)
        else:
            self._draw_pattern_tab(surf)

    def _draw_data_tab(self, surf):
        self._draw_hit_error_bar(surf)
        self._draw_local_ur_graph(surf)
        self._draw_combo_graph(surf)
        self._draw_timeline(surf)
        self._draw_controls(surf)

    # ------------------------------------------------------------------
    # Hit error bar — horizontal timing distribution (inspired by danser)
    # ------------------------------------------------------------------

    def _draw_hit_error_bar(self, surf):
        gx  = self.vx(TIMELINE_X)
        gy  = self.vy(HIT_ERR_Y)
        gw  = self.vx(TIMELINE_W)
        gh  = self.vs(HIT_ERR_H)
        mid = gy + gh // 2

        great_ms, good_ms = self.beatmap.hit_windows(self.replay.mods)
        # Show ±good_ms as the full range, with some margin
        display_range = good_ms * 1.3
        if display_range < 1:
            return

        def off_to_x(offset_ms):
            frac = offset_ms / display_range
            return gx + int((frac + 1.0) * 0.5 * gw)

        # Background
        pygame.draw.rect(surf, (18, 20, 32), (gx, gy, gw, gh), border_radius=self.vs(4))

        # Window zones (filled rectangles)
        good_x0 = off_to_x(-good_ms);  good_x1 = off_to_x(good_ms)
        grt_x0  = off_to_x(-great_ms); grt_x1  = off_to_x(great_ms)
        bar_y   = gy + gh // 4;        bar_h   = gh // 2

        pygame.draw.rect(surf, (40, 65, 40),
                         (good_x0, bar_y, good_x1 - good_x0, bar_h))
        pygame.draw.rect(surf, (20, 55, 20),
                         (grt_x0,  bar_y, grt_x1 - grt_x0,  bar_h))

        # Center line
        cx = off_to_x(0)
        pygame.draw.line(surf, (120, 130, 170), (cx, gy + 4), (cx, gy + gh - 4), 1)

        # Window boundary markers
        for bx, col in [(good_x0, (100, 140, 80)), (good_x1, (100, 140, 80)),
                        (grt_x0,  ( 80, 190, 90)), (grt_x1,  ( 80, 190, 90))]:
            pygame.draw.line(surf, col, (bx, bar_y), (bx, bar_y + bar_h), 1)

        # Hit ticks
        for r in self.analysis.note_results:
            if r.is_miss:
                mx = off_to_x(0) + (self.vs(4) if r.offset != r.offset else 0)  # cluster near 0
                pygame.gfxdraw.filled_circle(surf, off_to_x(0), gy + self.vs(8),
                                             self.vs(3), (*MISS_COL, 200))
            elif not math.isnan(r.offset):
                tx = off_to_x(r.offset)
                if gx <= tx <= gx + gw:
                    col  = (*H300_COL, 160) if r.is_great else (*H100_COL, 220)
                    tick_h = self.vs(14) if r.is_great else self.vs(20)
                    ty   = mid - tick_h // 2
                    pygame.gfxdraw.box(surf, pygame.Rect(tx, ty, max(1, self.vs(1)), tick_h), col)

        # Average offset marker
        if not math.isnan(self.analysis.mean_offset):
            ax = off_to_x(self.analysis.mean_offset)
            if gx <= ax <= gx + gw:
                pygame.draw.line(surf, GOLD, (ax, gy + 2), (ax, gy + gh - 2), 2)

        # Border
        pygame.draw.rect(surf, LINE_COL, (gx, gy, gw, gh), 1, border_radius=self.vs(4))

        # Labels
        self._txt(surf, "hit error", gx + self.vs(6), gy + self.vs(4), self.f_sm, DIM_TEXT)
        self._txt(surf, f"±{great_ms:.0f}ms", grt_x0 - self.vs(2),
                  gy + self.vs(4), self.f_sm, (80, 190, 90), anchor="topright")
        self._txt(surf, f"−{good_ms:.0f}ms", good_x0 + self.vs(2),
                  gy + gh - self.vs(18), self.f_sm, DIM_TEXT)
        self._txt(surf, f"+{good_ms:.0f}ms", good_x1 - self.vs(2),
                  gy + gh - self.vs(18), self.f_sm, DIM_TEXT, anchor="topright")
        self._txt(surf, f"avg {self.analysis.mean_offset:+.1f}ms",
                  gx + gw - self.vs(6), gy + self.vs(4),
                  self.f_sm, GOLD, anchor="topright")

    # ------------------------------------------------------------------
    # Rolling UR graph — timing consistency over the map
    # ------------------------------------------------------------------

    def _draw_local_ur_graph(self, surf):
        gx = self.vx(TIMELINE_X)
        gy = self.vy(LOCAL_UR_Y)
        gw = self.vx(TIMELINE_W)
        gh = self.vs(LOCAL_UR_H)

        pygame.draw.rect(surf, (18, 20, 32), (gx, gy, gw, gh), border_radius=self.vs(4))
        pygame.draw.rect(surf, LINE_COL, (gx, gy, gw, gh), 1, border_radius=self.vs(4))

        local_ur = self.analysis.local_ur
        if len(local_ur) < 2:
            self._txt(surf, "rolling UR  (not enough data)", gx + self.vs(6), gy + self.vs(4),
                      self.f_sm, DIM_TEXT)
            return

        global_ur = self.analysis.ur
        max_ur    = max(u for _, u in local_ur) * 1.1 or 1.0
        n_notes   = len(self.analysis.note_results)

        # Overall UR reference line
        ref_y = gy + gh - int(global_ur / max_ur * (gh - self.vs(10))) - self.vs(5)
        ref_y = max(gy + 2, min(gy + gh - 2, ref_y))
        pygame.draw.line(surf, (160, 130, 40),
                         (gx + self.vs(1), ref_y), (gx + gw - self.vs(1), ref_y), 1)

        # UR line
        pts = []
        for ni, ur_val in local_ur:
            px = gx + int(ni / max(1, n_notes - 1) * (gw - 2)) + 1
            py = gy + gh - int(ur_val / max_ur * (gh - self.vs(10))) - self.vs(5)
            py = max(gy + 2, min(gy + gh - 2, py))
            pts.append((px, py))

        if len(pts) >= 2:
            # Color segments green→red based on UR
            for i in range(len(pts) - 1):
                _, ur_val = local_ur[i]
                ratio = min(1.0, ur_val / max(1.0, global_ur * 2))
                r_c = int(80  + 175 * ratio)
                g_c = int(200 - 140 * ratio)
                pygame.draw.line(surf, (r_c, g_c, 60), pts[i], pts[i + 1], max(1, self.vs(1)))

        self._txt(surf, "rolling UR", gx + self.vs(6), gy + self.vs(4), self.f_sm, DIM_TEXT)
        self._txt(surf, f"overall: {global_ur:.1f}  est: {self.analysis.ur_corrected:.1f}",
                  gx + gw - self.vs(6), gy + self.vs(4), self.f_sm, GOLD, anchor="topright")

    # ------------------------------------------------------------------
    # Combo graph — combo progression over time
    # ------------------------------------------------------------------

    def _draw_combo_graph(self, surf):
        gx = self.vx(TIMELINE_X)
        gy = self.vy(COMBO_G_Y)
        gw = self.vx(TIMELINE_W)
        gh = self.vs(COMBO_G_H)

        pygame.draw.rect(surf, (18, 20, 32), (gx, gy, gw, gh), border_radius=self.vs(4))
        pygame.draw.rect(surf, LINE_COL, (gx, gy, gw, gh), 1, border_radius=self.vs(4))

        data = self._combo_data
        if not data:
            return

        max_combo = max(c for _, c in data) or 1
        t_min, t_max = data[0][0], data[-1][0]
        t_span = max(1, t_max - t_min)

        # Max combo reference line (dim gold)
        ref_y = gy + self.vs(4)
        pygame.draw.line(surf, (80, 65, 20),
                         (gx + 1, ref_y), (gx + gw - 1, ref_y), 1)

        # Filled area under combo curve
        pts = [(gx, gy + gh)]
        for t, c in data:
            px = gx + int((t - t_min) / t_span * (gw - 2)) + 1
            py = gy + gh - int(c / max_combo * (gh - self.vs(8))) - self.vs(4)
            py = max(gy + 2, min(gy + gh - 2, py))
            pts.append((px, py))
        pts.append((gx + gw, gy + gh))

        if len(pts) >= 3:
            fill = pygame.Surface((gw, gh), pygame.SRCALPHA)
            adj = [(x - gx, y - gy) for x, y in pts]
            pygame.draw.polygon(fill, (80, 160, 255, 35), adj)
            surf.blit(fill, (gx, gy))
            line_pts = pts[1:-1]
            if len(line_pts) >= 2:
                pygame.draw.lines(surf, (80, 160, 255), False, line_pts,
                                  max(1, self.vs(1)))

        # Miss markers (combo resets)
        for r in self.analysis.note_results:
            if r.is_miss and t_span > 0:
                mx = gx + int((r.note.time - t_min) / t_span * (gw - 2)) + 1
                pygame.draw.line(surf, MISS_COL,
                                 (mx, gy + self.vs(4)), (mx, gy + gh - self.vs(4)), 1)

        self._txt(surf, "combo", gx + self.vs(6), gy + self.vs(4), self.f_sm, DIM_TEXT)
        self._txt(surf, f"max {self.replay.max_combo}×",
                  gx + gw - self.vs(6), gy + self.vs(4), self.f_sm, GOLD, anchor="topright")

    # ------------------------------------------------------------------
    # Pattern Analysis tab
    # ------------------------------------------------------------------

    def _draw_pattern_tab(self, surf):
        gx  = self.vx(TIMELINE_X)
        col_w = self.vx(COL_SPLIT)
        avail = self.vx(TIMELINE_W)
        y   = self.vy(TAB_CONTENT) + self.vs(12)

        patterns = self._pattern_data
        overall  = self.replay.accuracy

        # ── Header summary ──────────────────────────────────────────
        n_critical = sum(1 for p in patterns if p['delta'] < -5 and p['count'] >= 3)
        n_flag     = sum(1 for p in patterns if p['delta'] < -2 and p['count'] >= 2)
        if n_critical:
            hdr = f"{n_critical} pattern{'s' if n_critical != 1 else ''} significantly below average"
            hdr_col = MISS_COL
        elif n_flag:
            hdr = f"{n_flag} pattern{'s' if n_flag != 1 else ''} below average"
            hdr_col = H100_COL
        else:
            hdr = "No problematic patterns detected"
            hdr_col = H300_COL
        self._txt(surf, hdr, gx, y, self.f_md, hdr_col)
        y += self.vs(26)

        # ── Struggling pattern cards ─────────────────────────────────
        # Show up to 15 worst patterns, min 2 occurrences
        candidates = [p for p in patterns if p['count'] >= 2][:15]

        if candidates:
            self._txt(surf, "Worst patterns  (4-note window · worst accuracy first)",
                      gx, y, self.f_sm, DIM_TEXT)
            y += self.vs(18)

            cards_per_row = 5
            card_gap  = self.vs(4)
            card_w    = (avail - card_gap * (cards_per_row - 1)) // cards_per_row
            card_h    = self.vs(108)
            note_r    = self.vs(10)
            # layout zones within card
            _ni_top   = self.vs(10)   # note circles top
            _div_top  = self.vs(34)   # divisor / BPM label
            _sep_y    = self.vs(44)   # separator line
            # bottom half: horizontal — acc% left, delta+count right
            _stat_cy  = self.vs(76)   # vertical centre of bottom half
            _acc_cx   = 2             # acc% centred at card_w * 2/9 from left
            _right_x  = 5             # right column starts at card_w * 5/9

            for idx, pat in enumerate(candidates):
                col_i = idx % cards_per_row
                row_i = idx // cards_per_row
                cx = gx + col_i * (card_w + card_gap)
                cy = y  + row_i * (card_h + self.vs(6))

                # Strict colour coding: any miss → red; any significant 100-rate → yellow
                miss_rate = pat['nmiss'] / max(1, pat['count'])
                acc_col = (MISS_COL if miss_rate > 0 or pat['acc'] < overall - 5
                           else H100_COL if pat['acc'] < overall - 1
                           else H300_COL)
                bg = pygame.Rect(cx, cy, card_w, card_h)
                pygame.draw.rect(surf, (22, 24, 38), bg, border_radius=self.vs(5))
                pygame.draw.rect(surf, acc_col,  bg, 1, border_radius=self.vs(5))

                # ── TOP HALF: note circles ──
                ni_total_w = len(pat['pattern']) * (note_r * 2 + self.vs(3)) - self.vs(3)
                ni_x  = cx + (card_w - ni_total_w) // 2
                ni_cy = cy + _ni_top + note_r
                for sym in pat['pattern']:
                    is_big = isinstance(sym, str) and sym.endswith('b')
                    kind   = sym[0] if isinstance(sym, str) else sym
                    nc = DON_COL if kind == 'D' else KAT_COL
                    self._aa_circle(surf, ni_x + note_r, ni_cy, note_r, nc)
                    if is_big:
                        pygame.gfxdraw.aacircle(surf, ni_x + note_r, ni_cy,
                                                note_r + self.vs(2), (*nc, 140))
                    ni_x += note_r * 2 + self.vs(3)

                # Timing: divisor + BPM (effective)
                timing_str = f"{pat['div_label']}  ·  {pat['avg_bpm']:.0f} BPM"
                self._txt(surf, timing_str,
                          cx + card_w // 2, cy + _div_top,
                          self.f_xs, DIM_TEXT, anchor="center")

                # Separator between halves
                pygame.draw.line(surf, (40, 44, 65),
                                 (cx + self.vs(8), cy + _sep_y),
                                 (cx + card_w - self.vs(8), cy + _sep_y), 1)

                # ── BOTTOM HALF: horizontal layout ──────────────────
                # Left: large acc%
                acc_cx = cx + card_w * _acc_cx // 9
                self._txt(surf, f"{pat['acc']:.0f}%",
                          acc_cx, cy + _stat_cy,
                          self.f_md, acc_col, anchor="center")

                # Vertical divider
                div_x = cx + card_w * _right_x // 9
                pygame.draw.line(surf, (40, 44, 65),
                                 (div_x - self.vs(4), cy + _sep_y + self.vs(6)),
                                 (div_x - self.vs(4), cy + self.vs(104)), 1)

                # Right: delta above, count below
                delta_col = (H300_COL if pat['delta'] >= 0
                             else H100_COL if pat['delta'] > -3
                             else MISS_COL)
                self._txt(surf, f"{pat['delta']:+.0f}% vs avg",
                          div_x, cy + _stat_cy - self.vs(10),
                          self.f_xs, delta_col)

                meta = f"×{pat['count']}"
                if pat['nmiss']:
                    meta += f"  {pat['nmiss']}✕"
                self._txt(surf, meta,
                          div_x, cy + _stat_cy + self.vs(4),
                          self.f_xs, DIM_TEXT)

            rows_used = math.ceil(len(candidates) / cards_per_row)
            y += rows_used * (card_h + self.vs(6)) + self.vs(10)

        # ── Leading-context table for top 3 bad patterns ────────────
        bad_pats = [p for p in candidates if p['worst_lead'] is not None
                    and p['delta'] < -1][:3]
        if bad_pats:
            self._txt(surf, "Worst lead-in patterns", gx, y, self.f_sm, DIM_TEXT)
            y += self.vs(18)
            note_r2 = self.vs(7)
            row_h   = self.vs(22)
            for pat in bad_pats:
                # Pattern label (4 small circles inline)
                lx = gx
                for sym in pat['pattern']:
                    is_big = sym.endswith('b')
                    nc = DON_COL if sym[0] == 'D' else KAT_COL
                    self._aa_circle(surf, lx + note_r2, y + row_h // 2, note_r2, nc)
                    lx += note_r2 * 2 + self.vs(2)
                lx += self.vs(4)
                self._txt(surf, "preceded by", lx, y, self.f_sm, DIM_TEXT)
                lx += self.f_sm.size("preceded by")[0] + self.vs(6)
                # Leading 2-note context (circles)
                for sym in pat['worst_lead']:
                    nc = DON_COL if sym == 'D' else KAT_COL
                    self._aa_circle(surf, lx + note_r2, y + row_h // 2, note_r2, nc)
                    lx += note_r2 * 2 + self.vs(2)
                lx += self.vs(6)
                lead_acc_str = f"→ {pat['worst_lead_acc']:.0f}% acc"
                self._txt(surf, lead_acc_str, lx, y, self.f_sm, MISS_COL)
                y += row_h
            y += self.vs(8)

        # ── Stream / run analysis ────────────────────────────────────
        self._draw_stream_analysis(surf, gx, y, avail)

    def _draw_stream_analysis(self, surf, gx, y, avail):
        """Detect and show accuracy on same-type runs (streams)."""
        from osu_parser import NOTE_DON, NOTE_KAT
        notes = [r for r in self.analysis.note_results
                 if r.note.kind in (NOTE_DON, NOTE_KAT)]
        if len(notes) < 8:
            return

        # Find runs of same note type (length ≥ 4)
        run_stats: dict = {}  # length → {n300, n100, nmiss, count}
        i = 0
        while i < len(notes):
            kind = notes[i].note.kind
            j = i + 1
            while j < len(notes) and notes[j].note.kind == kind:
                j += 1
            run_len = j - i
            if run_len >= 4:
                bucket = min(run_len, 12)  # cap display at 12+
                if bucket not in run_stats:
                    run_stats[bucket] = {'n300': 0, 'n100': 0, 'nmiss': 0, 'count': 0}
                s = run_stats[bucket]
                s['count'] += 1
                for r in notes[i:j]:
                    if r.is_great:   s['n300'] += 1
                    elif r.is_good:  s['n100'] += 1
                    else:            s['nmiss'] += 1
            i = j

        if not run_stats:
            return

        self._txt(surf, "Same-type run accuracy (DD… or KK…)", gx, y, self.f_sm, DIM_TEXT)
        y += self.vs(18)

        row_h  = self.vs(22)
        lw     = self.vs(70)
        bar_max = avail - lw - self.vs(80)

        for length in sorted(run_stats):
            s     = run_stats[length]
            total = s['n300'] + s['n100'] + s['nmiss']
            if total == 0:
                continue
            acc = (s['n300'] + 0.5 * s['n100']) / total * 100
            label = f"{length}+" if length == 12 else str(length)
            self._txt(surf, f"{label}-note", gx, y, self.f_sm, DIM_TEXT)

            bw   = int(bar_max * acc / 100)
            bcol = (H300_COL if acc >= 95 else H100_COL if acc >= 85 else MISS_COL)
            bar_rect = pygame.Rect(gx + lw, y + self.vs(3),
                                   bar_max, row_h - self.vs(6))
            pygame.draw.rect(surf, (28, 30, 46), bar_rect, border_radius=self.vs(3))
            if bw > 0:
                pygame.draw.rect(surf,
                                 tuple(max(0, c - 60) for c in bcol),
                                 pygame.Rect(gx + lw, y + self.vs(3), bw, row_h - self.vs(6)),
                                 border_radius=self.vs(3))
            pygame.draw.rect(surf, LINE_COL, bar_rect, 1, border_radius=self.vs(3))

            self._txt(surf, f"{acc:.0f}%  ×{s['count']}",
                      gx + lw + bar_max + self.vs(6), y, self.f_sm, bcol)
            y += row_h

    # ------------------------------------------------------------------
    # BPM & Stamina tab
    # ------------------------------------------------------------------

    def _draw_bpm_stamina_tab(self, surf):
        gx   = self.vx(TIMELINE_X)
        avail = self.vx(TIMELINE_W)
        y    = self.vy(TAB_CONTENT) + self.vs(10)

        bpm_data, n_reps = self._bpm_acc_data
        # ── BPM Comfortability chart ────────────────────────────────
        # Header row — BPM in data is already effective (DT applied per-replay)
        src_label = (f"aggregated · {n_reps} replays"
                     if n_reps >= 2 else "this replay only")
        self._txt(surf, "BPM Comfortability", gx, y, self.f_md, (200, 205, 230))
        self._txt(surf, src_label,
                  gx + avail, y + self.vs(3),
                  self.f_sm, DIM_TEXT, anchor="topright")
        y += self.vs(22)

        chart_h = self.vs(130)
        chart_w = avail

        if not bpm_data:
            self._txt(surf, "Not enough data", gx + avail // 2, y + chart_h // 2,
                      self.f_md, DIM_TEXT, anchor="center")
        else:
            # Axes background
            pygame.draw.rect(surf, (14, 16, 26),
                             (gx, y, chart_w, chart_h), border_radius=self.vs(4))
            pygame.draw.rect(surf, LINE_COL,
                             (gx, y, chart_w, chart_h), 1, border_radius=self.vs(4))

            # Horizontal guide lines at 90 / 95 / 100%
            for pct in (90, 95, 100):
                gy_line = y + chart_h - int(chart_h * pct / 100)
                pygame.draw.line(surf, (35, 40, 60),
                                 (gx, gy_line), (gx + chart_w, gy_line), 1)
                self._txt(surf, f"{pct}%", gx - self.vs(4), gy_line,
                          self.f_sm, (80, 85, 110), anchor="topright")

            # BPM bars
            bpms     = [d["bpm"] for d in bpm_data]
            accs     = [d["acc"] for d in bpm_data]
            totals   = [d["total"] for d in bpm_data]
            min_bpm  = min(bpms)
            max_bpm  = max(bpms)
            bpm_span = max(max_bpm - min_bpm, 1)

            # bar width: distribute evenly, capped
            n_bars   = len(bpm_data)
            bar_w    = max(4, min(self.vs(18), chart_w // max(1, n_bars) - 2))

            for i, (bpm_eff, acc, total) in enumerate(zip(bpms, accs, totals)):
                bx = gx + int((bpm_eff - min_bpm) / bpm_span * (chart_w - bar_w))
                bh = max(2, int(chart_h * acc / 100))
                bcol = (H300_COL if acc >= 95 else H100_COL if acc >= 85 else MISS_COL)
                # bar
                pygame.draw.rect(surf, tuple(max(0, c - 50) for c in bcol),
                                 (bx, y + chart_h - bh, bar_w, bh),
                                 border_radius=self.vs(2))
                # dot at top
                self._aa_circle(surf, bx + bar_w // 2, y + chart_h - bh,
                                 self.vs(3), bcol)
                # BPM label below chart
                lbl = f"{int(bpm_eff)}"
                self._txt(surf, lbl,
                          bx + bar_w // 2, y + chart_h + self.vs(3),
                          self.f_sm, DIM_TEXT, anchor="center")

        y += chart_h + self.vs(28)

        # ── Stamina: accuracy + UR over song sections ───────────────
        self._txt(surf, "Stamina (accuracy & UR across map)",
                  gx, y, self.f_md, (200, 205, 230))
        y += self.vs(22)

        stats = self.analysis.section_stats
        stam_h = self.vs(100)

        if not stats or len(stats) < 2:
            self._txt(surf, "Not enough sections to display stamina.",
                      gx, y, self.f_sm, DIM_TEXT)
            return

        pygame.draw.rect(surf, (14, 16, 26),
                         (gx, y, avail, stam_h), border_radius=self.vs(4))
        pygame.draw.rect(surf, LINE_COL,
                         (gx, y, avail, stam_h), 1, border_radius=self.vs(4))

        # Guide lines at 90 / 95 / 100% accuracy
        for pct in (90, 95, 100):
            gy_line = y + stam_h - int(stam_h * pct / 100)
            pygame.draw.line(surf, (35, 40, 60),
                             (gx, gy_line), (gx + avail, gy_line), 1)
            self._txt(surf, f"{pct}%", gx - self.vs(4), gy_line,
                      self.f_sm, (80, 85, 110), anchor="topright")

        n      = len(stats)
        seg_w  = avail / n
        max_ur = max((s["ur"] for s in stats), default=1) or 1

        # Accuracy filled polygon
        acc_pts = []
        for i, s in enumerate(stats):
            px = int(gx + (i + 0.5) * seg_w)
            py_pt = y + stam_h - int(stam_h * s["acc"] / 100)
            acc_pts.append((px, py_pt))

        if len(acc_pts) >= 2:
            # filled area under curve
            poly = [(gx, y + stam_h)] + acc_pts + [(gx + avail, y + stam_h)]
            fill_surf = pygame.Surface((avail, stam_h), pygame.SRCALPHA)
            adj_poly  = [(px - gx, py_pt - y) for px, py_pt in poly]
            pygame.draw.polygon(fill_surf, (80, 200, 100, 40), adj_poly)
            surf.blit(fill_surf, (gx, y))
            pygame.draw.lines(surf, (80, 200, 100), False, acc_pts, 2)

        # UR dots
        for i, s in enumerate(stats):
            px = int(gx + (i + 0.5) * seg_w)
            ur_frac = min(1.0, s["ur"] / (max_ur * 1.1))
            py_pt  = y + int(stam_h * ur_frac)
            self._aa_circle(surf, px, py_pt, self.vs(3), GOLD)

        # Legend
        leg_y = y + stam_h + self.vs(4)
        self._txt(surf, "acc ▬", gx, leg_y, self.f_sm, (80, 200, 100))
        self._txt(surf, "UR ●",  gx + self.vs(50), leg_y, self.f_sm, GOLD)

        # Section count label (time extent)
        total_ms  = self.beatmap.total_length_ms if hasattr(self.beatmap, "total_length_ms") \
                    else (stats[-1]["end"] if stats else 0)
        self._txt(surf, f"{n} sections · {total_ms/1000:.0f}s",
                  gx + avail, leg_y, self.f_sm, DIM_TEXT, anchor="topright")

    # ------------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------------

    def _draw_timeline(self, surf):
        tx = self.vx(TIMELINE_X)
        ty = self.vy(TIMELINE_Y)
        tw = self.vx(TIMELINE_W)
        th = self.vs(TIMELINE_H)

        pygame.draw.rect(surf, TIMELINE_BG,
                         (tx - self.vs(4), ty - self.vs(5),
                          tw + self.vs(8), th + self.vs(10)))
        pygame.draw.rect(surf, LINE_COL, (tx, ty, tw, th), 1,
                         border_radius=self.vs(4))

        # Hit result dots
        for r in self.analysis.note_results:
            if r.note.kind not in (NOTE_DON, NOTE_KAT):
                continue
            rx = self._timeline_x(r.note.time)
            if r.is_miss:
                col, h = (*MISS_COL, 255), th
            elif r.is_good:
                col, h = (*H100_COL, 200), th // 2
            else:
                col, h = (*H300_COL, 120), th // 4
            s = pygame.Surface((max(1, self.vs(2)), h), pygame.SRCALPHA)
            s.fill(col)
            surf.blit(s, (rx, ty + (th - h) // 2))

        # Problem markers
        for p in self.analysis.problems:
            px  = self._timeline_x(p.start_ms)
            col = {"miss_cluster": MISS_COL, "high_ur": H100_COL,
                   "timing_drift": (180, 160, 255)}.get(p.kind, GOLD)
            pygame.draw.line(surf, col,
                             (px, ty - self.vs(6)), (px, ty + th + self.vs(6)), 2)

        # Current-position cursor
        cx = max(tx, min(tx + tw, self._timeline_x(self.game_time)))
        pygame.draw.line(surf, TEXT_COL, (cx, ty - self.vs(8)), (cx, ty + th + self.vs(8)), 2)
        pygame.draw.polygon(surf, TEXT_COL, [
            (cx - self.vs(6), ty - self.vs(14)),
            (cx + self.vs(6), ty - self.vs(14)),
            (cx, ty - self.vs(6))])

        # Time labels
        for frac in [0, 0.25, 0.5, 0.75, 1.0]:
            t  = frac * self.song_end_ms
            lx = tx + int(frac * tw)
            self._txt(surf, f"{t/1000:.0f}s", lx, ty + th + self.vs(5),
                      self.f_sm, DIM_TEXT, anchor="center")

    # ------------------------------------------------------------------
    # Controls (below timeline in data area)
    # ------------------------------------------------------------------

    def _draw_controls(self, surf):
        y0   = self.vy(TIMELINE_Y + TIMELINE_H) + self.vs(18)
        row1 = y0
        row2 = row1 + self.vs(32)
        btn_h = self.vs(24)
        pad   = self.vs(8)

        # --- Play / Pause button ---
        lbl_pp  = "▶ Play" if not self.playing else "⏸ Pause"
        pp_s    = self.f_md.render(lbl_pp, True, TEXT_COL)
        pp_w    = pp_s.get_width() + pad * 2
        pp_rect = pygame.Rect(self.vx(TIMELINE_X), row1, pp_w, btn_h)
        pygame.draw.rect(surf, (50, 110, 70) if self.playing else (55, 55, 80),
                         pp_rect, border_radius=self.vs(5))
        pygame.draw.rect(surf, LINE_COL, pp_rect, 1, border_radius=self.vs(5))
        surf.blit(pp_s, (pp_rect.x + pad, pp_rect.y + (btn_h - pp_s.get_height()) // 2))
        self._btn_play = pp_rect

        def _spd_group(x, label, value, fmt, color, dn_key, up_key):
            """Draw a labeled − value + button group; returns (dn_rect, up_rect, right_x)."""
            lbl_s = self.f_sm.render(label, True, DIM_TEXT)
            surf.blit(lbl_s, (x, row1 + (btn_h - lbl_s.get_height()) // 2))
            x += lbl_s.get_width() + self.vs(6)

            dn_s    = self.f_md.render("−", True, TEXT_COL)
            dn_rect = pygame.Rect(x, row1, self.vs(26), btn_h)
            pygame.draw.rect(surf, (50, 50, 75), dn_rect, border_radius=self.vs(4))
            pygame.draw.rect(surf, LINE_COL, dn_rect, 1, border_radius=self.vs(4))
            surf.blit(dn_s, (dn_rect.x + (dn_rect.w - dn_s.get_width()) // 2,
                             dn_rect.y + (btn_h - dn_s.get_height()) // 2))

            val_s = self.f_md.render(fmt.format(value), True, color)
            vx    = dn_rect.right + self.vs(6)
            surf.blit(val_s, (vx, row1 + (btn_h - val_s.get_height()) // 2))

            up_s    = self.f_md.render("+", True, TEXT_COL)
            up_rect = pygame.Rect(vx + val_s.get_width() + self.vs(6), row1,
                                  self.vs(26), btn_h)
            pygame.draw.rect(surf, (50, 50, 75), up_rect, border_radius=self.vs(4))
            pygame.draw.rect(surf, LINE_COL, up_rect, 1, border_radius=self.vs(4))
            surf.blit(up_s, (up_rect.x + (up_rect.w - up_s.get_width()) // 2,
                             up_rect.y + (btn_h - up_s.get_height()) // 2))
            return dn_rect, up_rect, up_rect.right

        # Playback speed
        spd = self._speed()
        spd_col = GOLD if spd != 1.0 else TEXT_COL
        sx = pp_rect.right + self.vs(18)
        self._btn_spd_dn, self._btn_spd_up, sx = _spd_group(
            sx, "Speed", spd, "{:.2f}×", spd_col, None, None)

        if spd != 1.0:
            m_s = self.f_sm.render("(audio muted)", True, MISS_COL)
            surf.blit(m_s, (sx + self.vs(8),
                            row1 + (btn_h - m_s.get_height()) // 2))
            sx += m_s.get_width() + self.vs(18)
        else:
            sx += self.vs(22)

        # Scroll speed
        scr = self._scroll_speed
        scr_col = ACCENT if scr != 1.5 else DIM_TEXT
        self._btn_scr_dn, self._btn_scr_up, sx = _spd_group(
            sx, "Scroll", scr, "{:.1f}×", scr_col, None, None)

        # Keyboard hint (right-aligned within the left column)
        p_hint = "   [P] Profile" if self.profile else ""
        hints = f"[←/→] ±5s   [Shift] ±30s   [ / ] scroll   [Home/End]   [↑/↓] Issues   [Q] Quit{p_hint}"
        self._txt(surf, hints,
                  self.vx(COL_SPLIT) - self.vs(14), row1,
                  self.f_sm, DIM_TEXT, anchor="topright")

        # --- Volume slider (row 2) ---
        vol_lbl = self.f_sm.render("Vol", True, DIM_TEXT)
        vl_x    = self.vx(TIMELINE_X)
        surf.blit(vol_lbl, (vl_x, row2 + (self.vs(10) - vol_lbl.get_height()) // 2))

        sl_x  = vl_x + vol_lbl.get_width() + self.vs(10)
        sl_w  = self.vx(240)
        sl_h  = self.vs(10)
        sl_y  = row2 + self.vs(4)
        pygame.draw.rect(surf, (50, 55, 80),
                         pygame.Rect(sl_x, sl_y, sl_w, sl_h), border_radius=self.vs(5))
        fw = int(sl_w * self._volume)
        if fw > 0:
            pygame.draw.rect(surf, ACCENT,
                             pygame.Rect(sl_x, sl_y, fw, sl_h), border_radius=self.vs(5))
        self._aa_circle(surf, sl_x + fw, sl_y + sl_h // 2, self.vs(9), TEXT_COL)
        pct_s = self.f_sm.render(f"{int(self._volume * 100)}%", True, DIM_TEXT)
        surf.blit(pct_s, (sl_x + sl_w + self.vs(8),
                          row2 + (self.vs(10) - pct_s.get_height()) // 2))

        self._vol_rect = pygame.Rect(sl_x - self.vs(9), sl_y - self.vs(7),
                                     sl_w + self.vs(18), sl_h + self.vs(14))

    def _set_volume_from_x(self, mx: int):
        if self._vol_rect is None:
            return
        sl_x = self._vol_rect.x + self.vs(9)
        sl_w = self.vx(240)
        self._volume = max(0.0, min(1.0, (mx - sl_x) / max(1, sl_w)))
        pygame.mixer.music.set_volume(self._volume)

    # ------------------------------------------------------------------
    # Right column: statistics panel
    # ------------------------------------------------------------------

    def _draw_stats_panel(self, surf):
        px = self.vx(PANEL_X)
        py = self.vy(PANEL_Y)
        pw = self.vx(PANEL_W)
        ph = self.vy(PANEL_H)

        # Vertical divider
        pygame.draw.line(surf, LINE_COL, (self.vx(COL_SPLIT), 0),
                         (self.vx(COL_SPLIT), self.H), 1)

        # Panel background
        r = pygame.Rect(px, py, pw, ph)
        pygame.draw.rect(surf, PANEL_BG, r, border_radius=self.vs(6))
        pygame.draw.rect(surf, LINE_COL, r, 1, border_radius=self.vs(6))

        an, rep, bm = self.analysis, self.replay, self.beatmap
        x0    = px + self.vs(18)
        right = px + pw - self.vs(18)
        y     = py + self.vs(16)
        row_h = self.vs(22)

        def row(label, val, vc=TEXT_COL):
            nonlocal y
            self._txt(surf, label, x0, y, self.f_sm, DIM_TEXT)
            self._txt(surf, val, right, y, self.f_sm, vc, anchor="topright")
            y += row_h

        def sep():
            nonlocal y
            y += self.vs(5)
            pygame.draw.line(surf, SEP_COL, (x0, y), (right, y), 1)
            y += self.vs(8)

        def section(title):
            nonlocal y
            self._txt(surf, title.upper(), x0, y, self.f_sm, (90, 100, 145))
            y += self.vs(20)

        # ── Hero: score + grade ──────────────────────────────────────
        grade, g_col = self._grade()
        score_str = f"{rep.score:,}"
        self._txt(surf, score_str, x0, y, self.f_xl, TEXT_COL)
        self._txt(surf, grade, right, y, self.f_xl, g_col, anchor="topright")
        y += self.vs(38)

        # Acc / combo on one line
        acc_col = GOLD if rep.accuracy >= 99.5 else TEXT_COL
        self._txt(surf, f"{rep.accuracy:.2f}%", x0, y, self.f_lg, acc_col)
        self._txt(surf, f"{rep.max_combo}×", right, y, self.f_lg, DIM_TEXT, anchor="topright")
        y += self.vs(30)

        # 300 / 100 / miss chips
        chip_labels = [
            (str(rep.n300), H300_COL),
            (str(rep.n100), H100_COL),
            (str(rep.nmiss), MISS_COL if rep.nmiss else DIM_TEXT),
        ]
        cx = x0
        for chip_txt, chip_col in chip_labels:
            cs = self.f_md.render(chip_txt, True, chip_col)
            cw = cs.get_width() + self.vs(16)
            ch = cs.get_height() + self.vs(6)
            pygame.draw.rect(surf, (28, 30, 46),
                             pygame.Rect(cx, y, cw, ch), border_radius=self.vs(4))
            pygame.draw.rect(surf, chip_col,
                             pygame.Rect(cx, y, cw, ch), 1, border_radius=self.vs(4))
            surf.blit(cs, (cx + self.vs(8), y + self.vs(3)))
            cx += cw + self.vs(8)
        y += self.vs(28) + self.vs(6)

        # Player / date / profile badge
        self._txt(surf, rep.player_name, x0, y, self.f_md, TEXT_COL)
        self._txt(surf, self._replay_date(), right, y, self.f_sm, DIM_TEXT, anchor="topright")
        y += self.vs(22)

        if self.profile:
            n_reps = len(self.profile.get("replays", []))
            aliases = self.profile.get("aliases", [])
            badge_txt  = f"Profile  ·  {n_reps} replay{'s' if n_reps != 1 else ''}"
            badge_surf = self.f_sm.render(badge_txt, True, ACCENT)
            bw = badge_surf.get_width() + self.vs(14)
            bh = badge_surf.get_height() + self.vs(6)
            pygame.draw.rect(surf, (24, 36, 56),
                             pygame.Rect(x0, y, bw, bh), border_radius=self.vs(4))
            pygame.draw.rect(surf, ACCENT,
                             pygame.Rect(x0, y, bw, bh), 1, border_radius=self.vs(4))
            surf.blit(badge_surf, (x0 + self.vs(7), y + self.vs(3)))
            if len(aliases) > 1:
                alias_str = "aka " + ", ".join(a for a in aliases if a != self.profile["display_name"])
                self._txt(surf, alias_str, x0 + bw + self.vs(10), y + self.vs(3),
                          self.f_sm, DIM_TEXT)
            y += bh + self.vs(6)

        self._txt(surf, f"{bm.artist} – {bm.title}", x0, y, self.f_sm, DIM_TEXT)
        y += self.vs(18)
        self._txt(surf, f"[{bm.version}]  mapped by {bm.creator}", x0, y, self.f_sm, DIM_TEXT)
        y += self.vs(20)

        sep()

        # ── Timing ───────────────────────────────────────────────────
        if not self.portable:
          section("Timing")
          ur_col = (GOLD if an.ur_corrected < 8 else
                    H100_COL if an.ur_corrected < 14 else MISS_COL)
          row("UR (raw / est)", f"{an.ur:.1f}  /  {an.ur_corrected:.1f}", ur_col)
          row("Avg offset",     f"{an.mean_offset:+.1f} ms",
              H100_COL if abs(an.mean_offset) > 12 else TEXT_COL)
          row("Early rate",     f"{an.early_rate*100:.0f}%")
          great_ms, good_ms = bm.hit_windows(rep.mods)
          row("Hit windows",    f"±{great_ms:.0f} ms  /  ±{good_ms:.0f} ms")
          row("OD (effective)", f"{bm.adjusted_od(rep.mods):.1f}")

        # UR trend sparkline (profile only)
        if not self.portable and self.profile:
            from profile import ProfileManager
            trend = ProfileManager().ur_trend(self.profile)
            if len(trend) >= 3:
                y += self.vs(4)
                self._txt(surf, "UR trend", x0, y, self.f_sm, DIM_TEXT)
                sp_x = x0 + self.vs(90)
                sp_w = right - sp_x
                sp_h = self.vs(18)
                sp_y = y
                ur_vals = [t["ur_corrected"] for t in trend]
                max_ur  = max(ur_vals) or 1
                pygame.draw.rect(surf, (22, 24, 36),
                                 (sp_x, sp_y, sp_w, sp_h), border_radius=self.vs(2))
                pts = []
                for i, v in enumerate(ur_vals):
                    px = sp_x + int(i / (len(ur_vals) - 1) * (sp_w - 2)) + 1
                    py = sp_y + sp_h - int(v / max_ur * (sp_h - 2)) - 1
                    pts.append((px, py))
                if len(pts) >= 2:
                    pygame.draw.lines(surf, ACCENT, False, pts, max(1, self.vs(1)))
                # Highlight current replay
                self._aa_circle(surf, pts[-1][0], pts[-1][1], self.vs(3), GOLD)
                y += sp_h + self.vs(4)

        sep()

        # ── Key Usage ────────────────────────────────────────────────
        section("Key Usage")
        kc = self._key_counts
        # Order: M2 (left kat), M1 (left don), K1 (right don), K2 (right kat)
        keys_order = [
            (KEY_M2, "K", KAT_COL),
            (KEY_M1, "D", DON_COL),
            (KEY_K1, "D", DON_COL),
            (KEY_K2, "K", KAT_COL),
        ]
        max_k   = max(kc.values()) or 1
        bar_area_w = right - x0
        bar_w   = (bar_area_w - self.vs(3) * 3) // 4
        bar_max_h = self.vs(44)
        kx = x0
        for key_mask, key_label, key_col in keys_order:
            cnt  = kc.get(key_mask, 0)
            bh   = max(2, int(cnt / max_k * bar_max_h))
            by   = y + bar_max_h - bh
            bar_bg = pygame.Rect(kx, y, bar_w, bar_max_h)
            pygame.draw.rect(surf, (28, 30, 46), bar_bg, border_radius=self.vs(3))
            bar_fill = pygame.Rect(kx, by, bar_w, bh)
            c_dim = tuple(max(0, c - 80) for c in key_col)
            pygame.draw.rect(surf, c_dim, bar_fill, border_radius=self.vs(3))
            pygame.draw.rect(surf, key_col, bar_bg, 1, border_radius=self.vs(3))
            self._txt(surf, key_label, kx + bar_w // 2, y + bar_max_h + self.vs(3),
                      self.f_sm, key_col, anchor="center")
            self._txt(surf, str(cnt), kx + bar_w // 2, y + bar_max_h + self.vs(16),
                      self.f_sm, DIM_TEXT, anchor="center")
            kx += bar_w + self.vs(3)
        y += bar_max_h + self.vs(32)
        sep()

        # ── Playstyle ────────────────────────────────────────────────
        section("Playstyle")
        ps            = an.playstyle
        profile_layout = (self.profile.get("layout", "KDDK") if self.profile else "KDDK")
        is_grouped    = profile_layout in ("DDKK", "KKDD")

        NAME_COLS = {"Full-Alt": H300_COL, "Semi-Alt": H100_COL,
                     "Singletap": (200, 200, 100), "Roll": MISS_COL}
        if is_grouped:
            dfa    = ps.don_finger_alt
            kfa    = ps.kat_finger_alt
            avg_fa = (dfa + kfa) / 2
            if dfa >= 0.75 and kfa >= 0.75:   display_name = "Full-Alt"
            elif avg_fa >= 0.52:               display_name = "Semi-Alt"
            elif avg_fa >= 0.25:               display_name = "Singletap"
            else:                              display_name = "Roll"
        else:
            display_name = ps.name

        ps_col = NAME_COLS.get(display_name, TEXT_COL)
        self._txt(surf, display_name, x0, y, self.f_md, ps_col)
        self._txt(surf, profile_layout, right, y, self.f_md, DIM_TEXT, anchor="topright")
        y += self.vs(26)
        if is_grouped:
            row("Don-finger alt", f"{ps.don_finger_alt*100:.0f}%")
            row("Kat-finger alt", f"{ps.kat_finger_alt*100:.0f}%")
        else:
            row("Pattern Alt Rate",  f"{ps.alt_rate*100:.0f}%")
            row("Side balance", f"{ps.left_bias*100:.0f}% L  /  {(1-ps.left_bias)*100:.0f}% R")
            if ps.phrase_count >= 4:
                psa = ps.phrase_start_alt_rate
                psa_col = H300_COL if psa >= 0.65 else (H100_COL if psa >= 0.4 else MISS_COL)
                l_col   = H300_COL if ps.phrase_alt_L_rate >= 0.65 else (H100_COL if ps.phrase_alt_L_rate >= 0.4 else MISS_COL)
                r_col   = H300_COL if ps.phrase_alt_R_rate >= 0.65 else (H100_COL if ps.phrase_alt_R_rate >= 0.4 else MISS_COL)
                # Header row: label + combined %
                self._txt(surf, "Full-Alt %", x0, y, self.f_sm, DIM_TEXT)
                detail = (f"{psa*100:.0f}%"
                          f"   start-L {ps.phrase_alt_L_rate*100:.0f}%"
                          f"   start-R {ps.phrase_alt_R_rate*100:.0f}%")
                row("Full-Alt %", detail, psa_col)
                if len(ps.phrase_alt_sections) >= 2:
                    spark_h = self.vs(18)
                    self._draw_phrase_alt_sparkline(
                        surf, x0, y, right - x0, spark_h, ps.phrase_alt_sections)
                    y += spark_h + self.vs(4)
            if ps.bridge_pair_count >= 8:
                br_col = H300_COL if ps.bridge_alt_rate >= 0.65 else (
                    H100_COL if ps.bridge_alt_rate >= 0.4 else MISS_COL)
                row("Bridge alt", f"{ps.bridge_alt_rate*100:.0f}%", br_col)
        if ps.double_taps and display_name in ("Full-Alt", "Semi-Alt"):
            row("Alt breaks", str(len(ps.double_taps)), MISS_COL)
        sep()

        # ── Section Breakdown ────────────────────────────────────────
        if not self.portable:
            section("By Section")
            graph_h = self.vs(64)
            self._draw_section_graph(surf, x0, y, pw - self.vs(36), graph_h)
            y += graph_h + self.vs(20)
            sep()

        # ── Issues ───────────────────────────────────────────────────
        if not self.portable:
          section("Issues")
        remaining_h = py + ph - y - self.vs(12)
        max_items   = max(1, remaining_h // self.vs(34))
        self._draw_problems(surf, x0, y, right, max_items)

    def _draw_gauge(self, surf, x, y, w, h, value, lo=0.0, hi=1.0,
                    left_label="", right_label="", marker_col=None):
        """Horizontal filled gauge with a marker dot and optional end labels."""
        if marker_col is None:
            marker_col = (220, 180, 60)
        pygame.draw.rect(surf, (22, 24, 38), (x, y, w, h), border_radius=self.vs(3))
        pygame.draw.rect(surf, LINE_COL,     (x, y, w, h), 1, border_radius=self.vs(3))
        frac = max(0.0, min(1.0, (value - lo) / max(hi - lo, 1e-9)))
        fill_w = max(self.vs(2), int(w * frac))
        t   = frac
        col = (int(220 * (1 - t) + 80 * t), int(80 * (1 - t) + 220 * t), 60)
        pygame.draw.rect(surf, col, (x, y, fill_w, h), border_radius=self.vs(3))
        mx = x + int(w * frac)
        self._aa_circle(surf, mx, y + h // 2, self.vs(3), marker_col)
        if left_label:
            self._txt(surf, left_label,  x + self.vs(4), y + h // 2,
                      self.f_xs, (0, 0, 0), anchor="midleft")
        if right_label:
            self._txt(surf, right_label, x + w - self.vs(4), y + h // 2,
                      self.f_xs, (0, 0, 0), anchor="midright")

    def _draw_pattern_grid(self, surf, cx, cy, card_w, area_h, pattern, avg_gap_fracs):
        """
        Draw 4 note circles on a beat-grid timeline.
        Notes are placed proportionally by avg_gap_fracs (beat fractions).
        Tick marks at 1/4 beat positions show whether notes are on-grid.
        """
        pad       = self.vs(8)
        grid_x    = cx + pad
        grid_w    = card_w - pad * 2
        note_r    = self.vs(8)
        baseline_y = cy + area_h - self.vs(6)
        note_cy    = baseline_y - note_r - self.vs(3)

        # Compute note positions in beats from first note
        N = len(pattern)
        gap_fracs = avg_gap_fracs if avg_gap_fracs and len(avg_gap_fracs) == N - 1 \
                    else [0.25] * (N - 1)
        positions = [0.0]
        for f in gap_fracs:
            positions.append(positions[-1] + f)
        total_span = max(positions[-1], 0.01)

        # Scale: show the note span with a bit of right-padding
        display_span = total_span * 1.15
        scale = grid_w / display_span

        # Baseline
        pygame.draw.line(surf, (45, 50, 75),
                         (grid_x, baseline_y), (grid_x + grid_w, baseline_y), 1)

        # Beat tick marks at 1/4 intervals within display range
        tick_interval = 0.25
        t = 0.0
        while t <= display_span + 0.01:
            tx = int(grid_x + t * scale)
            is_beat = abs(t - round(t)) < 0.01
            tick_h  = self.vs(5) if is_beat else self.vs(3)
            col     = (70, 76, 110) if is_beat else (50, 55, 82)
            pygame.draw.line(surf, col,
                             (tx, baseline_y), (tx, baseline_y + tick_h), 1)
            t = round(t + tick_interval, 6)

        # Note circles at proportional positions
        for sym, pos in zip(pattern, positions):
            is_big = isinstance(sym, str) and sym.endswith('b')
            kind   = sym[0] if isinstance(sym, str) else sym
            nc     = DON_COL if kind == 'D' else KAT_COL
            nx     = int(grid_x + pos * scale)
            # Drop line from circle bottom to baseline
            pygame.draw.line(surf, (38, 42, 62),
                             (nx, note_cy + note_r), (nx, baseline_y), 1)
            self._aa_circle(surf, nx, note_cy, note_r, nc)
            if is_big:
                pygame.gfxdraw.aacircle(surf, nx, note_cy,
                                        note_r + self.vs(2), (*nc, 140))

    def _draw_phrase_alt_sparkline(self, surf, x, y, w, h, series):
        """Thin sparkline for Full-Alt % rolling series. 0–1 range, green=high red=low."""
        pygame.draw.rect(surf, (14, 16, 26), (x, y, w, h), border_radius=self.vs(2))
        pygame.draw.rect(surf, LINE_COL,     (x, y, w, h), 1, border_radius=self.vs(2))
        if len(series) < 2:
            return
        pad = self.vs(2)
        iw  = w - 2 * pad
        ih  = h - 2 * pad
        pts = []
        for i, v in enumerate(series):
            px = x + pad + int(i / (len(series) - 1) * iw)
            py = y + pad + int((1.0 - v) * ih)
            pts.append((px, py, v))
        for i in range(len(pts) - 1):
            v   = pts[i][2]
            r_c = int(60  + 195 * (1 - v))
            g_c = int(200 - 140 * (1 - v))
            pygame.draw.line(surf, (r_c, g_c, 60),
                             pts[i][:2], pts[i + 1][:2], max(1, self.vs(1)))

    def _draw_section_graph(self, surf, x, y, w, h):
        stats = self.analysis.section_stats
        if not stats:
            return
        n      = len(stats)
        bw     = max(2, w // n)
        max_ur = max((s["ur"] for s in stats), default=1) or 1
        for i, s in enumerate(stats):
            bxi = x + i * bw
            af  = s["acc"] / 100.0
            col = (int(220 * (1 - af)), int(220 * af), 50)
            bh  = max(1, int(h * 0.80 * af))
            pygame.draw.rect(surf, col,
                             (bxi + 1, y + h - bh, bw - 2, bh),
                             border_radius=self.vs(3))
            ur_y = y + int(h * 0.80 * (1 - min(1.0, s["ur"] / (max_ur + 1))))
            self._aa_circle(surf, bxi + bw // 2, ur_y, self.vs(4), GOLD)
        self._txt(surf, "acc ▬   UR ●",
                  x + w - self.vs(90), y + h + self.vs(4),
                  self.f_sm, DIM_TEXT)

    def _draw_problems(self, surf, x, y, right_edge, max_items=8):
        problems = self.analysis.problems
        if not problems:
            self._txt(surf, "No significant issues.", x, y, self.f_sm, H300_COL)
            return
        icons = {"miss_cluster": "●", "high_ur": "⚡",
                 "timing_drift": "→", "double_tap": "×", "alt_break": "×"}
        cols  = {"miss_cluster": MISS_COL, "high_ur": H100_COL,
                 "timing_drift": (180, 160, 255), "double_tap": DON_COL}
        item_h = self.vs(34)
        for i, p in enumerate(problems[self.problem_scroll:
                                       self.problem_scroll + max_items]):
            iy  = y + i * item_h
            ic  = icons.get(p.kind, "!")
            col = cols.get(p.kind, TEXT_COL)
            self._txt(surf, ic, x, iy, self.f_sm, col)
            self._txt(surf, f"{p.start_ms/1000:.1f}s",
                      x + self.vs(20), iy, self.f_sm, DIM_TEXT)
            # Clip description to available width
            desc   = p.description
            desc_x = x + self.vs(70)
            max_w  = right_edge - desc_x - self.vs(6)
            ds     = self.f_sm.render(desc, True, col)
            while ds.get_width() > max_w and len(desc) > 6:
                desc = desc[:-4] + "…"
                ds   = self.f_sm.render(desc, True, col)
            surf.blit(ds, (desc_x, iy))
            bw = int(self.vs(70) * p.severity)
            pygame.draw.rect(surf, col,
                             (x + self.vs(20), iy + self.vs(18), bw, self.vs(3)),
                             border_radius=1)
        shown     = min(max_items, len(problems) - self.problem_scroll)
        remaining = len(problems) - self.problem_scroll - shown
        if remaining > 0:
            more_y = y + shown * item_h
            self._txt(surf, f"↓ {remaining} more  (↑/↓ keys)",
                      x, more_y, self.f_sm, DIM_TEXT)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        running   = True
        prev_size = (self.W, self.H)

        while running:
            dt_real = self.clock.tick(60) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

                elif event.type == pygame.WINDOWRESIZED:
                    nw, nh = event.x, event.y
                    if (nw, nh) != prev_size:
                        self.W, self.H = nw, nh
                        self._build_fonts()
                        self.skin.invalidate_scaled()
                        prev_size = (nw, nh)

                elif event.type == pygame.KEYDOWN:
                    running = self._key(event)

                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    running = self._mouse_down(event.pos)

                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    self.dragging      = False
                    self._vol_dragging = False

                elif event.type == pygame.MOUSEMOTION:
                    if self.dragging:
                        self._seek(self._timeline_to_ms(event.pos[0]))
                    elif self._vol_dragging:
                        self._set_volume_from_x(event.pos[0])

            if running:
                self._advance(dt_real)
                self._render()

    def _mouse_down(self, pos) -> bool:
        mx, my = pos

        # Tab clicks (always active)
        for i, rect in enumerate(self._tab_rects):
            if rect.collidepoint(mx, my):
                self._active_tab = i
                return True

        tx = self.vx(TIMELINE_X)
        ty = self.vy(TIMELINE_Y)
        tw = self.vx(TIMELINE_W)
        th = self.vs(TIMELINE_H)

        # Timeline scrub (only in Data tab)
        if (self._active_tab == 0 and
                tx <= mx <= tx + tw and
                ty - self.vs(12) <= my <= ty + th + self.vs(18)):
            self._seek(self._timeline_to_ms(mx))
            self.dragging = True
        # Volume slider
        elif self._vol_rect and self._vol_rect.collidepoint(mx, my):
            self._set_volume_from_x(mx)
            self._vol_dragging = True
        # Play/Pause button
        elif self._btn_play and self._btn_play.collidepoint(mx, my):
            self.playing = not self.playing
            if self.playing: self._start_audio()
            else:            self._stop_audio()
        # Speed down
        elif self._btn_spd_dn and self._btn_spd_dn.collidepoint(mx, my):
            self.speed_idx = max(0, self.speed_idx - 1)
            if self.playing:
                self._stop_audio()
                if self._speed() == 1.0: self._start_audio()
        # Speed up
        elif self._btn_spd_up and self._btn_spd_up.collidepoint(mx, my):
            self.speed_idx = min(len(SPEEDS) - 1, self.speed_idx + 1)
            if self.playing:
                self._stop_audio()
                if self._speed() == 1.0: self._start_audio()
        # Scroll speed down
        elif self._btn_scr_dn and self._btn_scr_dn.collidepoint(mx, my):
            self._scroll_speed = max(0.25, round(self._scroll_speed - 0.25, 2))
        # Scroll speed up
        elif self._btn_scr_up and self._btn_scr_up.collidepoint(mx, my):
            self._scroll_speed = min(8.0,  round(self._scroll_speed + 0.25, 2))
        return True

    def _key(self, event) -> bool:
        shift = pygame.key.get_mods() & pygame.KMOD_SHIFT

        if event.key in (pygame.K_q, pygame.K_ESCAPE):
            return False
        elif event.key == pygame.K_p and self.profile:
            self._open_profile = True
            return False
        elif event.key == pygame.K_SPACE:
            self.playing = not self.playing
            if self.playing: self._start_audio()
            else:            self._stop_audio()
        elif event.key == pygame.K_LEFT:
            self._seek(max(0.0, self.game_time - (30000 if shift else 5000)))
        elif event.key == pygame.K_RIGHT:
            self._seek(min(float(self.song_end_ms),
                           self.game_time + (30000 if shift else 5000)))
        elif event.key in (pygame.K_COMMA, pygame.K_LESS):
            self.speed_idx = max(0, self.speed_idx - 1)
            if self.playing:
                self._stop_audio()
                if self._speed() == 1.0: self._start_audio()
        elif event.key in (pygame.K_PERIOD, pygame.K_GREATER):
            self.speed_idx = min(len(SPEEDS) - 1, self.speed_idx + 1)
            if self.playing:
                self._stop_audio()
                if self._speed() == 1.0: self._start_audio()
        elif event.key == pygame.K_LEFTBRACKET:
            self._scroll_speed = max(0.25, round(self._scroll_speed - 0.25, 2))
        elif event.key == pygame.K_RIGHTBRACKET:
            self._scroll_speed = min(8.0,  round(self._scroll_speed + 0.25, 2))
        elif event.key == pygame.K_UP:
            self.problem_scroll = max(0, self.problem_scroll - 1)
        elif event.key == pygame.K_DOWN:
            self.problem_scroll = min(
                max(0, len(self.analysis.problems) - 1),
                self.problem_scroll + 1)
        elif event.key == pygame.K_HOME:
            self._seek(0.0)
        elif event.key == pygame.K_END:
            self._seek(float(self.song_end_ms))
        return True

    def _render(self):
        surf = self.screen
        surf.fill(BG)
        self._draw_gameplay(surf)
        self._draw_data(surf)
        self._draw_stats_panel(surf)
        pygame.display.flip()
