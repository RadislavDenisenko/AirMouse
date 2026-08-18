"""The zoom lens: slow down and it blooms, flick and it gets out of the way.

Everything here drives LensModel — the pure half of lens.py — with
synthetic cursors at 60Hz. The Win32 shell is deliberately untested (it is
a thin applier of the model's numbers); what matters is that the model can
never show a lens at the wrong moment, never leaves the monitor, and never
dies on a junk config, because those are the failures a user would feel.
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _TESTS_DIR)

from lens import LensModel, LensWindow, lens_rects, smoothstep

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


MON = (0, 0, 2560, 1440)
DT = 1.0 / 60.0


def run(model, seconds, speed, start=(800.0, 700.0), t0=0.0, mon=MON,
        active=True, brake=0.0):
    """Drive the model with a constant-speed straight-line cursor.
    Returns (frames list, end position, end time)."""
    x, y = start
    frames = []
    t = t0
    for _ in range(int(round(seconds / DT))):
        t += DT
        x += speed * DT
        frames.append(model.update(x, y, t, mon, active=active, brake=brake))
    return frames, (x, y), t


def settle(model, pos, t0, seconds=1.5, **kw):
    """Hold the cursor still until the lens has fully bloomed."""
    return run(model, seconds, 0.0, start=pos, t0=t0, **kw)


# ============================ blooming and hiding ============================
m = LensModel()
frames, pos, t = settle(m, (800.0, 700.0), 0.0)
last = frames[-1]
check("a still cursor blooms the lens", last is not None)
check("...to full opacity", last is not None and last[0] == 255,
      f"alpha={last and last[0]}")
FULL_H = int(1440 * 0.22)
FULL_W = int(FULL_H * LensModel.ASPECT)
check("...at full size, wider than tall (labels are horizontal)",
      last is not None and last[1] == (FULL_W, FULL_H),
      f"wh={last and last[1]}")

first_visible = next((i for i, f in enumerate(frames) if f is not None), None)
check("the bloom is not instant", first_visible is not None
      and first_visible >= int(0.12 / DT),
      f"first visible frame {first_visible}")

frames, pos, t = run(m, 0.5, 2000.0, start=pos, t0=t)
gone_at = next((i for i, f in enumerate(frames) if f is None), None)
check("a flick hides the lens", frames[-1] is None)
check("...and hides it fast (<0.25s)", gone_at is not None
      and gone_at <= int(0.25 / DT), f"gone at frame {gone_at}")

# asymmetry: getting out of the way must be quicker than arriving
check("hiding is faster than blooming", gone_at is not None
      and first_visible is not None and gone_at < first_visible,
      f"hide={gone_at} bloom={first_visible} frames")

# =============================== the dwell gate ==============================
m = LensModel()
_, pos, t = run(m, 0.5, 2000.0)          # traveling
# a reversal instant: speed passes through ~0 for two frames
for _ in range(2):
    t += DT
    f = m.update(pos[0], pos[1], t, MON)
check("a two-frame pause mid-flick shows nothing", f is None)
_, pos2, t = run(m, 0.4, 2000.0, start=pos, t0=t)
check("...and the flick carries on hidden", m.u < 0.02, f"u={m.u:.3f}")

# ============================= the brake override ============================
m = LensModel()
frames, pos, t = run(m, 0.6, 900.0, brake=1.0)
check("a full brake squeeze blooms even while moving",
      frames[-1] is not None and frames[-1][0] == 255,
      f"alpha={frames[-1] and frames[-1][0]}")

m = LensModel()
frames, _, _ = run(m, 0.6, 900.0, brake=0.0)
check("...but the same motion unbraked shows nothing",
      all(f is None for f in frames))

# ============================== active gating ===============================
m = LensModel()
frames, _, _ = run(m, 1.5, 0.0, active=False)
check("an inactive hand never shows the lens (real mouse stays clean)",
      all(f is None for f in frames))

m = LensModel()
_, pos, t = settle(m, (800.0, 700.0), 0.0)
frames, _, _ = run(m, 0.5, 0.0, start=pos, t0=t, active=False)
gone_at = next((i for i, f in enumerate(frames) if f is None), None)
check("disengaging fades the lens out", frames[-1] is None)
check("...promptly", gone_at is not None and gone_at <= int(0.25 / DT),
      f"gone at frame {gone_at}")

m = LensModel(enabled=False)
frames, _, _ = run(m, 1.5, 0.0)
check("disabled means never, even at rest", all(f is None for f in frames))

# ============================ zoom stays constant ============================
m = LensModel()
_, pos, t = settle(m, (800.0, 700.0), 0.0)
z_still = m.zoom
_, pos, t = run(m, 0.2, 400.0, start=pos, t0=t)
check("speed changes presence, never magnification", m.zoom == z_still,
      f"zoom {z_still} -> {m.zoom}")

# ============================== geometry: rects ==============================
win, src = lens_rects(1280, 720, 400, 300, 2.5, MON)
check("the lens is centred on the cursor",
      win == (1080, 570, 1480, 870), f"win={win}")
check("the source is the lens divided by the zoom",
      src[2] - src[0] == 160 and src[3] - src[1] == 120
      and src[0] == 1280 - 80, f"src={src}")

win, src = lens_rects(10, 10, 400, 300, 2.5, MON)
check("a corner cursor parks the lens at the corner",
      win[0] == 0 and win[1] == 0, f"win={win}")
check("...and the source too — corner pixels stay visible",
      src[0] == 0 and src[1] == 0, f"src={src}")

win, src = lens_rects(2555, 1435, 400, 300, 2.5, MON)
check("the far corner clamps inside the monitor",
      win[2] <= 2560 and win[3] <= 1440, f"win={win}")

pmon = (-1440, -400, 0, 2160)   # portrait monitor left of primary
win, src = lens_rects(-700, 900, 400, 300, 2.5, pmon)
check("negative-origin monitors clamp correctly",
      pmon[0] <= win[0] and win[2] <= pmon[2]
      and pmon[0] <= src[0] and src[2] <= pmon[2],
      f"win={win} src={src}")

win, src = lens_rects(100, 100, 5000, 4000, 2.5, (0, 0, 800, 600))
check("a lens bigger than the monitor pins to its origin, no crash",
      win[0] == 0 and win[1] == 0, f"win={win}")

# the size the model asks for scales with the monitor, not the config alone
m = LensModel()
small = (0, 0, 1280, 720)
frames, _, _ = settle(m, (600.0, 300.0), 0.0, mon=small)
sh = int(720 * 0.22)
check("lens size follows the monitor's short side",
      frames[-1] is not None
      and frames[-1][1] == (int(sh * LensModel.ASPECT), sh),
      f"wh={frames[-1] and frames[-1][1]}")

# his real second monitor: the same 1440p panel rotated must give the SAME
# lens as the primary, not a 78%-bigger one keyed to the long side
m = LensModel()
portrait = (2560, -400, 4000, 2160)
frames, _, _ = settle(m, (3000.0, 900.0), 0.0, mon=portrait)
check("a rotated monitor feels identical to the primary",
      frames[-1] is not None and frames[-1][1] == (FULL_W, FULL_H),
      f"wh={frames[-1] and frames[-1][1]}")

# ============================ teleports (recenter) ===========================
m = LensModel()
frames, pos, t = settle(m, (800.0, 700.0), 0.0)
check("(setup) lens is up before the teleport", frames[-1] is not None)
t += DT
f = m.update(1280.0, 720.0, t, MON)   # shaka recenter: SetCursorPos jump
check("a teleporting cursor hides the lens THAT frame, no smear",
      f is None and m.u == 0.0, f"u={m.u:.3f}")
frames, _, _ = settle(m, (1280.0, 720.0), t)
first = next((i for i, fr in enumerate(frames) if fr is not None), None)
check("...and the new spot earns a fresh, dwell-gated bloom",
      first is not None and first >= int(0.12 / DT), f"first={first}")

# ===================== deactivation re-arms the dwell gate ===================
m = LensModel()
_, pos, t = settle(m, (800.0, 700.0), 0.0)
_, pos, t = run(m, 0.5, 0.0, start=pos, t0=t, active=False)
frames, _, _ = settle(m, pos, t)
first = next((i for i, fr in enumerate(frames) if fr is not None), None)
check("re-engaging after a break needs the calm dwell again",
      first is not None and first >= int(0.12 / DT), f"first={first}")

m = LensModel()
_, pos, t = settle(m, (800.0, 700.0), 0.0)
m.reset()
check("reset() forgets everything", m.u == 0.0 and m._last is None
      and m._calm_s == 0.0)

# ========================== live tuning + junk config ========================
m = LensModel()
m.apply_params({"zoom": 4.0, "size_frac": 0.30})
check("zoom and size retune live", m.zoom == 4.0 and m.size_frac == 0.30)

m.apply_params({"zoom": -3, "size_frac": 99, "aim_speed": "garbage",
                "travel_speed": None, "unknown_key": True})
check("junk values clamp or bounce instead of applying",
      m.zoom >= 1.0 and m.size_frac <= 0.50 and m.aim_speed == 180.0,
      f"zoom={m.zoom} frac={m.size_frac} aim={m.aim_speed}")

m.apply_params({"aim_speed": 500.0, "travel_speed": 400.0})
check("inverted thresholds degrade to a step, not a crash",
      m.travel_speed > m.aim_speed,
      f"aim={m.aim_speed} travel={m.travel_speed}")

m.apply_params("not a dict at all")
m.apply_params(None)
check("a malformed lens block is ignored, not fatal", m.aim_speed == 500.0)

check("smoothstep degrades to a step on an empty band",
      smoothstep(5, 5, 4) == 0.0 and smoothstep(5, 5, 6) == 1.0)

# =============================== the window API ==============================
# Construction must never raise and never open a window — the thread does
# that lazily on start(), which tests do NOT call.
lw = LensWindow({"enabled": True, "zoom": 3.0})
check("the window wrapper builds from config without a screen",
      lw.ok and lw.model.zoom == 3.0)
lw.set_state(active=True, brake=0.5)
check("state pokes are plain attributes", lw._active and lw._brake == 0.5)
lw.apply_params({"zoom": 2.0})
check("params reach the model", lw.model.zoom == 2.0)
lw.stop()   # never started; must be a no-op

# ============================ config plumbing ================================
from config_defaults import DEFAULT_CONFIG

blk = DEFAULT_CONFIG.get("lens")
check("the lens block ships in the defaults", isinstance(blk, dict))
defaults_model = LensModel()
check("the shipped defaults and the model defaults agree",
      blk is not None
      and defaults_model.zoom == blk["zoom"]
      and defaults_model.size_frac == blk["size_frac"]
      and defaults_model.aim_speed == blk["aim_speed"]
      and defaults_model.travel_speed == blk["travel_speed"]
      and defaults_model.enabled == blk["enabled"])

import settings_store

check("lens settings apply live", settings_store.is_live("lens.zoom"))

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASS")
