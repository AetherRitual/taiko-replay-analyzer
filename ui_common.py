"""Shared UI utilities for viewer.py and profile_viewer.py."""
import pygame

# Font name priority: DejaVu (Linux), then common Windows/macOS faces, then default
FONT_PREF      = ["DejaVu Sans", "Segoe UI", "Arial", "Helvetica", ""]
FONT_MONO_PREF = ["DejaVu Sans Mono", "Consolas", "Courier New", ""]


def sysfont(name_pref: list, size: int, bold: bool = False) -> pygame.font.Font:
    """Return the first available font from name_pref, falling back to the pygame default."""
    for name in name_pref:
        try:
            f = pygame.font.SysFont(name, size, bold=bold)
            if f is not None:
                return f
        except Exception:
            continue
    return pygame.font.Font(None, size)
