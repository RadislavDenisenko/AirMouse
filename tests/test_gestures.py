"""Unit tests for the gesture state machine using synthetic hands."""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))

from gestures import GestureController, IDLE, ENGAGING, TRACKING, ARMED
from mouse_input import NullMouse

FRAME = (640, 480)
DT = 1.0 / 30.0


def synthetic_hand(px, py, open_palm=True):
    """21 fake landmarks for a hand whose palm center is (px, py)."""
    pts = [(0, 0)] * 21
    pts[0] = (px, py + 50)        # wrist
    pts[9] = (px, py - 50)        # middle mcp  -> palm center = (px, py)
    # thumb chain (tip splayed wide so the spread engage check passes)
    pts[1], pts[2], pts[3], pts[4] = (px - 30, py + 30), (px - 55, py), (px - 70, py - 10), (px - 85, py - 20)
    finger_x = {"index": px - 25, "middle": px, "ring": px + 25, "pinky": px + 45}
    bases = {"index": 5, "middle": 9, "ring": 13, "pinky": 17}
    for name, b in bases.items():
        x = finger_x[name]
        if open_palm:
            pts[b] = (x, py - 50)
            pts[b + 1] = (x, py - 100)   # pip
            pts[b + 2] = (x, py - 130)
            pts[b + 3] = (x, py - 160)   # tip: far from wrist -> extended
        else:  # curled fist
            pts[b] = (x, py - 50)
            pts[b + 1] = (x, py - 70)    # pip
            pts[b + 2] = (x, py - 40)
            pts[b + 3] = (x, py + 10)    # tip: close to wrist -> curled
    return pts


def hand(px, py, pinch_dist=60.0, tip="index"):
    """Synthetic hand; thumb tip placed exactly pinch_dist px from the given
    fingertip, approaching from the thumb's natural rest side (below-left).
    When actually pinching (dist < 45) the finger curls down to meet the
    thumb, like a real pinch. Hand size is 100 px, so ratio = pinch_dist/100.
    (Shared by the other suites — lives here because this module
    is import-safe: its tests only run under __main__.)"""
    import math
    pts = list(synthetic_hand(px, py))
    t_idx = 8 if tip == "index" else 12
    if pinch_dist < 45:
        pts[t_idx] = (pts[t_idx][0], py - 90)   # fingertip curls down
    tx, ty = pts[t_idx]
    rest = (px - 60, py - 40)                   # thumb's open rest position
    d = math.hypot(rest[0] - tx, rest[1] - ty)
    ux, uy = (rest[0] - tx) / d, (rest[1] - ty) / d
    pts[4] = (tx + ux * pinch_dist, ty + uy * pinch_dist)
    return pts


def peace_hand(px, py):
    """Peace sign: index+middle extended, ring+pinky curled, thumb at rest.
    Lives here, not in a suite, because this module is import-safe — its
    checks only run under __main__, so importing it can't sys.exit the caller."""
    pts = list(synthetic_hand(px, py))
    for b in (13, 17):                     # ring, pinky bases
        x = pts[b][0]
        pts[b + 1] = (x, py - 70)          # pip stays high
        pts[b + 2] = (x, py - 40)
        pts[b + 3] = (x, py + 10)          # tip curled to wrist
    return pts


def brake_hand(px, py, frac=1.0):
    """Precision-brake pose: index and thumb stay out, middle+ring+pinky
    curled `frac` of the way in. frac=0 is an open hand, frac=1 a full
    squeeze — so a test can sweep the curl continuously. Lives here for the
    same reason peace_hand does: this module is import-safe."""
    open_pts = synthetic_hand(px, py)
    pts = list(open_pts)
    for b in (9, 13, 17):                  # middle, ring, pinky bases
        x = pts[b][0]
        for k, shut in ((1, (x, py - 70)), (2, (x, py - 40)), (3, (x, py + 10))):
            ox, oy = open_pts[b + k]
            pts[b + k] = (ox + (shut[0] - ox) * frac,
                          oy + (shut[1] - oy) * frac)
    return pts


def brake_pinch(px, py, pinch_dist=20.0):
    """Brake pose with the thumb pinched to the index tip — squeezing to slow
    down and clicking at the same time, which is the point of the gesture."""
    import math
    pts = brake_hand(px, py)
    pts[8] = (pts[8][0], py - 90)               # index curls to meet the thumb
    tx, ty = pts[8]
    rest = (px - 60, py - 40)
    d = math.hypot(rest[0] - tx, rest[1] - ty)
    ux, uy = (rest[0] - tx) / d, (rest[1] - ty) / d
    pts[4] = (tx + ux * pinch_dist, ty + uy * pinch_dist)
    return pts


def clench(px, py, f, index_lag=0.0):
    """An open hand `f` of the way into a fist, with the index trailing the
    other fingers by `index_lag`.

    Real hands do not close all four fingers at once — the pinky leads and
    the index finishes last. That roll matters because a hand mid-clench
    briefly reads as "index out, middle in", which is also the precision
    brake's shape."""
    open_pts = synthetic_hand(px, py)
    shut_pts = synthetic_hand(px, py, open_palm=False)
    pts = list(open_pts)
    for name, b in (("index", 5), ("middle", 9), ("ring", 13), ("pinky", 17)):
        ff = f
        if name == "index":
            ff = max(0.0, min(1.0, (f - index_lag) / max(1e-6, 1.0 - index_lag)))
        for k in (1, 2, 3):
            ox, oy = open_pts[b + k]
            sx, sy = shut_pts[b + k]
            pts[b + k] = (ox + (sx - ox) * ff, oy + (sy - oy) * ff)
    return pts


