"""Shared UI kit for the AirMouse settings app (see DESIGN.md).

Hand-drawn animated Tk Canvas widgets plus the text system, kept in one
importable module so every screen shares the same look, motion and markup —
and so the pure logic (markup parsing, wrap layout, font resolution) stays
unit-testable without opening a window.

Type system — deliberately not the usual UI faces:
  DISPLAY = Bahnschrift SemiBold (DIN-style, engineered, very legible) for
            titles, highlighted phrases and value bubbles.
  BODY    = Candara (warm humanist) for captions and labels.

Highlighted phrases: caption text uses *asterisk markup* to mark the few
words that carry the meaning. Those render in DISPLAY, lit by a soft accent
glow, so a setting can be grasped at a glance instead of read.
"""

import math
import time
import tkinter as tk
import tkinter.font as tkfont

# ---------------------------------------------------------------- tokens ---
BG = "#211e1b"
PANEL = "#282420"
CARD = "#2f2a25"
CARD_HOVER = "#383129"
LINE = "#453c33"
TEXT = "#ebe4da"
DIM = "#9d938a"
OK = "#96ab7c"        # muted sage — saved, healthy
WARN = "#c9a35c"      # muted ochre — needs a restart, or a failed write
DANGER = "#b96754"    # muted clay — destructive only

# (name, accent, accent-soft). Brick is the default; the header
# lets you switch between them.
ACCENTS = (("amber", "#e0823d", "#8a6a4d"),
           ("brick", "#c25e4a", "#7d5347"),
           ("teal", "#4f9488", "#476b64"))
DEFAULT_ACCENT = 1

DISPLAY_STACK = ("Bahnschrift SemiBold", "Bahnschrift", "Sitka Heading Semibold",
                 "Franklin Gothic Medium", "Trebuchet MS")
BODY_STACK = ("Candara", "Corbel", "Constantia", "Trebuchet MS")

TICK_MS = 15

_FAMILIES = frozenset()
FONTS = {}


# ------------------------------------------------------------ pure logic ---
def resolve_family(stack, families):
    """First family in `stack` that actually exists, else a safe default."""
    for fam in stack:
        if fam in families:
            return fam
    return "TkDefaultFont"


def parse_markup(s):
    """'Buttons *grab your cursor* — always push through'
    -> [('Buttons ', False), ('grab your cursor', True), (' — ...', False)]

    Odd/unclosed markers degrade to plain text rather than raising."""
    parts = s.split("*")
    runs = []
    for i, part in enumerate(parts):
        if part:
            runs.append((part, i % 2 == 1))
    return runs


def layout_runs(runs, width, measure, space_w, line_h):
    """Greedy word-wrap of markup runs into positioned words.

    measure(text, is_chip) -> pixel width. Returns
    (lines, total_height) where each line is a list of
    (x, text, is_chip, w) — geometry only, so this is testable headlessly."""
    lines, cur, x = [], [], 0.0
    for text, is_chip in runs:
        for word in text.split(" "):
            if not word:
                continue
            w = measure(word, is_chip)
            gap = space_w if cur else 0.0
            if cur and x + gap + w > width:
                lines.append(cur)
                cur, x = [], 0.0
                gap = 0.0
            cur.append((x + gap, word, is_chip, w))
            x += gap + w
    if cur:
        lines.append(cur)
    return lines, len(lines) * line_h


def hex_lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    av = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    bv = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(av[i] + (bv[i] - av[i]) * t)
                                   for i in range(3))


def ease_out(p):
    return 1.0 - (1.0 - p) ** 3


def ease_in(p):
    return p * p * p


def ease_in_out(p):
    return p * p * (3.0 - 2.0 * p)


def ease_out_back(p, c1=1.2):
    c3 = c1 + 1.0
    return 1.0 + c3 * (p - 1.0) ** 3 + c1 * (p - 1.0) ** 2


