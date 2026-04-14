"""Load and cache osu! skin assets for the Taiko replay viewer."""
from pathlib import Path
from functools import lru_cache
import pygame

DEFAULT_SKIN = Path(__file__).parent / "skin"

# Canonical taiko note colors (applied as BLEND_MULT tint to hitcircle)
DON_TINT = (255, 100, 100, 255)   # red
KAT_TINT = (80,  150, 255, 255)   # blue
DON_BIG_TINT = (255, 110, 110, 255)  # same red family as DON_TINT
KAT_BIG_TINT = (60,  210, 255, 255)


def _tint(surf: pygame.Surface, color: tuple) -> pygame.Surface:
    """Return a copy of surf multiplied by color (BLEND_RGBA_MULT)."""
    result = surf.copy()
    overlay = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    overlay.fill(color)
    result.blit(overlay, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return result


def _scale_to(surf: pygame.Surface, size: int) -> pygame.Surface:
    return pygame.transform.smoothscale(surf, (size, size))


def _scale_wh(surf: pygame.Surface, w: int, h: int) -> pygame.Surface:
    return pygame.transform.smoothscale(surf, (w, h))


class SkinLoader:
    def __init__(self, skin_path: Path = DEFAULT_SKIN):
        self.path = skin_path
        self._raw: dict[str, pygame.Surface | None] = {}
        # Scaled note cache: (kind, is_big, diameter) -> Surface
        self._notes: dict = {}
        self._drum: dict = {}   # size -> Surface
        self._hit_res: dict = {}  # (result_name, h) -> Surface
        self._roll: dict = {}     # (w, h) -> Surface
        self._bar_bg: dict = {}   # (w, h) -> Surface (right lane bg)
        self._bar_left: dict = {} # (w, h) -> Surface

    # ------------------------------------------------------------------
    # Raw asset loading
    # ------------------------------------------------------------------

    def _load_raw(self, name: str) -> pygame.Surface | None:
        if name in self._raw:
            return self._raw[name]
        # Try @2x first
        for candidate in [name.replace('.png', '@2x.png'), name]:
            p = self.path / candidate
            if p.exists():
                try:
                    surf = pygame.image.load(str(p)).convert_alpha()
                    # Discard 1×1 placeholder images
                    if surf.get_size() == (1, 1):
                        break
                    self._raw[name] = surf
                    return surf
                except Exception:
                    continue
        self._raw[name] = None
        return None

    # ------------------------------------------------------------------
    # Note surfaces
    # ------------------------------------------------------------------

    def note_surface(self, kind: str, is_big: bool, diameter: int) -> pygame.Surface | None:
        key = (kind, is_big, diameter)
        if key in self._notes:
            return self._notes[key]

        base_name = 'taikobigcircle.png' if is_big else 'taikohitcircle.png'
        over_name = 'taikobigcircleoverlay.png' if is_big else 'taikohitcircleoverlay.png'

        base = self._load_raw(base_name)
        over = self._load_raw(over_name)

        if base is None:
            self._notes[key] = None
            return None

        if kind == 'don':
            tint = DON_BIG_TINT if is_big else DON_TINT
        else:
            tint = KAT_BIG_TINT if is_big else KAT_TINT

        scaled = _scale_to(base, diameter)
        result = _tint(scaled, tint)

        if over:
            scaled_over = _scale_to(over, diameter)
            result.blit(scaled_over, (0, 0))

        self._notes[key] = result
        return result

    # ------------------------------------------------------------------
    # Hit zone drum
    # ------------------------------------------------------------------

    def drum_surface(self, drum_h: int) -> pygame.Surface | None:
        """Return composited drum half-image for left-edge rendering.

        drum_h: pixel height to render the drum at (typically ~3× lane height).
        The images are right-half-circles; the drum center is off the left edge of
        the screen.  Blit the returned surface at (0, cy - drum_h // 2).
        """
        if drum_h in self._drum:
            return self._drum[drum_h]

        outer = self._load_raw('taiko-drum-outer.png')
        inner = self._load_raw('taiko-drum-inner.png')

        if outer is None and inner is None:
            self._drum[drum_h] = None
            return None

        # Outer defines the canvas width (maintains aspect ratio relative to its height)
        if outer:
            ow, oh = outer.get_size()
            out_w = max(1, int(ow * drum_h / oh))
            scaled_outer = _scale_wh(outer, out_w, drum_h)
            surf_w = out_w
        else:
            iw, ih = inner.get_size()
            surf_w = max(1, int(iw * drum_h / ih))

        result = pygame.Surface((surf_w, drum_h), pygame.SRCALPHA)

        if outer:
            result.blit(scaled_outer, (0, 0))

        if inner:
            iw, ih = inner.get_size()
            # Scale inner relative to outer's natural height (preserves their ratio)
            ref_oh = outer.get_size()[1] if outer else ih
            in_h = max(1, int(drum_h * ih / ref_oh))
            in_w = max(1, int(iw * in_h / ih))
            scaled_inner = _scale_wh(inner, in_w, in_h)
            # Centre inner vertically within the surface
            iy = (drum_h - in_h) // 2
            result.blit(scaled_inner, (0, iy))

        self._drum[drum_h] = result
        return result

    # ------------------------------------------------------------------
    # Hit result judgments
    # ------------------------------------------------------------------

    def hit_result_surface(self, result_name: str, height: int) -> pygame.Surface | None:
        key = (result_name, height)
        if key in self._hit_res:
            return self._hit_res[key]

        names = {'300': 'taiko-hit300.png', '100': 'taiko-hit100.png',
                 'miss': 'taiko-hit0.png',  '100k': 'taiko-hit100k.png'}
        img = self._load_raw(names.get(result_name, ''))
        if img is None:
            self._hit_res[key] = None
            return None

        iw, ih = img.get_size()
        w = int(iw * height / ih)
        scaled = _scale_wh(img, w, height)
        self._hit_res[key] = scaled
        return scaled

    # ------------------------------------------------------------------
    # Drum roll bar
    # ------------------------------------------------------------------

    def roll_surface(self, width: int, height: int) -> pygame.Surface | None:
        key = (width, height)
        if key in self._roll:
            return self._roll[key]

        mid = self._load_raw('taiko-roll-middle.png')
        end = self._load_raw('taiko-roll-end.png')

        if mid is None:
            self._roll[key] = None
            return None

        result = pygame.Surface((width, height), pygame.SRCALPHA)
        # The middle image is 1px wide — tile it
        scaled_mid = _scale_wh(mid, 1, height)
        for x in range(width - (end.get_size()[0] if end else 0)):
            result.blit(scaled_mid, (x, 0))

        if end:
            ew, eh = end.get_size()
            scaled_end = _scale_wh(end, int(ew * height / eh), height)
            result.blit(scaled_end, (width - scaled_end.get_width(), 0))

        self._roll[key] = result
        return result

    # ------------------------------------------------------------------
    # Lane backgrounds
    # ------------------------------------------------------------------

    def bar_right(self, w: int, h: int) -> pygame.Surface | None:
        key = (w, h)
        if key in self._bar_bg:
            return self._bar_bg[key]
        img = self._load_raw('taiko-bar-right.png')
        if img is None:
            self._bar_bg[key] = None
            return None
        # Tile horizontally to fill width
        result = pygame.Surface((w, h))
        iw, ih = img.get_size()
        scaled_tile = _scale_wh(img, iw, h)
        tw = scaled_tile.get_width()
        for x in range(0, w, tw):
            result.blit(scaled_tile, (x, 0))
        self._bar_bg[key] = result
        return result

    def bar_left(self, w: int, h: int) -> pygame.Surface | None:
        key = (w, h)
        if key in self._bar_left:
            return self._bar_left[key]
        img = self._load_raw('taiko-bar-left.png')
        if img is None:
            self._bar_left[key] = None
            return None
        scaled = _scale_wh(img, w, h)
        self._bar_left[key] = scaled
        return scaled

    def barline_surface(self, h: int) -> pygame.Surface | None:
        img = self._load_raw('taiko-barline.png')
        if img is None:
            return None
        iw, ih = img.get_size()
        return _scale_wh(img, max(1, int(iw * h / ih)), h)

    # ------------------------------------------------------------------
    # Invalidate cached scaled assets on resize
    # ------------------------------------------------------------------

    def invalidate_scaled(self):
        self._notes.clear()
        self._drum.clear()
        self._hit_res.clear()
        self._roll.clear()
        self._bar_bg.clear()
        self._bar_left.clear()