def raw_controller(mouse, sensitivity=5.0):
    """State-machine-only controller: dead zone off, smoothing disabled,
    short engage hold so tests don't need 1.2s of frames."""
    return GestureController(mouse, sensitivity=sensitivity,
                             dead_zone_px=0.0, filter_min_cutoff=None,
                             engage_hold_s=0.25)


def total_moves(mouse):
    dx = sum(e[1] for e in mouse.events if e[0] == "move")
    dy = sum(e[2] for e in mouse.events if e[0] == "move")
    return dx, dy


def main():
    failures = []

    def check(name, cond, detail=""):
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {name} {detail}")
        if not cond:
            failures.append(name)

    t = 0.0

    def run(ctrl, frames):
        nonlocal t
        info = None
        for pts in frames:
            t += DT
            info = ctrl.update(pts, FRAME, t)
        return info

    # --- Test 1: no hand -> stays IDLE, no events ---------------------------
    mouse = NullMouse()
    ctrl = raw_controller(mouse)
    info = run(ctrl, [None] * 10)
    check("idle with no hand", info["state"] == IDLE and not mouse.events)

    # --- Test 2: open palm engages after ~0.25s, anchor locked --------------
    info = run(ctrl, [synthetic_hand(320, 240)] * 2)
    check("open palm starts ENGAGING", info["state"] == ENGAGING)
    info = run(ctrl, [synthetic_hand(320, 240)] * 8)   # ~0.27s more
    check("locks TRACKING with anchor", info["state"] == TRACKING
          and info["anchor"] == (320.0, 240.0), f"anchor={info['anchor']}")
    check("no motion on engage", not mouse.events)

    # --- Test 3: hand right 5px/frame x10 -> cursor right ~250px ------------
    frames = [synthetic_hand(320 + 5 * i, 240) for i in range(1, 11)]
    run(ctrl, frames)
    dx, dy = total_moves(mouse)
    check("move right", dx == 250 and dy == 0, f"dx={dx} dy={dy}")

    # --- Test 4: hand up 4px/frame x5 -> cursor up (negative dy) ------------
    mouse.events.clear()
    frames = [synthetic_hand(370, 240 - 4 * i) for i in range(1, 6)]
    run(ctrl, frames)
    dx, dy = total_moves(mouse)
    check("move up", dx == 0 and dy == -100, f"dx={dx} dy={dy}")

    # --- Test 5: hand disappears -> freeze, then IDLE, zero motion ----------
    mouse.events.clear()
    info = run(ctrl, [None] * 3)                       # within grace: frozen
    check("grace period holds TRACKING", info["state"] == TRACKING)
    info = run(ctrl, [None] * 6)                       # past 0.25s grace
    check("lost hand -> IDLE", info["state"] == IDLE and info["anchor"] is None)
    check("no motion during loss", not mouse.events)

    # --- Test 6: re-engage elsewhere -> new anchor, no jump -----------------
    info = run(ctrl, [synthetic_hand(100, 150)] * 10)
    check("re-engage new anchor", info["state"] == TRACKING
          and info["anchor"] == (100.0, 150.0), f"anchor={info['anchor']}")
    check("no jump on re-engage", not mouse.events, f"events={mouse.events[:5]}")

    # --- Test 7: fist ARMS navigation (no click), reopen disarms ------------
    info = run(ctrl, [synthetic_hand(100, 150, open_palm=False)] * 3)
    check("fist arms navigation, presses nothing", info["state"] == ARMED
          and info["fist_armed"] and not mouse.events,
          f"state={info['state']} events={mouse.events}")
    info = run(ctrl, [synthetic_hand(100, 150)] * 3)
    check("reopen disarms back to TRACKING", info["state"] == TRACKING
          and not info["fist_armed"] and not mouse.events,
          f"events={mouse.events}")
    mouse.events.clear()
    info = run(ctrl, [None] * 9)
    check("drop hand -> IDLE, no motion", info["state"] == IDLE
          and not mouse.events)

    # --- Test 8: sub-pixel residual accumulation ----------------------------
    mouse2 = NullMouse()
    ctrl2 = raw_controller(mouse2, sensitivity=1.0)
    run(ctrl2, [synthetic_hand(320, 240)] * 10)        # engage
    assert ctrl2.state == TRACKING
    frames = [synthetic_hand(320 + 0.4 * i, 240) for i in range(1, 11)]
    run(ctrl2, frames)
    dx, dy = total_moves(mouse2)
    ints_only = all(isinstance(e[1], int) for e in mouse2.events if e[0] == "move")
    check("sub-pixel accumulates", dx == 4 and ints_only, f"dx={dx}")

    print()
    print("ALL PASS" if not failures else f"FAILED: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