def round_pts(x1, y1, x2, y2, r):
    """Point list for a smooth rounded rect (matte: flat fill, no gradient)."""
    r = min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2)
    return (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)


# ------------------------------------------------------------------ glow ---
# Real glow comes from PIL: draw a shape, Gaussian-blur it, and keep it as
# RGBA so Tk composites it over whatever is beneath. Soft falloff, no hard
# edge — the opposite of the stacked-plate "border" look.
#
# CRITICAL: these MUST stay RGBA. An earlier version pre-composited the glow
# over the assumed background colour, which made every glow an opaque
# rectangle that painted over neighbouring words. Never bake a background
# into a glow.
_GLOW_CACHE = {}


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def glow_frames(w, h, rect, radius, color, blur=10.0, levels=10,
                peak=1.0, tag="glow"):
    """List of `levels+1` RGBA PhotoImages: the shape glowing at 0..peak."""
    key = (tag, w, h, rect, radius, color, blur, levels, peak)
    hit = _GLOW_CACHE.get(key)
    if hit is not None:
        return hit
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(rect, radius=radius, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    fgc = _rgb(color)
    frames = []
    for i in range(levels + 1):
        a = peak * i / levels
        img = Image.new("RGBA", (w, h), fgc + (0,))
        img.putalpha(mask.point(lambda v, a=a: int(v * a)))
        frames.append(ImageTk.PhotoImage(img))
    _GLOW_CACHE[key] = frames
    return frames


def glow_photo(w, h, rect, radius, color, blur=6.0, alpha=0.5, tag="one"):
    """A single static soft glow (used behind highlighted phrases)."""
    return glow_frames(w, h, rect, radius, color, blur, 1, alpha, tag)[-1]


def surface_photo(w, h, fill, radius=14, blur=9.0, shadow=0.55, offset=4):
    """A soft floating surface: blurred drop-shadow under a barely-there
    fill. RGBA, no outline — the edge dissolves, so cards stop reading as
    blocks and nothing gets painted over."""
    key = ("surface", w, h, fill, radius, blur, shadow, offset)
    hit = _GLOW_CACHE.get(key)
    if hit is not None:
        return hit
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    pad = int(blur * 2)
    W, H = w + pad * 2, h + pad * 2
    sh = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sh).rounded_rectangle(
        (pad, pad + offset, pad + w, pad + h + offset), radius=radius,
        fill=int(255 * shadow))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    shadow_layer.putalpha(sh)
    img = Image.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 0)),
                                shadow_layer)
    top = Image.new("L", (W, H), 0)
    ImageDraw.Draw(top).rounded_rectangle((pad, pad, pad + w, pad + h),
                                          radius=radius, fill=255)
    top = top.filter(ImageFilter.GaussianBlur(0.8))   # kill the hard edge
    fill_layer = Image.new("RGBA", (W, H), _rgb(fill) + (255,))
    fill_layer.putalpha(top)
    img = Image.alpha_composite(img, fill_layer)
    photo = ImageTk.PhotoImage(img)
    _GLOW_CACHE[key] = (photo, pad)
    return photo, pad


def magnet_path(cx, cy, ang, r=7.0, leg=5.0, seg=9):
    """Horseshoe magnet outline (U opening left), rotated `ang` radians.
    Returns (path_points, north_tip, south_tip) in canvas coords."""
    local = [(-leg, -r), (0.0, -r)]
    for i in range(1, seg):
        a = -math.pi / 2 + math.pi * (i / seg)
        local.append((r * math.cos(a), r * math.sin(a)))
    local += [(0.0, r), (-leg, r)]
    ca, sa = math.cos(ang), math.sin(ang)

    def xf(p):
        return (cx + p[0] * ca - p[1] * sa, cy + p[0] * sa + p[1] * ca)

    path = [c for p in local for c in xf(p)]
    north = xf((-leg, -r)) + xf((-leg - 3.0, -r))
    south = xf((-leg, r)) + xf((-leg - 3.0, r))
    return path, north, south


