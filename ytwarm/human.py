"""Stochastic timing, mouse paths, scrolling and typing - the "not a script" layer.

Nothing here knows about YouTube. Everything takes a Playwright page and returns
plain numbers, which is what makes it testable on its own (see test_ytwarm.py).

Two rules that the whole module exists to keep:

- **No `time.sleep(2)` anywhere.** A fixed pause is the cheapest thing in the
  world to spot, because a human's pauses have a spread and a script's do not.
  Every wait here is a clamped normal draw through `nap()`.
- **Nothing lands on an exact centre.** Clicks aim at a random point inside the
  element's box and arrive along a bent path, because a straight line into the
  middle pixel of a button is not a hand.

The clamps on `gauss()` matter more than the mean: an unclamped normal draw
produces a negative delay a few times in a thousand, and the tail of a long one
is what parks a session for four minutes on a homepage.
"""
from __future__ import annotations

import math
import random
import time

# The cursor's last resting place. A page has no idea where the mouse is when it
# loads, so the first move of a session has to start from somewhere plausible
# rather than from 0,0 - the top-left corner is a place no real cursor sits.
_pos = [random.randint(240, 900), random.randint(180, 620)]

# Sizes real people actually run at. Only the Chrome WINDOW size now, and only
# for an off-screen or headless window - a visible one is maximised. The default
# Playwright 1280x720 is the most common bot size there is.
VIEWPORTS = [
    (1536, 864), (1440, 900), (1600, 900), (1366, 768), (1680, 1050), (1920, 1080),
]


def gauss(mean: float, spread: float | None = None,
          lo: float | None = None, hi: float | None = None) -> float:
    """A normal draw, clamped. Unclamped it eventually returns a negative delay."""
    spread = mean * 0.28 if spread is None else spread
    lo = mean * 0.35 if lo is None else lo
    hi = mean * 2.2 if hi is None else hi
    return max(lo, min(hi, random.gauss(mean, spread)))


def nap(mean: float, spread: float | None = None,
        lo: float | None = None, hi: float | None = None) -> float:
    t = gauss(mean, spread, lo, hi)
    time.sleep(t)
    return t


def chance(p: float) -> bool:
    return random.random() < max(0.0, min(1.0, p))


def pick(seq):
    return random.choice(list(seq))


def viewport() -> dict:
    w, h = pick(VIEWPORTS)
    return {"width": w, "height": h}


# ---------------------------------------------------------------- mouse


def bezier(p0, p3, steps: int) -> list:
    """A cubic Bezier from p0 to p3 with both control points pulled off the line.

    The offset is perpendicular to the travel and signed at random, so the path
    bows one way or the other instead of always bending the same direction -
    a consistent bow is as much of a tell as a straight line.
    """
    (x0, y0), (x3, y3) = p0, p3
    dx, dy = x3 - x0, y3 - y0
    dist = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / dist, dx / dist          # unit normal
    bow = dist * random.uniform(0.05, 0.22) * random.choice((1, -1))
    c1 = (x0 + dx * 0.3 + nx * bow, y0 + dy * 0.3 + ny * bow)
    c2 = (x0 + dx * 0.7 + nx * bow * random.uniform(0.4, 1.0),
          y0 + dy * 0.7 + ny * bow * random.uniform(0.4, 1.0))
    pts = []
    for i in range(1, max(2, steps) + 1):
        t = i / steps
        u = 1 - t
        x = (u ** 3 * x0 + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * x3)
        y = (u ** 3 * y0 + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * y3)
        pts.append((x, y))
    return pts


def move_mouse(page, x: float, y: float) -> None:
    steps = int(gauss(24, 7, 10, 48))
    for px, py in bezier(tuple(_pos), (x, y), steps):
        page.mouse.move(px, py)
        time.sleep(max(0.003, random.gauss(0.011, 0.004)))
    _pos[:] = [x, y]


def click(page, locator) -> None:
    """Move to a random point inside the element and click it there.

    Falls back to Playwright's own click when the element has no box - an
    off-screen or zero-size target is not worth a hand-rolled path.
    """
    try:
        locator.scroll_into_view_if_needed(timeout=4000)
        box = locator.bounding_box()
    except Exception:                                   # noqa: BLE001
        box = None
    if not box or box.get("width", 0) < 2 or box.get("height", 0) < 2:
        locator.click(timeout=8000)
        return
    x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
    y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    move_mouse(page, x, y)
    nap(0.19, 0.08, 0.05, 0.7)
    page.mouse.click(x, y)


def drift(page) -> None:
    """A small aimless cursor move. People do not hold the mouse perfectly still."""
    # No emulated viewport (see browser.py), so ask the page how big it really is.
    vp = page.viewport_size or page.evaluate(
        "() => ({width: innerWidth, height: innerHeight})")
    move_mouse(page,
               random.uniform(vp["width"] * 0.15, vp["width"] * 0.85),
               random.uniform(vp["height"] * 0.15, vp["height"] * 0.85))


# ---------------------------------------------------------------- scrolling


def scroll(page, distance: int | None = None, direction: int = 1) -> int:
    """Scroll roughly `distance` pixels in uneven steps. Returns pixels travelled.

    Three things make this not a loop of equal wheel ticks: the step size is a
    draw, ~8% of steps overshoot and correct back (the micro-stutter), and ~12%
    stop to read for a second or three.
    """
    target = int(distance if distance else gauss(900, 320, 280, 2200))
    done = 0
    while done < target:
        step = int(gauss(115, 45, 35, 260))
        page.mouse.wheel(0, step * direction)
        done += step
        nap(0.09, 0.05, 0.02, 0.4)
        if chance(0.08):
            page.mouse.wheel(0, -int(gauss(48, 20, 10, 95)) * direction)
            nap(0.28, 0.12, 0.06, 1.0)
        if chance(0.12):
            nap(1.2, 0.6, 0.3, 4.0)
    return done


def scroll_back(page, distance: int | None = None) -> int:
    return scroll(page, distance, direction=-1)


# ---------------------------------------------------------------- typing


def type_text(page, text: str, wpm: tuple = (210, 330)) -> None:
    """Type character by character with a per-key draw, a rare typo and a fix.

    `page.keyboard.type(text, delay=N)` is one fixed delay for every key, which
    is why it is not used. The typo branch is 2% per character: a typed session
    with no correction in it at all is a giveaway on a long query.
    """
    base = 60.0 / (random.uniform(*wpm) * 5)      # seconds per character
    for ch in text:
        if chance(0.02) and ch.isalpha():
            page.keyboard.type(random.choice("asdfghjkl"))
            nap(base * 3, base, base, base * 8)
            page.keyboard.press("Backspace")
            nap(base * 2, base, base, base * 6)
        page.keyboard.type(ch)
        time.sleep(gauss(base, base * 0.45, base * 0.3, base * 3.5))
        if ch == " " and chance(0.12):
            nap(0.32, 0.18, 0.08, 1.2)            # thinking mid-phrase
