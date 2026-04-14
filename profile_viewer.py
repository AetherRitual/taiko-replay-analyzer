"""Full-screen profile overview — aggregated stats + replay list + pattern analysis."""
import math
from pathlib import Path
from collections import Counter

import pygame
import pygame.gfxdraw

from ui_common import sysfont as _sysfont_base, FONT_PREF as _FONT_PREF


def _sysfont(size: int, bold: bool = False) -> pygame.font.Font:
    return _sysfont_base(_FONT_PREF, size, bold=bold)

BASE_W, BASE_H = 1920, 1080

# ── Palette (matches viewer.py) ────────────────────────────────────────
BG        = (12,  14,  22)
PANEL_BG  = (18,  20,  30)
LINE_COL  = (40,  44,  65)
TEXT_COL  = (210, 215, 235)
DIM_TEXT  = (100, 108, 140)
GOLD      = (255, 200,  60)
H300_COL  = (100, 220, 120)
H100_COL  = (255, 200,  60)
MISS_COL  = (255,  80,  80)
ACCENT    = ( 80, 160, 255)
DON_COL   = (255,  75,  75)
KAT_COL   = ( 90, 160, 255)
DON_BIG   = (255,  90,  90)
KAT_BIG   = ( 50, 200, 255)

# Virtual x where the replay list starts
LIST_X    = 1300
TAB_H     = 36
_TABS     = ["Overview", "Patterns", "Playstyle"]