# ------------------------------------------------------------ init/fonts ---
def init_fonts(root):
    """Resolve the type system against installed families. Call once after
    the Tk root exists; safe to call again.

    Mutates FONTS in place rather than rebinding it, so modules that did
    `from settings_ui import FONTS` see the resolved fonts too."""
    global _FAMILIES
    _FAMILIES = frozenset(tkfont.families(root))
    disp = resolve_family(DISPLAY_STACK, _FAMILIES)
    body = resolve_family(BODY_STACK, _FAMILIES)
    FONTS.clear()
    FONTS.update({
        "display_name": disp,
        "body_name": body,
        "app": (disp, 16),
        "section": (disp, 14),
        "card": (disp, 11),
        "chip": (disp, 10),
        "bubble": (disp, 9),
        "body": (body, 10),
        "nav": (body, 11),
        "small": (body, 9),
    })
    return FONTS


class Animator:
    """Interruptible tween engine on root.after. One job per key; retargeting
    mid-flight cancels and restarts, so mashing a control stays smooth."""

    def __init__(self, root):
        self.root = root
        self._jobs = {}

    def cancel(self, key):
        job = self._jobs.pop(key, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

    def run(self, key, frm, to, dur_ms, step, ease=ease_out, done=None):
        self.cancel(key)
        t0 = time.perf_counter()

        def tick():
            p = min(1.0, (time.perf_counter() - t0) * 1000.0 / max(1, dur_ms))
            step(frm + (to - frm) * ease(p))
            if p < 1.0:
                self._jobs[key] = self.root.after(TICK_MS, tick)
            else:
                self._jobs.pop(key, None)
                if done:
                    done()
        tick()


# --------------------------------------------------------- rich captions ---
class RichCaption:
    """Draws markup text onto an existing canvas: body words in DIM, and
    *marked* phrases bold in DISPLAY, lit by a soft accent GLOW behind the
    words — a bloom that fades out, not a highlighted rectangle."""

    def __init__(self, canvas, x, y, width, markup, accent, bg=CARD):
        self.cv = canvas
        self.x, self.y, self.width = x, y, width
        self.runs = parse_markup(markup)
        self.accent = accent
        self.bg = bg
        self._tag = f"rich{id(self)}"
        self._photos = []          # keep PhotoImage refs alive
        self.height = 0
        self.draw()

    def draw(self):
        cv, tag = self.cv, self._tag
        cv.delete(tag)
        body_f = tkfont.Font(font=FONTS["body"])
        chip_f = tkfont.Font(font=FONTS["chip"])
        line_h = max(body_f.metrics("linespace"), chip_f.metrics("linespace")) + 5

        def measure(word, is_chip):
            return (chip_f if is_chip else body_f).measure(word)

        # Word gap carries the glow's breathing room, so a phrase's bloom
        # doesn't wash into the word right after it.
        pad_x = 5
        lines, self.height = layout_runs(self.runs, self.width, measure,
                                        body_f.measure(" ") + pad_x, line_h)
        self._photos = []
        # PASS 1 — every glow, for every line, before any text is drawn. A
        # glow box is taller than its line, so drawing per-line would let a
        # later line's bloom land on top of the line above it.
        for li, line in enumerate(lines):
            ly = self.y + li * line_h
            span = None
            for (wx, _word, is_chip, ww) in line + [(0, "", False, 0)]:
                if is_chip:
                    span = (wx, wx + ww) if span is None else (span[0], wx + ww)
                elif span is not None:
                    gw = int(span[1] - span[0]) + 30
                    gh = line_h + 16
                    photo = glow_photo(gw, gh, (15, 10, gw - 15, gh - 10), 8,
                                       self.accent, blur=6.5, alpha=0.40,
                                       tag="chip")
                    self._photos.append(photo)
                    cv.create_image(self.x + span[0] - 15, ly - 10,
                                    anchor="nw", image=photo, tags=(tag, "glow"))
                    span = None
        # PASS 2 — all the words on top
        for li, line in enumerate(lines):
            ly = self.y + li * line_h
            for (wx, word, is_chip, _ww) in line:
                cv.create_text(self.x + wx, ly, anchor="nw", text=word,
                               font=FONTS["chip"] if is_chip else FONTS["body"],
                               fill=self.accent if is_chip else DIM, tags=tag)
        return self.height

    def retint(self, accent, bg):
        self.accent, self.bg = accent, bg
        self.draw()


# ---------------------------------------------------------------- widgets ---
class MagnetToggle(tk.Canvas):
    """The signature control: a real little horseshoe magnet swings in from
    the right, snaps onto the switch with a spark, and the knob is FLUNG
    across to meet it — the switch lights up and holds a soft glow. Pull the
    magnet away and the knob flicks back off.

    Sequenced in stages (each well under 250 ms) so it reads as physics
    rather than a slideshow. Fully interruptible: clicking mid-swing
    retargets via a sequence token."""

    # Sized so the bloom has room to fall off inside the widget, and so the
    # whole thing fits INSIDE its card — the canvas bg matches the card fill,
    # so the widget's own rectangle is invisible (no "box" around the switch).
    W, H = 170, 64
    PX1, PY1, PX2, PY2 = 24, 20, 72, 44    # the pill
    KR = 9
    MR, MLEG = 9.0, 7.0                    # magnet radius / leg length
    PARK = (146.0, 20.0, -0.85)            # magnet: x, y, angle (radians)
    HOLD = (84.0, 32.0, 0.0)               # poles biting the pill's right edge
    GLOW_LEVELS = 14

    def __init__(self, parent, anim, accent, on=False, command=None, bg=CARD):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.anim = anim
        self.accent = accent
        self._bg = bg
        self.on = on
        self.command = command
        self._pos = 1.0 if on else 0.0     # knob 0..1
        self._glow = 1.0 if on else 0.0
        self._swing = 1.0 if on else 0.0   # magnet park->hold
        self._spark = 0.0
        self._seq = 0
        self._glow_frames = None           # PhotoImage refs must stay alive
        self._cache_key = None
        self._paint()
        self.bind("<Button-1>", lambda _e: self.flip())

    def _frames(self):
        """Pre-baked bloom frames for the current accent/background."""
        if self._cache_key != self.accent:
            self._glow_frames = glow_frames(
                self.W, self.H,
                (self.PX1, self.PY1, self.PX2, self.PY2),
                (self.PY2 - self.PY1) / 2, self.accent,
                blur=13.0, levels=self.GLOW_LEVELS, peak=0.92, tag="magnet")
            self._cache_key = self.accent
        return self._glow_frames

    # -- painting --
    def _paint(self):
        cv = self
        cv.delete("all")
        g = max(0.0, min(1.35, self._glow))
        # real Gaussian bloom behind the switch — soft falloff, no edge
        frames = self._frames()
        idx = int(round(min(1.0, g / 1.35) * self.GLOW_LEVELS))
        cv.create_image(0, 0, anchor="nw", image=frames[idx])
        track = hex_lerp(LINE, self.accent, min(1.0, self._pos))
        cv.create_polygon(round_pts(self.PX1, self.PY1, self.PX2, self.PY2,
                                    (self.PY2 - self.PY1) / 2),
                          smooth=True, fill=track, outline="")
        # knob
        kx = self.PX1 + 12 + (self.PX2 - self.PX1 - 24) * self._pos
        ky = (self.PY1 + self.PY2) / 2
        cv.create_oval(kx - self.KR, ky - self.KR, kx + self.KR, ky + self.KR,
                       fill=hex_lerp(DIM, BG, min(1.0, self._pos)), outline="")
        # spark burst where the magnet bites the switch
        if self._spark > 0.02:
            s = self._spark
            fade = hex_lerp(self._bg, TEXT, s)
            cx, cy = self.PX2 + 2, ky
            for k in range(6):
                a = -math.pi / 2 + k * (math.pi / 5)
                L = 5 + 8 * (1 - abs(2 * s - 1))
                cv.create_line(cx + 3 * math.cos(a), cy + 3 * math.sin(a),
                               cx + L * math.cos(a), cy + L * math.sin(a),
                               fill=fade, width=2, capstyle="round")
        # the magnet itself
        s = self._swing
        px, py, pa = self.PARK
        hx, hy, ha = self.HOLD
        mx = px + (hx - px) * s
        my = py + (hy - py) * s - 7.0 * math.sin(math.pi * s)   # swoops in
        ma = pa + (ha - pa) * s
        path, north, south = magnet_path(mx, my, ma, r=self.MR, leg=self.MLEG)
        # brushed-metal horseshoe: a dark under-stroke gives the body weight,
        # the lighter stroke on top reads as a highlight
        cv.create_line(*path, smooth=True, width=8, capstyle="round",
                       fill=hex_lerp(self._bg, DIM, 0.55))
        cv.create_line(*path, smooth=True, width=5, capstyle="round",
                       fill=hex_lerp(DIM, TEXT, 0.35 + 0.35 * s))
        # painted pole faces: accent north, pale south
        cv.create_line(*north, width=8, capstyle="round", fill=self.accent)
        cv.create_line(*south, width=8, capstyle="round",
                       fill=hex_lerp(DIM, TEXT, 0.55))

    def _set(self, attr):
        def setter(v):
            setattr(self, attr, v)
            self._paint()
        return setter

    # -- the sequence --
    def flip(self):
        self.set(not self.on)
        if self.command:
            self.command(self.on)

    def set(self, on, animate=True):
        self.on = on
        self._seq += 1
        token = self._seq
        if not animate:
            self._pos = self._glow = self._swing = 1.0 if on else 0.0
            self._spark = 0.0
            self._paint()
            return
        k = id(self)
        if on:
            # 1. the magnet accelerates in (attraction grows as it nears)
            def contact():
                if token != self._seq:
                    return
                # 2. spark, 3. the knob is FLUNG across to meet it,
                # 4. the switch flares and settles into a steady glow
                self.anim.run(("spark", k), 0.0, 1.0, 150,
                              self._set("_spark"))
                self.anim.run(("pos", k), self._pos, 1.0, 170,
                              self._set("_pos"),
                              ease=lambda p: ease_out_back(p, 2.0))
                self.anim.run(("glow", k), self._glow, 1.35, 120,
                              self._set("_glow"),
                              done=lambda: (token == self._seq and
                                            self.anim.run(("glow", k), 1.35, 1.0,
                                                          150,
                                                          self._set("_glow"))))
            self.anim.run(("swing", k), self._swing, 1.0, 200,
                          self._set("_swing"), ease=ease_in, done=contact)
        else:
            # the magnet lets go: it retreats, the knob flicks back, light dies
            self.anim.run(("swing", k), self._swing, 0.0, 220,
                          self._set("_swing"), ease=ease_out)
            self.anim.cancel(("spark", k))
            self._spark = 0.0
            self.after(40, lambda: token == self._seq and
                       self.anim.run(("pos", k), self._pos, 0.0, 150,
                                     self._set("_pos"),
                                     ease=lambda p: ease_out_back(p, 1.6)))
            self.anim.run(("glow", k), self._glow, 0.0, 220,
                          self._set("_glow"))

    def retint(self, accent, bg):
        self.accent, self._bg = accent, bg
        self.configure(bg=bg)
        self._cache_key = None          # bloom must be re-baked for the new bg
        self._paint()


class Toggle(tk.Canvas):
    """Plain pill toggle for every other switch: the knob slides with a soft
    overshoot and the track cross-fades to the accent."""

    W, H = 44, 24
    KR = 9

    def __init__(self, parent, anim, accent, on=False, command=None, bg=CARD):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.anim, self.accent, self.on = anim, accent, on
        self.command = command
        self._pos = 1.0 if on else 0.0
        self._track = self.create_polygon(
            round_pts(1, 1, self.W - 1, self.H - 1, (self.H - 2) / 2),
            smooth=True, fill=LINE, outline="")
        self._knob = self.create_oval(0, 0, 0, 0, fill=DIM, outline="")
        self._paint(self._pos)
        self.bind("<Button-1>", lambda _e: self.flip())

    def _paint(self, pos):
        self._pos = pos
        cx = 12 + (self.W - 24) * pos
        cy = self.H / 2
        self.coords(self._knob, cx - self.KR, cy - self.KR,
                    cx + self.KR, cy + self.KR)
        self.itemconfigure(self._track, fill=hex_lerp(LINE, self.accent, pos))
        self.itemconfigure(self._knob, fill=hex_lerp(DIM, BG, pos))

    def flip(self):
        self.set(not self.on)
        if self.command:
            self.command(self.on)

    def set(self, on, animate=True):
        self.on = on
        target = 1.0 if on else 0.0
        if animate:
            self.anim.run(("tgl", id(self)), self._pos, target, 140,
                          self._paint, ease=ease_out_back)
        else:
            self._paint(target)

    def retint(self, accent, bg):
        self.accent = accent
        self.configure(bg=bg)
        self._paint(self._pos)


class Slider(tk.Canvas):
    """Matte slider with a value bubble that pops in while dragging and
    fades after release. Double-click glides the knob home to its default."""

    H = 44
    PAD = 10
    KR = 8
    TRACK_Y = 30

    def __init__(self, parent, anim, accent, width=380, lo=0, hi=100,
                 value=50, default=None, fmt="{:.0f}", command=None, bg=CARD):
        super().__init__(parent, width=width, height=self.H, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.anim, self.accent, self.w = anim, accent, width
        self.lo, self.hi = float(lo), float(hi)
        self.value = float(value)
        self.default = float(value if default is None else default)
        self.fmt, self.command, self._bg = fmt, command, bg
        self._bubble = 0.0
        ty = self.TRACK_Y
        self._track = self.create_polygon(
            round_pts(self.PAD, ty - 3, width - self.PAD, ty + 3, 3),
            smooth=True, fill=LINE, outline="")
        self._fill = self.create_polygon(
            round_pts(self.PAD, ty - 3, self.PAD + 6, ty + 3, 3),
            smooth=True, fill=accent, outline="")
        self._ring = self.create_oval(0, 0, 0, 0, fill=bg, outline="")
        self._knob = self.create_oval(0, 0, 0, 0, fill=accent, outline="")
        self._box = self.create_polygon(round_pts(0, 0, 1, 1, 8), smooth=True,
                                        fill=bg, outline="", state="hidden")
        self._txt = self.create_text(0, 0, text="", font=FONTS["bubble"],
                                     fill=bg, state="hidden")
        self._paint()
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: self._bub_to(0.0, 400))
        self.bind("<Double-Button-1>", self._reset)

    def _x_for(self, v):
        span = self.w - 2 * self.PAD
        return self.PAD + span * (v - self.lo) / (self.hi - self.lo)

    def _v_for(self, x):
        span = self.w - 2 * self.PAD
        return self.lo + max(0.0, min(1.0, (x - self.PAD) / span)) * (self.hi - self.lo)

    def _paint(self):
        x, ty = self._x_for(self.value), self.TRACK_Y
        self.coords(self._fill, *round_pts(self.PAD, ty - 3,
                                          max(x, self.PAD + 6), ty + 3, 3))
        self.coords(self._ring, x - self.KR - 2, ty - self.KR - 2,
                    x + self.KR + 2, ty + self.KR + 2)
        self.coords(self._knob, x - self.KR, ty - self.KR, x + self.KR, ty + self.KR)
        self._paint_bubble()

    def _paint_bubble(self):
        a = self._bubble
        if a <= 0.01:
            self.itemconfigure(self._box, state="hidden")
            self.itemconfigure(self._txt, state="hidden")
            return
        txt = self.fmt.format(self.value)
        bw = max(32, 9 * len(txt) + 14)
        x = max(self.PAD + bw / 2, min(self.w - self.PAD - bw / 2,
                                       self._x_for(self.value)))
        y = 12 + 3 * (1 - a)
        self.coords(self._box, *round_pts(x - bw / 2, y - 9, x + bw / 2, y + 9, 8))
        self.coords(self._txt, x, y)
        self.itemconfigure(self._box, state="normal",
                           fill=hex_lerp(self._bg, self.accent, a))
        self.itemconfigure(self._txt, state="normal", text=txt,
                           fill=hex_lerp(self._bg, BG, a), font=FONTS["bubble"])

    def _bub_to(self, target, dur):
        def step(v):
            self._bubble = v
            self._paint_bubble()
        self.anim.run(("bub", id(self)), self._bubble, target, dur, step)

    def _press(self, e):
        self.anim.cancel(("val", id(self)))
        self._apply(self._v_for(e.x))
        self._bub_to(1.0, 100)

    def _drag(self, e):
        self._apply(self._v_for(e.x))

    def _reset(self, _e):
        self._bub_to(1.0, 100)
        self.anim.run(("val", id(self)), self.value, self.default, 180,
                      self._apply, ease=ease_in_out,
                      done=lambda: self._bub_to(0.0, 400))

    def _apply(self, v):
        self.value = max(self.lo, min(self.hi, v))
        self._paint()
        if self.command:
            self.command(self.value)

    def glide_to(self, v, dur=180):
        self.anim.run(("val", id(self)), self.value, float(v), dur,
                      self._apply, ease=ease_in_out)

    def retint(self, accent, bg):
        self.accent, self._bg = accent, bg
        self.configure(bg=bg)
        self.itemconfigure(self._fill, fill=accent)
        self.itemconfigure(self._knob, fill=accent)
        self.itemconfigure(self._ring, fill=bg)
        self._paint()


class PillButton(tk.Canvas):
    """Secondary pill: contents shift 1px down and darken on press, then
    spring back."""

    def __init__(self, parent, anim, text, command=None, bg=CARD):
        f = tkfont.Font(font=FONTS["nav"])
        w = f.measure(text) + 34
        super().__init__(parent, width=w, height=30, bg=bg,
                         highlightthickness=0, cursor="hand2")
        # NB: never assign self._w on a Tk widget — that's Tkinter's internal
        # Tcl path name, and clobbering it breaks every later call.
        self.anim, self.command, self._btn_w = anim, command, w
        self._d = 0.0
        self._pill = self.create_polygon(round_pts(1, 1, w - 1, 27, 13),
                                         smooth=True, fill=CARD_HOVER,
                                         outline="")
        self._label = self.create_text(w / 2, 14, text=text,
                                       font=FONTS["nav"], fill=TEXT)
        self.bind("<Button-1>", self._down)
        self.bind("<ButtonRelease-1>", self._up)

    def _paint(self, d):
        self._d = d
        self.coords(self._pill, *round_pts(1, 1 + d, self._btn_w - 1, 27 + d, 13))
        self.coords(self._label, self._btn_w / 2, 14 + d)
        self.itemconfigure(self._pill, fill=hex_lerp(CARD_HOVER, LINE, d))

    def _down(self, _e):
        self.anim.run(("btn", id(self)), self._d, 1.0, 80, self._paint)

    def _up(self, e):
        self.anim.run(("btn", id(self)), self._d, 0.0, 120, self._paint,
                      ease=ease_out_back)
        if 0 <= e.x <= self._btn_w and 0 <= e.y <= 30 and self.command:
            self.command()

    def retint(self, bg):
        self.configure(bg=bg)
