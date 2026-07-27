"""Tests for the attention gate and its gating of the gesture controller."""
import math
import os
import sys

import numpy as np

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from attention import AttentionGate
from face_tracker import head_pose_deg, eye_blink_score
from gestures import GestureController, IDLE, TRACKING, PINCHED
from mouse_input import NullMouse
from test_gestures import synthetic_hand, hand, FRAME, DT


class Sig:
    """Stand-in for FaceSignals."""
    def __init__(self, present=True, pose=(0.0, 0.0, 0.0), blink=0.0):
        self.present = present
        self.pose = pose
        self.blink = blink
        self.ts_ms = 0


class Cat:
    def __init__(self, name, score):
        self.category_name, self.score = name, score


def _ry(theta_deg):
    """4x4 rotation about the vertical (yaw) axis."""
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    M = np.eye(4)
    M[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    return M


def _rx(phi_deg):
    """4x4 rotation about the horizontal (pitch) axis."""
    p = math.radians(phi_deg)
    c, s = math.cos(p), math.sin(p)
    M = np.eye(4)
    M[:3, :3] = [[1, 0, 0], [0, c, -s], [0, s, c]]
    return M


def main():
    failures = []

    def check(name, cond, detail=""):
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
        if not cond:
            failures.append(name)

    # --- head_pose_deg recovers pure yaw/pitch ------------------------------
    y, p, r = head_pose_deg(np.eye(4))
    check("identity pose ~ (0,0,0)", abs(y) < 1e-6 and abs(p) < 1e-6 and abs(r) < 1e-6,
          f"({y:.2f},{p:.2f},{r:.2f})")
    y30, _, _ = head_pose_deg(_ry(30))
    check("30 deg yaw recovered", abs(abs(y30) - 30) < 0.5, f"yaw={y30:.2f}")
    _, p20, _ = head_pose_deg(_rx(20))
    check("20 deg pitch recovered", abs(abs(p20) - 20) < 0.5, f"pitch={p20:.2f}")

    # --- blendshape blink parsing -------------------------------------------
    bs = [Cat("eyeBlinkLeft", 0.9), Cat("eyeBlinkRight", 0.7), Cat("jawOpen", 0.1)]
    check("blink score averages both eyes", abs(eye_blink_score(bs) - 0.8) < 1e-6)
    check("blink None when absent", eye_blink_score([Cat("jawOpen", 0.2)]) is None)

    # --- gate: starts off, turns on only when facing ------------------------
    g = AttentionGate(yaw_thresh_deg=22, pitch_thresh_deg=18,
                      on_grace_s=0.1, off_grace_s=0.1)
    check("gate starts OFF", g.attending is False)
    t = 0.0
    for _ in range(10):  # facing straight ahead
        t += DT
        g.update(Sig(pose=(2.0, -3.0, 0.0)), t)
    check("facing -> attending", g.attending is True, g.status())

    # turn head 40 deg right -> should drop after off_grace
    for _ in range(10):
        t += DT
        g.update(Sig(pose=(40.0, 0.0, 0.0)), t)
    check("looking away -> not attending", g.attending is False, g.status())
    check("status reports LOOKING AWAY", g.status() == "LOOKING AWAY")

    # face disappears entirely
    for _ in range(10):
        t += DT
        g.update(Sig(present=False, pose=None, blink=None), t)
    check("no face -> not attending", g.attending is False and g.status() == "NO FACE")

    # --- debounce: a single stray away-frame doesn't flip control -----------
    g2 = AttentionGate(on_grace_s=0.1, off_grace_s=0.1)
    t = 0.0
    for _ in range(10):
        t += DT
        g2.update(Sig(pose=(0.0, 0.0, 0.0)), t)
    was_on = g2.attending
    t += DT
    g2.update(Sig(pose=(50.0, 0.0, 0.0)), t)   # one bad frame (~33ms < grace)
    check("single away frame ignored", was_on and g2.attending is True)

    # --- neutral calibration shifts the window ------------------------------
    g3 = AttentionGate(yaw_thresh_deg=15, on_grace_s=0.0, off_grace_s=0.0)
    g3.set_neutral(30.0, 0.0)   # user sits with head turned 30 deg
    t = 0.0
    for _ in range(6):
        t += DT
        g3.update(Sig(pose=(30.0, 0.0, 0.0)), t)   # at their neutral
    check("calibrated neutral counts as facing", g3.attending is True)
    for _ in range(8):  # a few extra frames for the EMA to swing across
        t += DT
        g3.update(Sig(pose=(0.0, 0.0, 0.0)), t)    # straight ahead = away for them
    check("far from neutral = away", g3.attending is False)

    # --- eyes-closed gating (opt-in) ----------------------------------------
    ge = AttentionGate(require_eyes_open=True, eyes_closed_s=0.3,
                       blink_thresh=0.5, on_grace_s=0.0, off_grace_s=0.0)
    t = 0.0
    for _ in range(4):
        t += DT
        ge.update(Sig(pose=(0.0, 0.0, 0.0), blink=0.0), t)
    check("eyes open -> attending", ge.attending is True)
    # blink for < eyes_closed_s stays attending
    t += DT
    ge.update(Sig(pose=(0.0, 0.0, 0.0), blink=0.9), t)
    check("brief blink stays attending", ge.attending is True)
    for _ in range(15):  # sustained closure past 0.3s
        t += DT
        ge.update(Sig(pose=(0.0, 0.0, 0.0), blink=0.9), t)
    check("sustained closed eyes -> not attending", ge.attending is False)

    # --- controller gating: no engage while not attending -------------------
    def raw(mouse, drop=0.2):
        return GestureController(mouse, dead_zone_px=0.0, filter_min_cutoff=None,
                                 attention_drop_s=drop, engage_hold_s=0.25)

    t = 0.0

    def run(ctrl, frames, attending):
        nonlocal t
        info = None
        for pts in frames:
            t += DT
            info = ctrl.update(pts, FRAME, t, attending=attending)
        return info

    mouse = NullMouse()
    ctrl = raw(mouse)
    info = run(ctrl, [synthetic_hand(320, 240)] * 12, attending=False)
    check("no engage while looking away", info["state"] == IDLE and not mouse.events)

    # now look back -> engages normally
    info = run(ctrl, [synthetic_hand(320, 240)] * 12, attending=True)
    check("engages once attending", info["state"] == TRACKING)

    # move while attending -> cursor moves
    mouse.events.clear()
    run(ctrl, [synthetic_hand(320 + 5 * i, 240) for i in range(1, 6)], attending=True)
    moved_looking = sum(e[1] for e in mouse.events if e[0] == "move")
    check("moves while looking", moved_looking > 0, f"dx={moved_looking}")

    # --- suspend: motion swallowed while looking away, anchor kept ----------
    mouse.events.clear()
    last_x = 320 + 25
    info = run(ctrl, [synthetic_hand(last_x + 5 * i, 240) for i in range(1, 5)],
               attending=False)
    check("no motion while suspended",
          not any(e[0] == "move" for e in mouse.events), f"events={mouse.events}")
    check("still TRACKING within drop window (anchor kept)",
          info["state"] == TRACKING and info["suspended"] is True)

    # glance back within the window -> resumes, first frame no jump
    mouse.events.clear()
    info = run(ctrl, [synthetic_hand(last_x + 20, 240)] * 1, attending=True)
    check("resume: no catch-up jump on first frame",
          not any(e[0] == "move" for e in mouse.events))
    info = run(ctrl, [synthetic_hand(last_x + 20 + 5 * i, 240) for i in range(1, 4)],
               attending=True)
    check("resume: moves again", any(e[0] == "move" for e in mouse.events))

    # --- prolonged look-away fully disengages -------------------------------
    info = run(ctrl, [synthetic_hand(400, 240)] * 12, attending=False)  # >0.2s
    check("prolonged away -> IDLE", info["state"] == IDLE and info["anchor"] is None)

    # --- a held drag survives a brief glance, releases only on full disengage
    # (pinch is the drag gesture since v3.2 — the fist arms navigation now)
    mouse2 = NullMouse()
    ctrl2 = raw(mouse2, drop=0.2)
    run(ctrl2, [synthetic_hand(300, 240)] * 12, attending=True)          # engage
    run(ctrl2, [hand(300, 240, 20)] * 4, attending=True)                 # pinch down
    check("pinch grabbed (left down)", ("down",) in mouse2.events,
          f"events={mouse2.events}")
    mouse2.events.clear()
    info = run(ctrl2, [hand(300, 240, 20)] * 3, attending=False)         # ~0.1s
    check("brief glance keeps the drag held", ("up",) not in mouse2.events
          and info["suspended"] is True)
    info = run(ctrl2, [hand(300, 240, 20)] * 3, attending=True)          # look back
    check("drag resumes on look-back (still held)",
          ("up",) not in mouse2.events and ("down",) not in mouse2.events
          and info["state"] == PINCHED, f"state={info['state']}")
    mouse2.events.clear()
    info = run(ctrl2, [hand(300, 240, 20)] * 12, attending=False)        # >0.2s
    check("sustained look-away releases drag + goes IDLE",
          ("up",) in mouse2.events and info["state"] == IDLE)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