class ProfileViewer:
    """
    Full-screen profile overview.
    run() blocks until ESC or replay selected.
    Returns a replay record dict (with osr_path/osu_path) or None.
    """

    def __init__(self, profile: dict, songs_folder: Path,
                 screen: pygame.Surface):
        self.profile      = profile
        self.songs_folder = songs_folder
        self.screen       = screen
        self._scroll      = 0
        self._hovered     = -1
        self._active_tab  = 0
        self._tab_rects   = []
        self._running     = True
        self._result      = None

        w, h = screen.get_size()
        self._s = min(w / BASE_W, h / BASE_H)
        self._build_fonts()

        from profile import ProfileManager
        pm = ProfileManager()
        self._bpm_data    = pm.aggregated_bpm_acc(profile, min_total=5)
        self._pat_data    = pm.aggregated_patterns(profile, min_count=5)
        self._ps_data     = pm.aggregated_playstyle(profile)
        self._replays     = sorted(profile.get("replays", []),
                                   key=lambda r: r.get("date", ""), reverse=True)
        chrono            = sorted(profile.get("replays", []),
                                   key=lambda r: r.get("date", ""))
        self._ur_trend    = chrono[-40:]
        self._row_h       = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_fonts(self):
        s = self._s
        self.f_xs  = _sysfont(max(11, int(13 * s)))
        self.f_sm  = _sysfont(max(14, int(16 * s)))
        self.f_md  = _sysfont(max(16, int(20 * s)))
        self.f_lg  = _sysfont(max(18, int(26 * s)), bold=True)
        self.f_xl  = _sysfont(max(22, int(36 * s)), bold=True)

    def vx(self, x): return int(x * self._s * self.screen.get_width()  / BASE_W)
    def vy(self, y): return int(y * self._s * self.screen.get_height() / BASE_H)
    def vs(self, v): return max(1, int(v * self._s))

    def _txt(self, surf, text, x, y, font=None, color=TEXT_COL, anchor="topleft"):
        font = font or self.f_md
        img  = font.render(str(text), True, color)
        rect = img.get_rect(**{anchor: (x, y)})
        surf.blit(img, rect)
        return rect

    def _aa_circle(self, surf, cx, cy, r, color):
        if r < 1: return
        pygame.gfxdraw.aacircle(surf, cx, cy, r, color)
        pygame.gfxdraw.filled_circle(surf, cx, cy, r, color)

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    def run(self):
        clock = pygame.time.Clock()
        self._running = True
        self._result  = None
        while self._running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False
                elif event.type == pygame.WINDOWRESIZED:
                    self._s = min(event.x / BASE_W, event.y / BASE_H)
                    self._build_fonts()
                elif event.type == pygame.KEYDOWN:
                    self._handle_key(event)
                elif event.type == pygame.MOUSEMOTION:
                    self._handle_mouse_move(event.pos)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    self._handle_click(event.pos, event.button)
                elif event.type == pygame.MOUSEWHEEL:
                    self._scroll = max(0, self._scroll - event.y * 2)
            self._draw(self.screen)
            pygame.display.flip()
            clock.tick(60)
        return self._result

    def _cycle_layout(self):
        """Cycle the profile's layout: KDDK → DDKK → KKDD → KDDK and save."""
        _order = ("KDDK", "DDKK", "KKDD")
        current = self.profile.get("layout", "KDDK")
        next_layout = _order[(_order.index(current) + 1) % len(_order)] if current in _order else "KDDK"
        self.profile["layout"] = next_layout
        from profile import ProfileManager
        ProfileManager().save(self.profile)
        # Recompute aggregated playstyle (layout affects nothing there, but refresh ps_data)
        # The display will pick up the new layout on next draw.

    def _handle_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self._running = False
        elif event.key == pygame.K_UP:
            self._scroll = max(0, self._scroll - 3)
        elif event.key == pygame.K_DOWN:
            self._scroll += 3
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if 0 <= self._hovered < len(self._replays):
                self._open_replay(self._hovered)
        elif event.key == pygame.K_TAB:
            self._active_tab = (self._active_tab + 1) % len(_TABS)
            self._scroll = 0
        elif event.key == pygame.K_l:
            self._cycle_layout()

    def _handle_mouse_move(self, pos):
        # Tab hover
        for i, r in enumerate(self._tab_rects):
            if r.collidepoint(pos):
                return
        # Replay list hover
        if self._row_h is None:
            return
        lx = self.vx(LIST_X)
        if pos[0] < lx:
            self._hovered = -1
            return
        hdr_h   = self.vs(56)
        tab_h   = self.vs(TAB_H)
        list_y0 = hdr_h + tab_h + self.vs(52)
        rel_y   = pos[1] - list_y0
        if rel_y < 0:
            self._hovered = -1
            return
        idx = rel_y // self._row_h + self._scroll
        self._hovered = idx if 0 <= idx < len(self._replays) else -1

    def _handle_click(self, pos, button):
        if button == 1:
            for i, r in enumerate(self._tab_rects):
                if r.collidepoint(pos):
                    self._active_tab = i
                    self._scroll = 0
                    return
            if 0 <= self._hovered < len(self._replays):
                self._open_replay(self._hovered)

    def _open_replay(self, idx):
        rec = self._replays[idx]
        osr, osu = rec.get("osr_path"), rec.get("osu_path")
        if osr and osu and Path(osr).exists() and Path(osu).exists():
            self._result  = rec
            self._running = False

    # ------------------------------------------------------------------
    # Master draw
    # ------------------------------------------------------------------

    def _draw(self, surf):
        w, h = surf.get_size()
        surf.fill(BG)

        hdr_h = self.vs(56)
        # Header bar
        pygame.draw.rect(surf, PANEL_BG, (0, 0, w, hdr_h))
        pygame.draw.line(surf, LINE_COL, (0, hdr_h), (w, hdr_h), 1)

        name   = self.profile.get("display_name", "Unknown")
        n_reps = len(self._replays)
        dates  = [r.get("date","") for r in self._replays if r.get("date")]
        drange = f"{min(dates)} – {max(dates)}" if len(dates) >= 2 else (dates[0] if dates else "")

        self._txt(surf, name,
                  self.vs(22), hdr_h // 2, self.f_lg, TEXT_COL, anchor="midleft")
        badge = f"{n_reps} replay{'s' if n_reps != 1 else ''}  ·  {drange}"
        self._txt(surf, badge,
                  self.vs(22) + self.f_lg.size(name)[0] + self.vs(14),
                  hdr_h // 2, self.f_sm, DIM_TEXT, anchor="midleft")
        self._txt(surf, "[ESC] back  [Tab] cycle tabs",
                  w - self.vs(16), hdr_h // 2, self.f_sm, DIM_TEXT, anchor="midright")

        # Tabs (left panel only)
        lx    = self.vx(LIST_X)
        tab_h = self.vs(TAB_H)
        tw    = lx // len(_TABS)
        self._tab_rects = []
        for i, label in enumerate(_TABS):
            rect = pygame.Rect(i * tw, hdr_h, tw, tab_h)
            self._tab_rects.append(rect)
            active = (i == self._active_tab)
            bg     = PANEL_BG if active else BG
            pygame.draw.rect(surf, bg, rect)
            col    = TEXT_COL if active else DIM_TEXT
            self._txt(surf, label,
                      rect.centerx, rect.centery, self.f_sm, col, anchor="center")
            if active:
                pygame.draw.line(surf, ACCENT,
                                 (rect.left, rect.bottom - 1),
                                 (rect.right - 1, rect.bottom - 1), 2)
        pygame.draw.line(surf, LINE_COL, (0, hdr_h + tab_h), (lx, hdr_h + tab_h), 1)

        # Vertical divider
        pygame.draw.line(surf, LINE_COL, (lx, hdr_h), (lx, h), 1)

        # Left panel content
        pad   = self.vs(24)
        top_y = hdr_h + tab_h + self.vs(12)
        if self._active_tab == 0:
            self._draw_overview(surf, pad, top_y, lx - pad * 2, h - top_y)
        elif self._active_tab == 1:
            self._draw_patterns(surf, pad, top_y, lx - pad * 2, h - top_y)
        else:
            self._draw_playstyle(surf, pad, top_y, lx - pad * 2, h - top_y)

        # Right: replay list (always visible)
        self._draw_replay_list(surf, lx + self.vs(14), hdr_h + tab_h,
                               w - lx - self.vs(14), h - hdr_h - tab_h)

    # ------------------------------------------------------------------
    # Overview tab
    # ------------------------------------------------------------------

    def _draw_overview(self, surf, x, y, avail_w, avail_h):
        n_reps = len(self._replays)
        self._txt(surf, "BPM Comfortability",  x, y, self.f_md, (200, 205, 230))
        self._txt(surf, f"aggregated · {n_reps} replays · weighted by map difficulty (OD)",
                  x + avail_w, y + self.vs(3), self.f_xs, DIM_TEXT, anchor="topright")
        y += self.vs(24)

        chart_h = self.vs(160)
        self._draw_comfort_chart(surf, x, y, avail_w, chart_h)
        y += chart_h + self.vs(32)

        # UR + Accuracy trends side by side
        half     = (avail_w - self.vs(14)) // 2
        trend_h  = self.vs(110)
        self._draw_trend(surf, x,                        y, half, trend_h,
                         "ur",       "UR Trend",        GOLD,     lower_better=True)
        self._draw_trend(surf, x + half + self.vs(14),   y, half, trend_h,
                         "accuracy", "Accuracy Trend",  H300_COL, lower_better=False)
        y += trend_h + self.vs(32)

        # Best / Worst BPM buckets — confidence-weighted so low-sample buckets don't dominate
        if len(self._bpm_data) >= 2:
            # Confidence score: blend comfort toward 50% for small samples (< 60 notes)
            def _conf(d):
                c = min(1.0, d["total"] / 60.0)
                return d["comfort"] * c + 50.0 * (1 - c)

            by_conf = sorted(self._bpm_data, key=_conf)
            worst3  = by_conf[:3]
            best3   = by_conf[-3:][::-1]
            col_w   = avail_w // 2 - self.vs(8)
            self._txt(surf, "Strongest", x,                       y, self.f_sm, H300_COL)
            self._txt(surf, "Weakest",   x + col_w + self.vs(16), y, self.f_sm, MISS_COL)
            y += self.vs(20)
            for be, we in zip(best3, worst3):
                self._txt(surf,
                          f"{int(be['bpm'])} BPM  {be['comfort']:.0f}%  ×{be['total']}",
                          x, y, self.f_xs, H300_COL)
                self._txt(surf,
                          f"{int(we['bpm'])} BPM  {we['comfort']:.0f}%  ×{we['total']}",
                          x + col_w + self.vs(16), y, self.f_xs, MISS_COL)
                y += self.vs(18)

    def _draw_comfort_chart(self, surf, x, y, w, h):
        pygame.draw.rect(surf, (14, 16, 26), (x, y, w, h), border_radius=self.vs(4))
        pygame.draw.rect(surf, LINE_COL,     (x, y, w, h), 1, border_radius=self.vs(4))

        if not self._bpm_data:
            self._txt(surf, "Not enough data  (need ≥ 5 notes per BPM bucket)",
                      x + w // 2, y + h // 2, self.f_sm, DIM_TEXT, anchor="center")
            return

        for pct in (70, 80, 90, 100):
            gy = y + h - int(h * pct / 100)
            pygame.draw.line(surf, (35, 40, 60), (x, gy), (x + w, gy), 1)
            self._txt(surf, f"{pct}%", x - self.vs(4), gy,
                      self.f_xs, (80, 85, 110), anchor="topright")

        bpms  = [d["bpm"]     for d in self._bpm_data]
        comfs = [d["comfort"] for d in self._bpm_data]
        span  = max(max(bpms) - min(bpms), 1)
        bar_w = max(4, min(self.vs(22), w // max(1, len(bpms)) - 2))

        for bpm, comf, d in zip(bpms, comfs, self._bpm_data):
            bx   = x + int((bpm - min(bpms)) / span * (w - bar_w))
            bh   = max(2, int(h * comf / 100))
            # color gradient: green (comfortable) → red (struggling)
            t    = max(0.0, min(1.0, (comf - 70) / 30))   # 70%=red .. 100%=green
            bcol = (int(220 * (1 - t)), int(220 * t), 60)
            pygame.draw.rect(surf, tuple(max(0, c - 50) for c in bcol),
                             (bx, y + h - bh, bar_w, bh), border_radius=self.vs(2))
            self._aa_circle(surf, bx + bar_w // 2, y + h - bh, self.vs(3), bcol)
            self._txt(surf, str(int(bpm)),
                      bx + bar_w // 2, y + h + self.vs(2),
                      self.f_xs, DIM_TEXT, anchor="midtop")
            # note count inside bar if it fits
            if bh > self.vs(18):
                self._txt(surf, str(d["total"]),
                          bx + bar_w // 2, y + h - bh + self.vs(3),
                          self.f_xs, (0, 0, 0), anchor="midtop")

    def _draw_trend(self, surf, x, y, w, h, key, title, line_col, lower_better=False):
        self._txt(surf, title, x, y, self.f_sm, DIM_TEXT)
        y += self.vs(18); h -= self.vs(18)
        pygame.draw.rect(surf, (14, 16, 26), (x, y, w, h), border_radius=self.vs(4))
        pygame.draw.rect(surf, LINE_COL,     (x, y, w, h), 1, border_radius=self.vs(4))

        vals = [r[key] for r in self._ur_trend if r.get(key) is not None]
        if len(vals) < 2:
            self._txt(surf, "not enough data",
                      x + w // 2, y + h // 2, self.f_xs, DIM_TEXT, anchor="center")
            return

        lo, hi = min(vals), max(vals)
        span   = max(hi - lo, 0.001)
        pts    = []
        for i, v in enumerate(vals):
            px   = x + self.vs(4) + int(i / (len(vals) - 1) * (w - self.vs(8)))
            frac = (v - lo) / span
            if lower_better: frac = 1 - frac
            py   = y + self.vs(4) + int((1 - frac) * (h - self.vs(8)))
            pts.append((px, py))

        r0, g0, b0 = line_col
        fill = pygame.Surface((w, h), pygame.SRCALPHA)
        poly = [(x, y + h)] + pts + [(x + w, y + h)]
        pygame.draw.polygon(fill, (r0, g0, b0, 30),
                            [(px - x, py - y) for px, py in poly])
        surf.blit(fill, (x, y))
        pygame.draw.lines(surf, line_col, False, pts, 2)
        self._aa_circle(surf, pts[-1][0], pts[-1][1], self.vs(4), GOLD)

        best = min(vals) if lower_better else max(vals)
        self._txt(surf, f"best {best:.1f}",
                  x + self.vs(5), y + self.vs(3), self.f_xs, DIM_TEXT)
        self._txt(surf, f"now {vals[-1]:.1f}",
                  x + w - self.vs(5), y + h - self.vs(14),
                  self.f_xs, GOLD, anchor="topright")

    # ------------------------------------------------------------------
    # Patterns tab
    # ------------------------------------------------------------------

    def _draw_patterns(self, surf, x, y, avail_w, avail_h):
        n_reps = len(self._replays)
        pats   = self._pat_data

        # Header
        n_critical = sum(1 for p in pats if p["delta"] < -5  and p["count"] >= 5)
        n_flag     = sum(1 for p in pats if p["delta"] < -2  and p["count"] >= 3)
        if n_critical:
            hdr = f"{n_critical} pattern{'s' if n_critical != 1 else ''} significantly below average"
            hdr_col = MISS_COL
        elif n_flag:
            hdr = f"{n_flag} pattern{'s' if n_flag != 1 else ''} below average"
            hdr_col = H100_COL
        else:
            hdr = "No consistently problematic patterns"
            hdr_col = H300_COL
        self._txt(surf, hdr, x, y, self.f_md, hdr_col)
        self._txt(surf, f"{n_reps} replays  ·  overall pattern performance",
                  x + avail_w, y + self.vs(3), self.f_xs, DIM_TEXT, anchor="topright")
        y += self.vs(28)

        if not pats:
            self._txt(surf,
                      "No pattern data yet. Add replays via mass-add or the main viewer.",
                      x, y, self.f_sm, DIM_TEXT)
            return

        overall_acc = (
            sum(p["n300"] for p in pats) + 0.5 * sum(p["n100"] for p in pats)
        ) / max(1, sum(p["count"] for p in pats)) * 100

        # ── Pattern cards (worst-first, no BPM shown) ────────────────
        candidates = [p for p in pats if p["count"] >= 3][:15]
        if candidates:
            self._txt(surf, "Worst patterns  (4-note windows · aggregated across all replays)",
                      x, y, self.f_sm, DIM_TEXT)
            y += self.vs(18)

            cards_per_row = 5
            card_gap = self.vs(4)
            card_w   = (avail_w - card_gap * (cards_per_row - 1)) // cards_per_row
            card_h   = self.vs(96)   # slightly shorter — no BPM line
            note_r   = self.vs(10)
            _ni_top  = self.vs(8)
            _sep_y   = self.vs(38)
            _stat_cy = self.vs(66)
            _acc_cx  = 2
            _right_x = 5

            for idx, pat in enumerate(candidates):
                col_i = idx % cards_per_row
                row_i = idx // cards_per_row
                cx = x + col_i * (card_w + card_gap)
                cy = y + row_i * (card_h + self.vs(6))

                miss_rate = pat["nmiss"] / max(1, pat["count"])
                acc_col = (MISS_COL if miss_rate > 0 or pat["acc"] < overall_acc - 5
                           else H100_COL if pat["acc"] < overall_acc - 1
                           else H300_COL)
                bg = pygame.Rect(cx, cy, card_w, card_h)
                pygame.draw.rect(surf, (22, 24, 38), bg, border_radius=self.vs(5))
                pygame.draw.rect(surf, acc_col, bg, 1, border_radius=self.vs(5))

                # Top: note circles
                ni_total_w = len(pat["pattern"]) * (note_r * 2 + self.vs(3)) - self.vs(3)
                ni_x  = cx + (card_w - ni_total_w) // 2
                ni_cy = cy + _ni_top + note_r
                for sym in pat["pattern"]:
                    is_big = isinstance(sym, str) and sym.endswith("b")
                    kind   = sym[0] if isinstance(sym, str) else sym
                    nc = DON_COL if kind == "D" else KAT_COL
                    self._aa_circle(surf, ni_x + note_r, ni_cy, note_r, nc)
                    if is_big:
                        pygame.gfxdraw.aacircle(surf, ni_x + note_r, ni_cy,
                                                note_r + self.vs(2), (*nc, 140))
                    ni_x += note_r * 2 + self.vs(3)

                # Separator
                pygame.draw.line(surf, (40, 44, 65),
                                 (cx + self.vs(8), cy + _sep_y),
                                 (cx + card_w - self.vs(8), cy + _sep_y), 1)

                # Bottom: acc% left, delta+count right
                acc_cx_px = cx + card_w * _acc_cx // 9
                self._txt(surf, f"{pat['acc']:.0f}%",
                          acc_cx_px, cy + _stat_cy,
                          self.f_md, acc_col, anchor="center")

                div_x     = cx + card_w * _right_x // 9
                pygame.draw.line(surf, (40, 44, 65),
                                 (div_x - self.vs(4), cy + _sep_y + self.vs(6)),
                                 (div_x - self.vs(4), cy + self.vs(90)), 1)
                delta_col = (H300_COL if pat["delta"] >= 0
                             else H100_COL if pat["delta"] > -3 else MISS_COL)
                self._txt(surf, f"{pat['delta']:+.0f}% vs avg",
                          div_x, cy + _stat_cy - self.vs(10), self.f_xs, delta_col)
                meta = f"×{pat['count']}"
                if pat.get("nmiss"):
                    meta += f"  {pat['nmiss']}✕"
                self._txt(surf, meta,
                          div_x, cy + _stat_cy + self.vs(4), self.f_xs, DIM_TEXT)

            rows_used = math.ceil(len(candidates) / cards_per_row)
            y += rows_used * (card_h + self.vs(6)) + self.vs(16)

        # ── Worst lead-in patterns ────────────────────────────────────
        bad_with_lead = [p for p in candidates
                         if p.get("worst_lead") and p["delta"] < -1][:4]
        if bad_with_lead:
            self._txt(surf, "Worst lead-in contexts", x, y, self.f_sm, DIM_TEXT)
            y += self.vs(18)
            nr2   = self.vs(7)
            row_h = self.vs(22)
            for pat in bad_with_lead:
                lx = x
                # Pattern circles
                for sym in pat["pattern"]:
                    nc = DON_COL if (isinstance(sym, str) and sym[0] == "D") else KAT_COL
                    self._aa_circle(surf, lx + nr2, y + row_h // 2, nr2, nc)
                    lx += nr2 * 2 + self.vs(2)
                lx += self.vs(6)
                self._txt(surf, "preceded by", lx, y, self.f_xs, DIM_TEXT)
                lx += self.f_xs.size("preceded by")[0] + self.vs(6)
                for sym in pat["worst_lead"]:
                    nc = DON_COL if sym == "D" else KAT_COL
                    self._aa_circle(surf, lx + nr2, y + row_h // 2, nr2, nc)
                    lx += nr2 * 2 + self.vs(2)
                lx += self.vs(6)
                self._txt(surf, f"→ {pat['worst_lead_acc']:.0f}% acc",
                          lx, y, self.f_xs, MISS_COL)
                y += row_h
            y += self.vs(12)

        # ── Best patterns ─────────────────────────────────────────────
        best_pats = sorted([p for p in pats if p["count"] >= 5],
                           key=lambda p: -p["delta"])[:5]
        if best_pats:
            self._txt(surf, "Strongest patterns", x, y, self.f_sm, H300_COL)
            y += self.vs(18)
            row_h   = self.vs(22)
            note_r2 = self.vs(7)
            for pat in best_pats:
                lx = x
                for sym in pat["pattern"]:
                    nc = DON_COL if (isinstance(sym, str) and sym[0] == "D") else KAT_COL
                    self._aa_circle(surf, lx + note_r2, y + row_h // 2, note_r2, nc)
                    lx += note_r2 * 2 + self.vs(2)
                lx += self.vs(6)
                self._txt(surf,
                          f"{pat['acc']:.0f}%  {pat['delta']:+.0f}%  ×{pat['count']}",
                          lx, y, self.f_xs, H300_COL)
                y += row_h

    # ------------------------------------------------------------------
    # Playstyle tab
    # ------------------------------------------------------------------

    def _draw_pattern_grid(self, surf, cx, cy, card_w, area_h, pattern, avg_gap_fracs):
        """
        Draw note circles on a beat-grid timeline — proportional to avg_gap_fracs.
        Tick marks at 1/4 beat positions show whether notes land on-grid.
        """
        pad        = self.vs(8)
        grid_x     = cx + pad
        grid_w     = card_w - pad * 2
        note_r     = self.vs(8)
        baseline_y = cy + area_h - self.vs(6)
        note_cy    = baseline_y - note_r - self.vs(3)

        N = len(pattern)
        gap_fracs = avg_gap_fracs if avg_gap_fracs and len(avg_gap_fracs) == N - 1 \
                    else [0.25] * (N - 1)
        positions = [0.0]
        for f in gap_fracs:
            positions.append(positions[-1] + f)
        total_span   = max(positions[-1], 0.01)
        display_span = total_span * 1.15
        scale        = grid_w / display_span

        pygame.draw.line(surf, (45, 50, 75),
                         (grid_x, baseline_y), (grid_x + grid_w, baseline_y), 1)

        t = 0.0
        while t <= display_span + 0.01:
            tx     = int(grid_x + t * scale)
            is_beat = abs(t - round(t)) < 0.01
            tick_h  = self.vs(5) if is_beat else self.vs(3)
            col     = (70, 76, 110) if is_beat else (50, 55, 82)
            pygame.draw.line(surf, col, (tx, baseline_y), (tx, baseline_y + tick_h), 1)
            t = round(t + 0.25, 6)

        for sym, pos in zip(pattern, positions):
            is_big = isinstance(sym, str) and sym.endswith("b")
            kind   = sym[0] if isinstance(sym, str) else sym
            nc     = DON_COL if kind == "D" else KAT_COL
            nx     = int(grid_x + pos * scale)
            pygame.draw.line(surf, (38, 42, 62),
                             (nx, note_cy + note_r), (nx, baseline_y), 1)
            self._aa_circle(surf, nx, note_cy, note_r, nc)
            if is_big:
                pygame.gfxdraw.aacircle(surf, nx, note_cy,
                                        note_r + self.vs(2), (*nc, 140))

    def _draw_gauge(self, surf, x, y, w, h, value, lo=0.0, hi=1.0,
                    left_label="", right_label="", marker_col=GOLD):
        """Horizontal filled gauge with a marker dot and end labels."""
        pygame.draw.rect(surf, (22, 24, 38), (x, y, w, h), border_radius=self.vs(3))
        pygame.draw.rect(surf, LINE_COL,     (x, y, w, h), 1, border_radius=self.vs(3))
        frac = max(0.0, min(1.0, (value - lo) / max(hi - lo, 1e-9)))
        fill_w = max(self.vs(4), int(w * frac))
        t = frac
        col = (int(220 * (1 - t) + 80 * t),
               int(80  * (1 - t) + 220 * t),
               60)
        pygame.draw.rect(surf, col, (x, y, fill_w, h), border_radius=self.vs(3))
        mx = x + int(w * frac)
        self._aa_circle(surf, mx, y + h // 2, self.vs(4), marker_col)
        if left_label:
            self._txt(surf, left_label,  x + self.vs(4), y + h // 2,
                      self.f_xs, (0, 0, 0), anchor="midleft")
        if right_label:
            self._txt(surf, right_label, x + w - self.vs(4), y + h // 2,
                      self.f_xs, (0, 0, 0), anchor="midright")

    def _draw_mini_trend(self, surf, x, y, w, h, series, line_col, title=""):
        """Compact sparkline with title."""
        if title:
            self._txt(surf, title, x, y, self.f_xs, DIM_TEXT)
            y += self.vs(14); h -= self.vs(14)
        pygame.draw.rect(surf, (14, 16, 26), (x, y, w, h), border_radius=self.vs(3))
        pygame.draw.rect(surf, LINE_COL,     (x, y, w, h), 1, border_radius=self.vs(3))
        if len(series) < 2:
            self._txt(surf, "—", x + w // 2, y + h // 2, self.f_xs, DIM_TEXT, anchor="center")
            return
        lo, hi = min(series), max(series)
        span   = max(hi - lo, 0.001)
        pts = []
        for i, v in enumerate(series):
            px = x + self.vs(3) + int(i / (len(series) - 1) * (w - self.vs(6)))
            py = y + self.vs(3) + int((1 - (v - lo) / span) * (h - self.vs(6)))
            pts.append((px, py))
        pygame.draw.lines(surf, line_col, False, pts, 2)
        self._aa_circle(surf, pts[-1][0], pts[-1][1], self.vs(3), GOLD)
        self._txt(surf, f"{series[-1]*100:.0f}%", pts[-1][0] + self.vs(4), pts[-1][1],
                  self.f_xs, GOLD)

    def _draw_playstyle(self, surf, x, y, avail_w, avail_h):
        ps = self._ps_data
        n_reps = len(self._replays)

        self._txt(surf, "Playstyle Analysis", x, y, self.f_md, (200, 205, 230))
        self._txt(surf, f"{n_reps} replay{'s' if n_reps != 1 else ''}",
                  x + avail_w, y + self.vs(3), self.f_xs, DIM_TEXT, anchor="topright")
        self._txt(surf, "[L] change layout",
                  x + avail_w, y + self.vs(16), self.f_xs, DIM_TEXT, anchor="topright")
        y += self.vs(28)

        if not ps:
            self._txt(surf,
                      "No playstyle data yet. Re-add replays to populate this tab.",
                      x, y, self.f_sm, DIM_TEXT)
            return

        profile_layout = self.profile.get("layout", "KDDK")
        is_grouped = profile_layout in ("DDKK", "KKDD")  # clustered layouts use finger-alt metrics

        # ── Dominant classification ───────────────────────────────────
        # For grouped layouts (DDKK/KKDD) the stored names were derived from KDDK L-R
        # logic and are meaningless. Re-classify from finger-alt metrics instead.
        if is_grouped:
            dfa = ps.get("avg_don_finger_alt") or 0.0
            kfa = ps.get("avg_kat_finger_alt") or 0.0
            avg_fa = (dfa + kfa) / 2
            if dfa >= 0.75 and kfa >= 0.75:
                display_name = "Full-Alt"
            elif avg_fa >= 0.52:
                display_name = "Semi-Alt"
            elif avg_fa >= 0.25:
                display_name = "Singletap"
            else:
                display_name = "Roll"
        else:
            display_name = ps["dominant_name"]

        NAME_COLS = {"Full-Alt": H300_COL, "Semi-Alt": H100_COL,
                     "Singletap": ACCENT,  "Roll": DIM_TEXT}
        name_col = NAME_COLS.get(display_name, TEXT_COL)
        self._txt(surf, display_name, x, y, self.f_xl, name_col)
        badge_x = x + self.f_xl.size(display_name)[0] + self.vs(14)
        self._txt(surf, "dominant style", badge_x, y + self.vs(10), self.f_xs, DIM_TEXT)

        # Distribution pills — only shown for KDDK (grouped layouts have no per-replay names)
        if not is_grouped:
            dist_y = y + self.vs(4)
            pill_x = badge_x
            for name, cnt in ps["name_counts"].items():
                if cnt == 0: continue
                label = f"{name}  ×{cnt}"
                lw = self.f_xs.size(label)[0] + self.vs(10)
                col = NAME_COLS.get(name, TEXT_COL)
                pygame.draw.rect(surf, (28, 32, 50),
                                 (pill_x, dist_y + self.vs(14),
                                  lw, self.vs(16)), border_radius=self.vs(3))
                pygame.draw.rect(surf, col,
                                 (pill_x, dist_y + self.vs(14),
                                  lw, self.vs(16)), 1, border_radius=self.vs(3))
                self._txt(surf, label, pill_x + self.vs(5),
                          dist_y + self.vs(14) + self.vs(8), self.f_xs, col, anchor="midleft")
                pill_x += lw + self.vs(6)
                if pill_x + self.vs(80) > x + avail_w:
                    break

        y += self.vs(52)

        # ── Layout badge ─────────────────────────────────────────────
        layout_col = H100_COL if is_grouped else (180, 180, 210)
        self._txt(surf, profile_layout, x + avail_w, y - self.vs(52),
                  self.f_sm, layout_col, anchor="topright")

        if is_grouped:
            # ── DDKK: finger alternation gauges ──────────────────────
            dfa = ps.get("avg_don_finger_alt")
            kfa = ps.get("avg_kat_finger_alt")

            if dfa is not None:
                dfa_col = (H300_COL if dfa >= 0.75 else H100_COL if dfa >= 0.50 else ACCENT)
                self._txt(surf, "Don-finger alternation", x, y, self.f_sm, DIM_TEXT)
                self._txt(surf, f"{dfa*100:.1f}%  avg", x + avail_w, y,
                          self.f_sm, dfa_col, anchor="topright")
                y += self.vs(20)
                self._draw_gauge(surf, x, y, avail_w, self.vs(22), dfa,
                                 lo=0.0, hi=1.0, left_label="0%", right_label="100%",
                                 marker_col=dfa_col)
                y += self.vs(32)

            if kfa is not None:
                kfa_col = (H300_COL if kfa >= 0.75 else H100_COL if kfa >= 0.50 else ACCENT)
                self._txt(surf, "Kat-finger alternation", x, y, self.f_sm, DIM_TEXT)
                self._txt(surf, f"{kfa*100:.1f}%  avg", x + avail_w, y,
                          self.f_sm, kfa_col, anchor="topright")
                y += self.vs(20)
                self._draw_gauge(surf, x, y, avail_w, self.vs(22), kfa,
                                 lo=0.0, hi=1.0, left_label="0%", right_label="100%",
                                 marker_col=kfa_col)
                y += self.vs(32)

        else:
            # ── KDDK: L-R alternation rate ────────────────────────────
            avg_alt  = ps["avg_alt_rate"]
            alt_col  = (H300_COL if avg_alt >= 0.88 else H100_COL
                        if avg_alt >= 0.52 else ACCENT)
            self._txt(surf, "Pattern Alt Rate", x, y, self.f_sm, DIM_TEXT)
            self._txt(surf, f"{avg_alt*100:.1f}%  avg",
                      x + avail_w, y, self.f_sm, alt_col, anchor="topright")
            y += self.vs(20)
            self._draw_gauge(surf, x, y, avail_w, self.vs(22), avg_alt,
                             lo=0.0, hi=1.0, left_label="0%", right_label="100%",
                             marker_col=alt_col)
            y += self.vs(32)

            # ── KDDK: Left / right balance ────────────────────────────
            lb       = ps["avg_left_bias"]
            bias_str = f"{lb*100:.0f}% L  /  {(1-lb)*100:.0f}% R"
            self._txt(surf, "Side balance", x, y, self.f_sm, DIM_TEXT)
            self._txt(surf, bias_str, x + avail_w, y, self.f_sm, TEXT_COL, anchor="topright")
            y += self.vs(20)
            half_w = avail_w // 2
            pygame.draw.rect(surf, (60, 40, 40),
                             (x, y, half_w, self.vs(22)), border_radius=self.vs(3))
            pygame.draw.rect(surf, (40, 40, 60),
                             (x + half_w, y, half_w, self.vs(22)), border_radius=self.vs(3))
            pygame.draw.rect(surf, LINE_COL, (x, y, avail_w, self.vs(22)), 1,
                             border_radius=self.vs(3))
            self._txt(surf, "L", x + self.vs(6), y + self.vs(11),
                      self.f_xs, (200, 120, 120), anchor="midleft")
            self._txt(surf, "R", x + avail_w - self.vs(6), y + self.vs(11),
                      self.f_xs, (120, 120, 200), anchor="midright")
            marker_x = x + int(avail_w * lb)
            self._aa_circle(surf, marker_x, y + self.vs(11), self.vs(5), GOLD)
            y += self.vs(34)

            # ── KDDK: cross-phrase hand alternation ───────────────────
            avg_psa = ps.get("avg_phrase_start_alt_rate")
            if avg_psa is not None:
                avg_L   = ps.get("avg_phrase_alt_L_rate") or 0.0
                avg_R   = ps.get("avg_phrase_alt_R_rate") or 0.0
                psa_col = (H300_COL if avg_psa >= 0.65 else H100_COL if avg_psa >= 0.4 else MISS_COL)
                l_col   = (H300_COL if avg_L >= 0.65 else H100_COL if avg_L >= 0.4 else MISS_COL)
                r_col   = (H300_COL if avg_R >= 0.65 else H100_COL if avg_R >= 0.4 else MISS_COL)
                self._txt(surf, "Full-Alt %", x, y, self.f_sm, DIM_TEXT)
                self._txt(surf, f"{avg_psa*100:.1f}%  avg", x + avail_w, y, self.f_sm, psa_col,
                          anchor="topright")
                y += self.vs(20)
                self._txt(surf, "start-L", x, y, self.f_xs, DIM_TEXT)
                self._txt(surf, f"{avg_L*100:.0f}%", x + avail_w, y, self.f_xs, l_col,
                          anchor="topright")
                y += self.vs(18)
                self._txt(surf, "start-R", x, y, self.f_xs, DIM_TEXT)
                self._txt(surf, f"{avg_R*100:.0f}%", x + avail_w, y, self.f_xs, r_col,
                          anchor="topright")
                y += self.vs(24)

        # ── Alt-break frequency ───────────────────────────────────────
        avg_dt = ps["avg_dt_count"]
        self._txt(surf, "Alt breaks / replay", x, y, self.f_sm, DIM_TEXT)
        dt_col = (H300_COL if avg_dt < 2 else H100_COL if avg_dt < 8 else MISS_COL)
        self._txt(surf, f"{avg_dt:.1f}  avg", x + avail_w, y, self.f_sm, dt_col,
                  anchor="topright")
        y += self.vs(28)

        # ── Key usage ─────────────────────────────────────────────────
        self._txt(surf, "Key usage  (aggregated hits)", x, y, self.f_sm, DIM_TEXT)
        y += self.vs(20)
        fracs = ps["key_fracs"]
        if profile_layout == "DDKK":
            # Physical order: D-D-K-K
            key_layout = [("M1", "D", DON_COL), ("K1", "D", DON_COL),
                          ("M2", "K", KAT_COL),  ("K2", "K", KAT_COL)]
        elif profile_layout == "KKDD":
            # Physical order: K-K-D-D (mirror of DDKK)
            key_layout = [("M2", "K", KAT_COL), ("K2", "K", KAT_COL),
                          ("M1", "D", DON_COL),  ("K1", "D", DON_COL)]
        else:
            # KDDK physical order: K-D-D-K (default)
            key_layout = [("M2", "K", KAT_COL), ("M1", "D", DON_COL),
                          ("K1", "D", DON_COL),  ("K2", "K", KAT_COL)]
        bar_gap = self.vs(6)
        bar_w   = (avail_w - bar_gap * 3) // 4
        bar_max = self.vs(56)
        max_frac = max(fracs.values()) if fracs else 1.0
        for i, (key, label, col) in enumerate(key_layout):
            bx   = x + i * (bar_w + bar_gap)
            frac = fracs.get(key, 0.0)
            bh   = max(self.vs(4), int(bar_max * frac / max(max_frac, 0.001)))
            pygame.draw.rect(surf, (22, 24, 38),
                             (bx, y, bar_w, bar_max), border_radius=self.vs(3))
            pygame.draw.rect(surf, tuple(max(0, c - 60) for c in col),
                             (bx, y + bar_max - bh, bar_w, bh),
                             border_radius=self.vs(3))
            self._txt(surf, f"{label}",
                      bx + bar_w // 2, y + bar_max + self.vs(4),
                      self.f_sm, col, anchor="midtop")
            self._txt(surf, f"{frac*100:.1f}%",
                      bx + bar_w // 2, y + bar_max - bh - self.vs(2),
                      self.f_xs, col, anchor="midbottom")
            self._txt(surf, key,
                      bx + bar_w // 2, y + bar_max + self.vs(18),
                      self.f_xs, DIM_TEXT, anchor="midtop")
        y += bar_max + self.vs(36)

        # ── Trends ────────────────────────────────────────────────────
        half_w  = (avail_w - self.vs(14)) // 2
        mini_h  = self.vs(76)
        if is_grouped:
            dfa_series = ps.get("don_finger_alt_series", [])
            kfa_series = ps.get("kat_finger_alt_series", [])
            self._draw_mini_trend(surf, x, y, half_w, mini_h,
                                  dfa_series, DON_COL, "Don-finger alt over time")
            self._draw_mini_trend(surf, x + half_w + self.vs(14), y, half_w, mini_h,
                                  kfa_series, KAT_COL, "Kat-finger alt over time")
        else:
            L_series = ps.get("phrase_alt_L_series", [])
            R_series = ps.get("phrase_alt_R_series", [])
            if len(L_series) >= 2 and len(R_series) >= 2:
                self._draw_mini_trend(surf, x, y, half_w, mini_h,
                                      L_series, (120, 180, 255), "Full-Alt % — L hand over time")
                self._draw_mini_trend(surf, x + half_w + self.vs(14), y, half_w, mini_h,
                                      R_series, (255, 160, 100), "Full-Alt % — R hand over time")
            else:
                self._draw_mini_trend(surf, x, y, half_w, mini_h,
                                      ps["alt_rate_series"],  ACCENT,   "Pattern Alt Rate over time")
                self._draw_mini_trend(surf, x + half_w + self.vs(14), y, half_w, mini_h,
                                      ps["left_bias_series"], H100_COL, "Left-hand bias over time")

    # ------------------------------------------------------------------
    # Replay list (right panel, always shown)
    # ------------------------------------------------------------------

    def _draw_replay_list(self, surf, x, y0, col_w, col_h):
        self._txt(surf, "Replays", x, y0 + self.vs(8), self.f_md, (200, 205, 230))
        y0 += self.vs(34)

        row_h = self.vs(52)
        self._row_h = row_h

        hdr_y = y0
        self._txt(surf, "Date",  x,                       hdr_y, self.f_xs, DIM_TEXT)
        self._txt(surf, "Map",   x + self.vs(72),          hdr_y, self.f_xs, DIM_TEXT)
        self._txt(surf, "Acc",   x + col_w - self.vs(84),  hdr_y, self.f_xs, DIM_TEXT)
        self._txt(surf, "UR",    x + col_w - self.vs(34),  hdr_y, self.f_xs, DIM_TEXT)
        y0 += self.vs(18)
        pygame.draw.line(surf, LINE_COL,
                         (x - self.vs(4), y0 - self.vs(3)),
                         (x + col_w - self.vs(8), y0 - self.vs(3)), 1)

        visible = max(1, (col_h - self.vs(52)) // row_h)
        max_sc  = max(0, len(self._replays) - visible)
        self._scroll = min(self._scroll, max_sc)

        char_w    = max(1, self.f_xs.size("A")[0])
        max_chars = max(8, (col_w - self.vs(200)) // char_w)

        for i in range(visible):
            idx = i + self._scroll
            if idx >= len(self._replays):
                break
            rec = self._replays[idx]
            ry  = y0 + i * row_h

            osr, osu = rec.get("osr_path"), rec.get("osu_path")
            has_file = bool(osr and osu and Path(osr).exists() and Path(osu).exists())

            if idx == self._hovered and has_file:
                pygame.draw.rect(surf, (30, 36, 58),
                                 (x - self.vs(4), ry, col_w - self.vs(4), row_h - self.vs(2)),
                                 border_radius=self.vs(3))
                pygame.draw.rect(surf, ACCENT,
                                 (x - self.vs(4), ry, col_w - self.vs(4), row_h - self.vs(2)),
                                 1, border_radius=self.vs(3))
            elif i % 2 == 0:
                pygame.draw.rect(surf, (16, 18, 28),
                                 (x - self.vs(4), ry, col_w - self.vs(4), row_h - self.vs(2)),
                                 border_radius=self.vs(3))

            txt_col = TEXT_COL if has_file else DIM_TEXT
            acc_val = rec.get("accuracy", 0)
            acc_col = (H300_COL if acc_val >= 98 else H100_COL if acc_val >= 95
                       else MISS_COL if acc_val < 90 else TEXT_COL)

            self._txt(surf, rec.get("date", "—"),
                      x, ry + self.vs(6), self.f_xs, txt_col)
            mods = rec.get("mods", "")
            if mods:
                self._txt(surf, mods,
                          x, ry + row_h // 2 + self.vs(2), self.f_xs, GOLD)

            title = rec.get("beatmap_title", "Unknown")
            if len(title) > max_chars:
                title = title[:max_chars - 1] + "…"
            self._txt(surf, title,
                      x + self.vs(72), ry + self.vs(6), self.f_xs, txt_col)

            score = rec.get("score", 0)
            combo = rec.get("max_combo", 0)
            n300  = rec.get("n300", 0)
            n100  = rec.get("n100", 0)
            nmiss = rec.get("nmiss", 0)
            self._txt(surf,
                      f"{score:,}  ×{combo}  {n300}/{n100}/{nmiss}",
                      x + self.vs(72), ry + row_h // 2 + self.vs(2),
                      self.f_xs, DIM_TEXT)

            self._txt(surf, f"{acc_val:.2f}%",
                      x + col_w - self.vs(84), ry + self.vs(6), self.f_xs, acc_col)
            self._txt(surf, f"{rec.get('ur', 0):.1f}",
                      x + col_w - self.vs(34), ry + self.vs(6), self.f_xs, DIM_TEXT)

            if not has_file:
                self._txt(surf, "no file",
                          x + col_w - self.vs(84), ry + row_h // 2 + self.vs(2),
                          self.f_xs, (90, 70, 30))

        # Scrollbar
        if len(self._replays) > visible:
            sb_x = x + col_w - self.vs(6)
            sb_h = visible * row_h
            th   = max(self.vs(20), int(sb_h * visible / len(self._replays)))
            ty   = y0 + int((self._scroll / max(1, max_sc)) * (sb_h - th))
            pygame.draw.rect(surf, (28, 32, 50), (sb_x, y0, self.vs(4), sb_h))
            pygame.draw.rect(surf, (80, 90, 130),
                             (sb_x, ty, self.vs(4), th), border_radius=self.vs(2))
