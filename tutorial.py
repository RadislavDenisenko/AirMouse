"""First-run walkthrough: learn the gestures by doing them.

A gesture mouse has a discovery problem. Nothing on screen suggests that
touching your thumb to your ring finger sets the volume, and a list of poses
in a README is read once and forgotten. So the app teaches its own controls
the only way that sticks — it asks for each gesture in turn, and you drive
the walkthrough itself with the gestures you are learning.

Four decisions shape this screen, all of them from watching someone use it:

  * **A sidebar of numbered steps.** Where you are, what is behind you and
    what is coming, readable without moving your eyes off the picture.
  * **The instruction goes above the hands, big.** It used to sit in a
    paragraph under the panels and was never read — eyes go to the moving
    picture, not to body text below it.
  * **Nothing advances on its own.** Performing the gesture used to jump
    straight to the next step, leaving no time to notice what had happened.
    Now the gesture only marks the step satisfied; you move on by clicking
    Next, so you can play with volume or scrolling as long as you like.
  * **The cursor is real.** A pointer driven by the same motion model the
    app uses, and clicking Next is a genuine thumb-index pinch. Finishing
    the walkthrough means you have already used the mouse.

Everything is drawn on one canvas rather than assembled from widgets,
because the pointer has to float above every panel and hit-test a button,
which stacked widgets make awkward.

Detection deliberately runs a REAL `GestureController` over a `NullMouse`.
The walkthrough could recognise poses with its own simpler tests, but then
it would teach a slightly different app than the one you go on to use —
every threshold, debounce and hysteresis here is the shipping one. The null
mouse is also what makes the demos safe: swiping and minimising move drawn
pictures, never your actual desktop.

This window owns the webcam while it runs, because only one program can.
It closes the camera before handing back, and the tracker starts after.
"""

import math
import time
import tkinter as tk
import tkinter.font as tkfont

import cv2
from PIL import Image, ImageTk

from gestures import GestureController, IDLE, ENGAGING, SCROLL
from hand_roles import RoleAssigner
from hand_tracker import (HandTracker, INDEX_TIP, MIDDLE_TIP, RING_TIP,
                          THUMB_TIP, draw_landmarks, to_pixel_points)
from mouse_input import NullMouse
from paths import HAND_MODEL
from settings_ui import (ACCENTS, BG, CARD, DIM, FONTS, LINE, OK, PANEL, TEXT,
                         hex_lerp, init_fonts, round_pts)

# ------------------------------------------------------------------ layout --
W, H = 1240, 872
RAIL = 272                                  # sidebar width
MX = RAIL + 44                              # main content left edge

PILL = (MX, 40, 132, 30)                    # x, y, w, h
# Two lines of 32pt run from HEAD_Y to about HEAD_Y + 104, so the subtitle
# cannot start before ~200. There is deliberately no rule between them: the
# STEP N OF 8 pill already does the eyebrow job, and a short accent dash
# landing anywhere near a 32pt baseline just underlines the first word.
HEAD_Y, SUB_Y = 92, 228
OK_Y = 292                                  # the "you did it" line, under it
# BOTH COLUMNS SHARE A GRID. The camera panel's top edge locks to the
# headline's cap line, and the stage's bottom lands on the diagram's, so
# every hard edge on the right is answered by one on the left. The camera
# panel's height is the one thing chosen for another reason: 404x304 of
# picture is 4:3, so a webcam frame is not stretched to fit it.
CAM = (W - 44 - 424, HEAD_Y, 424, 324)      # 92 .. 416
STAGE = (W - 44 - 424, 448, 424, 242)       # 448 .. 690
DIAG = (MX, 296, 452, 394)                  # the diagram's zone, 296 .. 690
NEXT_BTN = (W - 44 - 216, H - 102, 216, 64)
SKIP_BTN = (NEXT_BTN[0] - 24 - 112, H - 94, 112, 48)
FOOT_Y = H - 140                            # hairline the buttons sit under
BOTTOM = H - 38                             # shared baseline: tip card, Next
TIP_X, TIP_W = 28, RAIL - 56                # the card's height follows its text

RAIL_TOP, RAIL_GAP = 120, 58                # sidebar rows, also their hit area
SIDEBAR = "#1a1714"                         # a shade under the page
CARD_SOFT = "#2b2621"

# THREE RADII, NOT NINE. Values a pixel or two apart read as unresolved
# rather than as intent, so every rounded thing on the page picks one of
# these: page-level panels, the cards and buttons inside them, small chips.
R_PANEL, R_CARD, R_CHIP = 16, 10, 4
STAGE_PAD = 48                              # the demo panel's ONE inset

FINGERS = ("index", "middle", "ring", "pinky")

POSES = {
    "spread":  {"ext": (1.0, 1.0, 1.0, 1.0), "thumb": "out", "spread": 1.0},
    # A pinching finger curls down so the thumb meets it out in front of the
    # palm. Since fingers now FOLD rather than just shorten, this sits higher
    # than it used to: the same 0.45 would drop the tip onto the knuckle line.
    "index":   {"ext": (0.62, 1.0, 1.0, 1.0), "thumb": "index"},
    "middle":  {"ext": (1.0, 0.62, 1.0, 1.0), "thumb": "middle"},
    "ring":    {"ext": (1.0, 1.0, 0.62, 1.0), "thumb": "ring"},
    "brake":   {"ext": (1.0, 0.16, 0.16, 0.16), "thumb": "out"},
    # Not fully folded: at zero the fingers vanish into the palm and the hand
    # reads as an empty box. A shallow curl still says "closed".
    "fist":    {"ext": (0.24, 0.24, 0.24, 0.24), "thumb": "in"},
    "peace":   {"ext": (1.0, 1.0, 0.16, 0.16), "thumb": "in"},
}

PINCH_TIPS = {"click": (THUMB_TIP, INDEX_TIP),
              "rclick": (THUMB_TIP, MIDDLE_TIP),
              "volume": (THUMB_TIP, RING_TIP),
              "next": (THUMB_TIP, INDEX_TIP)}


def big_fonts():
    """FIVE SIZES, and no more: 32 display / 16 button / 14 body / 12 label /
    10 micro-caps.

    The instruction has to be readable without looking away from your own
    hand, so it is far larger than anything in the settings app. The tier
    UNDER it matters just as much and used to be the weakest part: the
    camera has to see a whole hand, so you sit further back than for normal
    desktop work, and at that distance a 9pt grey label is a smudge. Every
    label that carried real information — the tip card, the demo readouts,
    the rail's cheat-sheet sub-labels — has been lifted out of that band,
    and the only things left at 10pt are all-caps eyebrows, which all now
    share one size so they read as one part of speech."""
    disp, body = FONTS["display_name"], FONTS["body_name"]
    return {"huge": (disp, 32, "bold"),
            "btn": (disp, 16, "bold"), "brand": (disp, 16, "bold"),
            "sub": (body, 14), "readout": (disp, 14, "bold"),
            "tip": (body, 12), "count": (body, 12),
            # active/inactive rail rows differ by WEIGHT ONLY, so a row does
            # not visibly jump a pixel taller the moment you arrive on it
            "rail": (disp, 12, "bold"), "rail_dim": (disp, 12),
            "micro": (disp, 10, "bold")}


# ------------------------------------------------------------ the drawn hand --
class HandDiagram:
    """A stylised hand that morphs between poses, drawn onto a shared canvas.

    Built from primitives rather than shipped as images: it has to animate
    between poses, and a folder of PNGs could not. A finger is a two-segment
    polyline that FOLDS as its extension drops — a straight capsule that only
    gets shorter reads as a stick lying on the palm, never as a curl.
    """

    # The hand used to sit small inside a large target, which reads as timid
    # rather than airy — it is the biggest thing on the page, so that
    # impression carries the whole screen. LEN and S scale together (a
    # middle finger is about 1.33 palms), so the proportions hold.
    LEN = 130                         # the middle finger, fully extended
    S = 1.0                           # everything else is measured against it
    WIDTHS = (17, 18, 16, 14)         # index, middle, ring, pinky
    # Concentric rings: a stage to stand on. The OUTER one is also the track
    # the direction arrows sit on, which is what fixes the hero's optical
    # bounds — see _draw_arrows.
    RINGS = (172, 146, 120)
    BASE_DY = 16                      # the knuckle line, below the ring's eye

    def __init__(self, accent):
        self.accent = accent
        self.cur = {"ext": [1.0] * 4, "spread": 1.0}
        self.target = dict(POSES["spread"])
        self._lit = set()
        self._pulse = 0.0
        self.flash = 0.0              # one-shot acknowledgement of a success
        self.motion = None            # 'updown' | 'leftright' | 'down' | None
        # Seed the origin from the zone straight away. tick() runs before the
        # first draw(), and with an unset origin the thumb eases toward a
        # target near (0, 0) — which drew it as a bar across the window.
        self.cx = DIAG[0] + DIAG[2] / 2 + 6
        self.base_y = DIAG[1] + DIAG[3] / 2 + self.BASE_DY
        self._thumb = self._thumb_target("out", self.cur["ext"])

    def _finger_x(self, i, spread=1.0):
        return self.cx + (-50, -17, 17, 48)[i] * self.S * (0.75 + 0.25 * spread)

    def _finger_len(self, i):
        return self.LEN * (0.86, 1.0, 0.94, 0.74)[i]

    def _finger_path(self, i, e, spread):
        """Knuckle, mid-joint and fingertip for one extension value.

        A real finger closing does two things at once: the proximal segment
        drops toward the palm and the rest folds forward and back down over
        it. Modelling both means a fist ends as a closed silhouette with a
        row of knuckles, instead of four stubs poking out of the top edge.

        The FLOOR on that first segment is load-bearing. At 0.13 a tightly
        curled finger raised about as far as its own stroke is wide, so the
        vertical run vanished inside its own round cap and all that survived
        was the horizontal fold — three separate capsules lying on the palm,
        which is what the brake pose looked like. A curled finger must always
        show a segment taller than the stroke that draws it.
        """
        fx = self._finger_x(i, spread)
        ln = self._finger_len(i)
        up = ln * (0.30 + 0.70 * e ** 1.6)          # how high the knuckle goes
        fold = 1.0 - e                              # how far the tip comes back
        knee = (fx, self.base_y - up)
        return ((fx, self.base_y + 22),
                knee,
                (fx + 7 * fold, knee[1] + ln * 0.62 * fold ** 1.3))

    def _thumb_target(self, where, ext):
        s = self.S
        if where == "out":
            return self.cx - 108 * s, self.base_y - 34 * s
        if where == "in":
            return self.cx - 60 * s, self.base_y - 26 * s
        # meet the fingertip where it actually ends up, fold and all
        i = FINGERS.index(where)
        tx, ty = self._finger_path(i, ext[i], self.cur["spread"])[2]
        return tx - 8, ty + 8

    def set_pose(self, name, lit=(), motion=None):
        self.target = dict(POSES.get(name, POSES["spread"]))
        self._lit = set(lit)
        self.motion = motion

    def tick(self, dt):
        k = min(1.0, dt * 7.0)
        for i in range(4):
            self.cur["ext"][i] += (self.target["ext"][i]
                                   - self.cur["ext"][i]) * k
        self.cur["spread"] += (self.target.get("spread", 0.6)
                               - self.cur["spread"]) * k
        goal = self._thumb_target(self.target.get("thumb", "out"),
                                  self.cur["ext"])
        self._thumb = tuple(self._thumb[j] + (goal[j] - self._thumb[j]) * k
                            for j in range(2))
        self._pulse = (self._pulse + dt * 2.0) % (math.pi * 2)
        self.flash = max(0.0, self.flash - dt * 1.4)

    def draw(self, cv, zone, fonts):
        x, y, w, h = zone
        ccx, ccy = x + w / 2, y + h / 2
        # The rings are the stage, so they own the zone's centre. The hand
        # sits a touch to the right of it because the thumb throws its mass
        # left; drawn on the same centre it looks pinned to one side.
        self.cx = ccx + 6
        # The hand spans (base_y - LEN) to (base_y + 98 * S), so its own
        # middle is ~16px above base_y. Offset by that or it hangs low.
        self.base_y = ccy + self.BASE_DY
        glow = 0.5 + 0.5 * math.sin(self._pulse)
        hot = hex_lerp(self.accent, "#ffffff", 0.22 * glow)

        # Concentric rings — circles, not ellipses, because the arrow discs
        # ride the outer one and a circle gives them the same reach on both
        # axes. All three clear the silhouette and fade inward.
        for i, r in enumerate(self.RINGS):
            col = hex_lerp(BG, LINE, 0.55 - i * 0.13)
            if i == 0 and self.flash > 0:
                col = hex_lerp(col, self.accent, self.flash)
            cv.create_oval(ccx - r, ccy - r, ccx + r, ccy + r, outline=col,
                           width=1)
        # A success has to be acknowledged where the eyes already are, and on
        # this screen that is the hand, not a button in the far corner.
        if self.flash > 0:
            rr = self.RINGS[0] + 20 * (1.0 - self.flash)
            cv.create_oval(ccx - rr, ccy - rr, ccx + rr, ccy + rr, width=2,
                           outline=hex_lerp(BG, OK, self.flash))

        s = self.S
        cx, base_y = self.cx, self.base_y
        skin = hex_lerp(DIM, BG, 0.34)
        edge = hex_lerp(DIM, "#ffffff", 0.10)
        # When the whole hand is the subject — every finger and the thumb —
        # the palm lights with them. Left grey it made an orange comb on a
        # grey blob, which reads as an oversight. Deriving it beats listing a
        # "palm" pseudo-finger in each step: it can never drift out of sync.
        # It lights well short of full accent, though: a 250px block of pure
        # amber out-shouts the pill, the brackets and the Next button.
        if self._lit >= {"thumb", *FINGERS}:
            skin = hex_lerp(hot, BG, 0.42)
            edge = hex_lerp(hot, "#ffffff", 0.2)

        # THE PALM, as one organic silhouette rather than a box. Fingers are
        # drawn over it in the same tone and started below the knuckle line,
        # so the whole thing reads as a hand instead of rods on a rectangle.
        # The bulge on the left is the thenar — the fleshy pad the thumb
        # actually grows out of, and the thing whose absence made earlier
        # passes look like sticks.
        palm = (cx - 70 * s, base_y - 6 * s,      # index-side knuckle
                cx - 20 * s, base_y - 14 * s,
                cx + 34 * s, base_y - 12 * s,
                cx + 68 * s, base_y + 2 * s,      # pinky-side knuckle
                cx + 72 * s, base_y + 44 * s,
                cx + 56 * s, base_y + 86 * s,     # heel, pinky side
                cx + 6 * s, base_y + 98 * s,
                cx - 40 * s, base_y + 90 * s,     # wrist
                cx - 74 * s, base_y + 56 * s,     # thenar bulge
                cx - 82 * s, base_y + 16 * s)
        cv.create_polygon(palm, smooth=True, splinesteps=24, fill=skin,
                          outline=edge, width=2)

        # Knuckle creases go on the palm BEFORE the fingers. Drawn after, they
        # land on top of the lit fingers and read as scuffs on the artwork.
        for i in range(4):
            kx = self._finger_x(i, self.cur["spread"])
            cv.create_arc(kx - 7, base_y + 3, kx + 7, base_y + 18,
                          start=20, extent=140, style=tk.ARC,
                          outline=hex_lerp(skin, BG, 0.22), width=2)

        for i, name in enumerate(FINGERS):
            root, knee, tip = self._finger_path(i, self.cur["ext"][i],
                                                self.cur["spread"])
            lit = name in self._lit
            colour = hot if lit else skin
            # Tapered: a pinky drawn as thick as a middle finger is the last
            # strong "this is a comb, not a hand" cue.
            wd = self.WIDTHS[i]
            # One polyline with round joins, so the fold is a continuous form
            # rather than two capsules meeting at a visible seam. It starts
            # well inside the palm so the knuckle joint is never a gap.
            cv.create_line(root[0], root[1], knee[0], knee[1], tip[0], tip[1],
                           fill=colour, width=wd + 1 if lit else wd,
                           capstyle=tk.ROUND, joinstyle=tk.ROUND)
            if not lit:                    # quiet outline keeps them separable
                cv.create_line(root[0], base_y + 2, knee[0], knee[1],
                               tip[0], tip[1], fill=edge, width=1,
                               capstyle=tk.ROUND, joinstyle=tk.ROUND)
            cv.create_oval(tip[0] - 4, tip[1] - 4, tip[0] + 4, tip[1] + 4,
                           fill=hex_lerp(colour, "#ffffff", 0.16), outline="")

        # the thumb, growing out of the thenar rather than floating beside it
        tx, ty = self._thumb
        lit = "thumb" in self._lit
        colour = hot if lit else skin
        cv.create_line(cx - 62 * s, base_y + 62 * s, cx - 76 * s,
                       base_y + 30 * s, tx, ty, fill=colour, width=19,
                       capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True)
        cv.create_oval(tx - 6, ty - 6, tx + 6, ty + 6,
                       fill=hex_lerp(colour, "#ffffff", 0.16), outline="")

        # Two capsules touching is ambiguous at a glance; a marked join is not.
        if self.target.get("thumb", "out") in FINGERS:
            r = 15 + 3 * glow
            cv.create_oval(tx - r, ty - r, tx + r, ty + r, width=3,
                           outline=hex_lerp(self.accent, "#ffffff", 0.45))

        self._draw_arrows(cv, ccx, ccy, glow)

    def _draw_arrows(self, cv, cx, cy, glow):
        """Circled arrows saying which way to MOVE once the pose is made.

        The pose alone never says that, and "move your hand up and down" read
        as body text is not the same as seeing where to go.

        THE DISCS RIDE THE OUTER RING, tucked just inside it, and they knock
        a clean gap out of it as they pass. That settles two things at once.
        The arrows and the rings stop being two graphic systems that happen
        to overlap — the disc is now a node ON the track — and, because the
        arrows can no longer reach past the ring, the illustration occupies
        exactly the same band whether a step has motion or not. It used to
        grow by 55px in each direction the moment a step had arrows, which
        made the hero visibly breathe as you advanced.

        The written UP / DOWN / LEFT / RIGHT labels are what forced the old
        arrows out past the ring, and an arrow in a circle already says which
        way it points, so they are gone. What is left is the reference
        language: a circled arrow joined to the subject by a dashed guide.
        """
        if not self.motion:
            return
        pairs = {"updown": ((0, -1), (0, 1)),
                 "leftright": ((-1, 0), (1, 0)),
                 "left": ((-1, 0),),
                 "right": ((1, 0),),
                 "down": ((0, 1),)}[self.motion]
        # breathing INWARD from the ring, so the outer edge is never crossed
        reach = self.RINGS[0] - 22 - 4 + 4 * glow
        for dx, dy in pairs:
            ax, ay = cx + dx * reach, cy + dy * reach
            # A dashed run from the EDGE of the hand out to the disc. Where
            # the hand fills the axis — up and down, where the fingers reach
            # nearly to the ring — there is no honest room for one, and a
            # 20px floating stub joined to nothing is worse than nothing.
            near, far = 84, reach - 24
            if far - near > 16:
                cv.create_line(cx + dx * near, cy + dy * near,
                               cx + dx * far, cy + dy * far,
                               fill=hex_lerp(BG, self.accent, 0.4),
                               width=2, dash=(4, 5))
            cv.create_oval(ax - 22, ay - 22, ax + 22, ay + 22,
                           fill=self.accent, outline=BG, width=7)
            cv.create_line(ax - dx * 8, ay - dy * 8, ax + dx * 8, ay + dy * 8,
                           fill=BG, width=4, capstyle=tk.ROUND,
                           arrow=tk.LAST, arrowshape=(9, 11, 5))


# ------------------------------------------------------------- demo stage --
DOCKED = 0.16          # a minimised window stops here, on the bar, not at 0


class DemoStage:
    """A pretend desktop that reacts to the gesture being taught.

    Being told a gesture sets the volume is not the same as watching a bar
    move because of your own hand. Nothing here touches the real system: the
    walkthrough drives a NullMouse, so these are the calls the app WOULD have
    made, drawn instead of performed. That is what makes it safe to practise
    minimising windows.
    """

    def __init__(self, accent):
        self.accent = accent
        self.mode = "none"
        self.label = ""
        self.level = 0.0
        self.shown = 1.0
        self.slide = 0.0
        self.flash = 0.0
        self.gone = False
        self.pressed = False          # the click demo's button has been hit
        self.dragging = False
        self.win = [0.0, 0.0]         # the draggable window's offset
        self.blink = 0.0
        self.word = "forward"         # which way the swipe demo is going

    def _blink(self):
        """Alerting without being unpleasant. A hard flash at a few hertz is
        genuinely hard to look at, so this crossfades over three seconds —
        noticeable in peripheral vision, calm to sit next to while you work
        out what to do.

        It pulses BETWEEN TWO TINTS OF THE ACCENT rather than toward white.
        The old gold was a second hue, roughly 16 degrees off, and since it
        was also the largest, brightest thing on the click step it made the
        one screen whose whole job is "there is a single accent" the one
        screen with two."""
        t = 0.5 - 0.5 * math.cos(self.blink * math.pi / 1.5)
        return hex_lerp(self.accent, hex_lerp(self.accent, "#ffffff", 0.35), t)

    def set_mode(self, mode, label="", word="forward"):
        self.mode = mode
        self.label = label
        self.word = word
        self.level = 0.5 if mode == "volume" else (1.0 if mode == "brake" else 0.0)
        self.shown = 1.0
        self.slide = 0.0
        self.flash = 0.0
        self.gone = False
        self.pressed = False
        self.dragging = False
        self.win = [0.0, 0.0]

    def tick(self, dt):
        self.blink += dt
        self.flash = max(0.0, self.flash - dt * 1.8)
        self.slide *= max(0.0, 1.0 - dt * 2.2)
        if self.mode == "minimize" and self.gone:
            self.shown += (DOCKED - self.shown) * min(1.0, dt * 3.2)
        elif self.mode == "anchor":
            self.level += dt * 0.7          # the ring closes, then repeats

    def draw(self, cv, zone, fonts):
        x, y, w, h = zone
        cv.create_text(x + STAGE_PAD, y + 20, text=self.label.upper(),
                       anchor="nw", fill=DIM, font=fonts["micro"])
        fn = getattr(self, f"_draw_{self.mode}", None)
        if fn is not None:
            fn(cv, x + w / 2, y + h / 2 + 14, x, y, w, h, fonts)

    def _draw_anchor(self, cv, cx, cy, x, y, w, h, fonts):
        """A ring closing onto a point: what engaging actually does. The
        opening step otherwise had nothing to show, and an empty panel reads
        as something that failed to load."""
        t = (self.level % 1.0)
        r = 54 - 26 * t
        cv.create_oval(cx - 30, cy - 30, cx + 30, cy + 30, outline=LINE,
                       width=2)
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, width=2,
                       outline=hex_lerp(self.accent, PANEL, t), dash=(5, 4))
        cv.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=self.accent,
                       outline="")
        cv.create_text(x + w - STAGE_PAD, cy, text="anchor", fill=DIM,
                       font=fonts["tip"], anchor="e")

    def _draw_click(self, cv, cx, cy, x, y, w, h, fonts):
        """Press the button, then drag the window it turns into.

        Two things to learn — tap to click, hold to drag — and the second
        only makes sense once the first has happened, so the panel changes
        into the drag exercise the moment the button is pressed."""
        if self.pressed:
            return self._draw_drag(cv, cx, cy, x, y, w, h, fonts)
        fill = hex_lerp(CARD_SOFT, self.accent, 0.6 * self.flash)
        # 2px, not 3: the brightest, heaviest object on the page has to be
        # the Next button, and a 3px pulsing outline out-ranked it.
        cv.create_polygon(round_pts(cx - 100, cy - 25, cx + 100, cy + 25,
                                    R_CARD),
                          smooth=True, fill=fill,
                          outline=self._blink(), width=2)
        cv.create_text(cx, cy, text="PRESS ME", fill=self._blink(),
                       font=fonts["readout"])

    def _draw_drag(self, cv, cx, cy, x, y, w, h, fonts):
        """A little window whose title bar is lit: grab it and move it."""
        wx, wy = cx + self.win[0], cy + self.win[1]
        ww, wh = 132, 74
        held = self.dragging
        cv.create_polygon(round_pts(wx - ww / 2, wy - wh / 2, wx + ww / 2,
                                    wy + wh / 2, R_CARD),
                          smooth=True, fill=CARD_SOFT,
                          outline=self.accent if held else LINE, width=2)
        # the title bar, lit so it is obvious what to take hold of
        bar = hex_lerp(self.accent, "#ffffff", 0.15) if held else self._blink()
        cv.create_polygon(round_pts(wx - ww / 2, wy - wh / 2, wx + ww / 2,
                                    wy - wh / 2 + 22, R_CARD),
                          smooth=True, fill=bar, outline="")
        cv.create_text(wx, wy - wh / 2 + 11, text="drag me", fill=BG,
                       font=fonts["micro"])
        for i in range(2):
            cv.create_line(wx - 40, wy + 4 + i * 14, wx + 40, wy + 4 + i * 14,
                           fill=DIM, width=4, capstyle=tk.ROUND)
        # This is the ONLY place the drag mechanic is explained next to the
        # thing being dragged, so it is an instruction at body size, centred
        # under the window — not a caption smaller than the window it is
        # about, tucked into the corner furthest from it.
        cv.create_text(cx, y + h - 24, anchor="center", fill=DIM,
                       font=fonts["sub"],
                       text="pinch and hold on the bar, then move")

    def _draw_rclick(self, cv, cx, cy, x, y, w, h, fonts):
        x0 = x + STAGE_PAD
        cv.create_polygon(round_pts(x0, cy - 22, x0 + 150, cy + 22, R_CARD),
                          smooth=True, fill=CARD_SOFT, outline=LINE, width=2)
        cv.create_text(x0 + 75, cy, text="right-click me", fill=DIM,
                       font=fonts["tip"])
        if self.flash > 0:
            n = min(1.0, (1.0 - self.flash) * 3.0)
            mh = 60 * n
            mx = x0 + 166
            cv.create_polygon(round_pts(mx, cy - 14, x + w - STAGE_PAD,
                                        cy - 14 + mh, R_CARD),
                              smooth=True, fill=CARD, outline=self.accent,
                              width=2)
            for i in range(3):
                ly = cy + 2 + i * 15
                if ly < cy - 14 + mh - 8:
                    cv.create_line(mx + 16, ly, x + w - STAGE_PAD - 16, ly,
                                   fill=DIM, width=2)

    def _draw_volume(self, cv, cx, cy, x, y, w, h, fonts):
        # ONE inset, shared with every other demo, so the same-looking bar
        # does not slide sideways and change length as you advance a step.
        # The speaker and the readout sit on a row ABOVE the bar rather than
        # eating into it from the ends.
        x0, x1 = x + STAGE_PAD, x + w - STAGE_PAD
        ry = cy - 44
        cv.create_polygon(x0, ry - 7, x0 + 7, ry - 7, x0 + 16, ry - 15,
                          x0 + 16, ry + 15, x0 + 7, ry + 7, x0, ry + 7,
                          fill=DIM, outline="")
        for i in range(3):
            if self.level > (i + 1) * 0.28:
                r = 11 + i * 8
                cv.create_arc(x0 + 16 - r, ry - r, x0 + 16 + r, ry + r,
                              start=-55, extent=110, style=tk.ARC,
                              outline=self.accent, width=2)
        cv.create_text(x1, ry, text=f"{int(self.level * 100)}", fill=TEXT,
                       font=fonts["rail"], anchor="e")
        self._bar(cv, x0, x1, cy, self.level, knob=True)

    def _draw_brake(self, cv, cx, cy, x, y, w, h, fonts):
        x0, x1 = x + STAGE_PAD, x + w - STAGE_PAD
        ry = cy - 44
        cv.create_text(x0, ry, text="cursor speed", fill=DIM,
                       font=fonts["tip"], anchor="w")
        # the same treatment the volume readout gets — one job, one style
        cv.create_text(x1, ry, text=f"x{self.level:.2f}", fill=TEXT,
                       font=fonts["rail"], anchor="e")
        self._bar(cv, x0, x1, cy, self.level)

    def _bar(self, cv, x0, x1, cy, level, knob=False):
        cv.create_line(x0, cy, x1, cy, fill=LINE, width=9, capstyle=tk.ROUND)
        fx = x0 + (x1 - x0) * max(0.0, min(1.0, level))
        cv.create_line(x0, cy, fx, cy, fill=self.accent, width=9,
                       capstyle=tk.ROUND)
        if knob:
            cv.create_oval(fx - 10, cy - 10, fx + 10, cy + 10,
                           fill=hex_lerp(self.accent, "#ffffff", 0.2),
                           outline="")

    def _draw_scroll(self, cv, cx, cy, x, y, w, h, fonts):
        x0, x1 = x + STAGE_PAD, x + w - STAGE_PAD
        top, bot = y + 62, y + h - 24
        cv.create_polygon(round_pts(x0, top, x1, bot, R_CARD),
                          smooth=True, fill=CARD_SOFT, outline=LINE, width=2)
        off = self.level % 24
        for i in range(-1, 8):
            ly = top + 16 + i * 24 + off
            if top + 8 < ly < bot - 8:
                cv.create_line(x0 + 24, ly, x1 - 24, ly, fill=DIM, width=5,
                               capstyle=tk.ROUND)

    def _draw_swipe(self, cv, cx, cy, x, y, w, h, fonts):
        """Two pages, one leaving and one arriving.

        Both are real pages with content in them: an incoming card filled a
        hair off the panel behind it reads as a hole, not as a page. The
        destination is named INSIDE the card that is arriving — the old
        callout floated 16px above the top edge of a box it was meant to
        label, attached to nothing, and was the only amber text on the panel.
        The pair is offset by half its own step so the composition stays
        centred on the panel at rest instead of sitting 44px off-axis.
        """
        dx = self.slide * 104
        side = -1 if self.word == "back" else 1
        base = cx - side * 75
        cw, ch = 68, 42
        for k, alpha in ((1, 0.55), (0, 1.0)):
            px = base + dx + k * side * 150
            cv.create_polygon(round_pts(px - cw, cy - ch, px + cw, cy + ch,
                                        R_CARD),
                              smooth=True,
                              fill=hex_lerp(PANEL, CARD_SOFT, alpha),
                              outline=LINE, width=2)
            for i in range(2):
                ly = cy + 2 + i * 18
                cv.create_line(px - cw + 20, ly, px + cw - 20, ly, fill=DIM,
                               width=4, capstyle=tk.ROUND)
        cv.create_text(base + dx + side * 150, cy - ch + 20, text=self.word,
                       fill=self.accent, font=fonts["micro"])

    def _draw_minimize(self, cv, cx, cy, x, y, w, h, fonts):
        # A taskbar with some body to it, not a hairline: a 3px rule at the
        # panel's edge reads as a broken border rather than as somewhere the
        # window could go. It sits on a real bottom inset, and it is deep
        # enough that the docked marker lands on its FACE — drawn on its
        # bottom edge, the two 1px strokes fought each other.
        bar_t, bar_b = y + h - 56, y + h - 24
        cv.create_polygon(round_pts(x + STAGE_PAD, bar_t, x + w - STAGE_PAD,
                                    bar_b, R_CHIP),
                          smooth=True, fill=hex_lerp(CARD, BG, 0.55),
                          outline=LINE, width=1)
        # Shrink AND travel down to the bar, then STOP at a docked stub.
        # Easing all the way to nothing left the last thing in the whole
        # walkthrough as an empty box — which reads as a panel that failed.
        home = y + 110
        rest = (bar_t + bar_b) / 2
        t = max(0.0, (self.shown - DOCKED) / (1.0 - DOCKED))
        my = rest + (home - rest) * t
        hw, hh = 26 + 52 * t, 8 + 23 * t
        # Once it has docked the panel would otherwise be 120px of nothing
        # above a bar — on the LAST frame of the whole walkthrough. Leaving
        # the outline of where the window was makes the panel say "it went
        # from here to there" instead.
        if t < 0.92:
            cv.create_polygon(round_pts(cx - 78, home - 31, cx + 78,
                                        home + 31, R_CARD),
                              smooth=True, fill="", outline=CARD_SOFT,
                              width=2, dash=(5, 5))
        cv.create_polygon(round_pts(cx - hw, my - hh, cx + hw, my + hh,
                                    R_CHIP + 6 * t),
                          smooth=True, fill=CARD_SOFT, outline=LINE, width=2)
        if t > 0.55:
            cv.create_line(cx - hw + 10, my - hh + 11, cx + hw - 10,
                           my - hh + 11, fill=DIM, width=3)
            cv.create_text(cx, my + 4, text="a window", fill=DIM,
                           font=fonts["tip"])
        else:
            # docked — marked the way a running app is, so the step lands on
            # "there it is" instead of on an empty panel
            cv.create_line(cx - hw + 5, my + hh + 6, cx + hw - 5, my + hh + 6,
                           fill=self.accent, width=2, capstyle=tk.ROUND)


# ----------------------------------------------------------------- steps ----
# `done` reads the live state dict built each frame from a real
# GestureController, so a step is satisfied on exactly the condition the app
# uses. Satisfying it does NOT advance — that is what Next is for.
#
# `ok` is what the screen says back the instant the gesture registers. It is
# drawn under the subtitle at instruction scale, because the acknowledgement
# has to land in the same fixation as the thing that asked for it: the eyes
# are on the hand and the demo panel, and a button changing colour in the
# far bottom corner is exactly the signal people report not seeing.
STEPS = [
    # AUTO-ADVANCES. This is the one step that must, because you cannot press
    # Next until you have been taught to click, and being stranded on step 1
    # unable to leave it is the worst possible first impression. FIVE seconds,
    # not three: satisfied fires the moment tracking starts, so the clock was
    # running while the user still had to read a 32pt headline, read the
    # subtitle, find their own hand in the camera panel and move it — four
    # things in three seconds, on the first screen they have ever seen. The
    # wait is also drawn on the Next button, so it is a countdown rather than
    # the screen changing under them.
    {"key": "engage", "hand": "raise your hand", "name": "Wake it up",
     "pose": "spread", "motion": None, "auto_after": 5.0,
     "lit": ("index", "middle", "ring", "pinky", "thumb"),
     "big": "Raise your\nopen hand",
     "sub": "That is it — you are connected. Move your hand\nand watch the pointer follow.",
     "ok": "Connected — the pointer is yours",
     "tip": "Keep your whole hand in view of the camera.",
     "stage": "anchor", "stage_label": "the anchor locking on",
     "done": lambda s: s["engaged"]},

    {"key": "click", "hand": "thumb + index", "name": "Click", "pose": "index", "motion": None,
     "lit": ("index", "thumb"),
     "big": "Touch your thumb\nto your index",
     "sub": "Tap them together to click. Hold them together\nto drag something.",
     "ok": "Done — that was a click",
     "tip": "This is also how you press Next — try it on the button.",
     "stage": "click", "stage_label": "try it here",
     "done": lambda s: s["clicks"] > 0},

    {"key": "rclick", "hand": "thumb + middle", "name": "Right click", "pose": "middle", "motion": None,
     "lit": ("middle", "thumb"),
     "big": "Thumb to your\nmiddle finger",
     "sub": "The same tap, one finger over. That opens the\nright-click menu.",
     "ok": "Done — that was a right click",
     "tip": "Your thumb belongs to whichever fingertip is nearest.",
     "stage": "rclick", "stage_label": "what a right click does",
     "done": lambda s: s["rclicks"] > 0},

    {"key": "volume", "hand": "thumb + ring", "name": "Volume", "pose": "ring", "motion": "updown",
     "lit": ("ring", "thumb"),
     "big": "Pinch your\nring finger",
     "sub": "Keep pinching and move your hand up or down\nto set the volume.",
     "ok": "Done — you moved the volume",
     "tip": "Moving back down undoes it exactly. Nothing real changes here.",
     "stage": "volume", "stage_label": "volume — pretend, nothing real changes",
     "done": lambda s: s["volume"] != 0},

    {"key": "brake", "hand": "curl three fingers", "name": "Precision", "pose": "brake", "motion": None,
     "lit": ("middle", "ring", "pinky"),
     "big": "Curl your last\nthree fingers",
     "sub": "The cursor slows down. Squeeze harder and it\nslows further.",
     "ok": "Done — the cursor is slowed",
     "tip": "Index and thumb stay free, so you can still click while slowed.",
     "stage": "brake", "stage_label": "how much you are slowing the cursor",
     "done": lambda s: s["brake"] > 0.5},

    {"key": "scroll", "hand": "peace sign", "name": "Scroll", "pose": "peace", "motion": "updown",
     "lit": ("index", "middle"),
     "big": "Hold up a\npeace sign",
     "sub": "Move your hand up or down away from where\nyou made it. Further = faster.",
     "ok": "Done — you are scrolling",
     "tip": "Come back toward the middle to slow down and stop.",
     "stage": "scroll", "stage_label": "a list, scrolling",
     "done": lambda s: s["scrolling"]},

    # Back and forward are taught one direction at a time, each with a single
    # arrow. Showing both at once meant neither read as an instruction — you
    # cannot follow "left or right" without first picking one. This one hands
    # off to its own mirror image, so the wait is longer than a blink and is
    # drawn as a countdown: a user whose eyes were on their own hand would
    # otherwise swipe right again on the forward step and conclude the
    # gesture is unreliable.
    {"key": "swipe_back", "hand": "grab, swipe right", "name": "Go back",
     "pose": "fist", "motion": "right", "auto_after": 4.0,
     "lit": ("index", "middle", "ring", "pinky", "thumb"),
     "big": "Close your hand,\nswipe right",
     "sub": "A relaxed grab arms navigation. Swiping right\ngoes back a page.",
     "ok": "Done — you went back a page",
     "tip": "It does not need to be a tight fist — just closed.",
     "stage": "swipe", "stage_label": "going back a page",
     "done": lambda s: s["back"] != 0},

    {"key": "swipe_fwd", "hand": "grab, swipe left", "name": "Go forward",
     "pose": "fist", "motion": "left",
     "lit": ("index", "middle", "ring", "pinky", "thumb"),
     "big": "Same grab,\nswipe left",
     "sub": "The other way goes forward again, back to the\npage you just left.",
     "ok": "Done — you went forward again",
     "tip": "Keep the fist closed through the whole stroke.",
     "stage": "swipe", "stage_label": "going forward again",
     "done": lambda s: s["forward"] != 0},

    {"key": "minimize", "hand": "grab, then pull down", "name": "Minimise", "pose": "fist", "motion": "down",
     "lit": ("index", "middle", "ring", "pinky", "thumb"),
     "big": "Grab, then pull\nstraight down",
     "sub": "A short, definite tug is enough. It fires as soon\nas the pull is clear.",
     "ok": "Done — the window is minimised",
     "tip": "The closed fist is what stops a dropped arm minimising things.",
     "stage": "minimize", "stage_label": "a window being minimised",
     "done": lambda s: s["minimised"]},
]


class Tutorial:
    """The walkthrough window. Owns the camera for as long as it is open."""

    def __init__(self, root, cfg, accent=None):
        self.root = root
        self.cfg = cfg
        self.accent = accent or ACCENTS[0][1]
        self.i = 0
        self.completed = False
        self.satisfied = [False] * len(STEPS)
        self._last = None
        self._tick_job = None
        self.enter = 0.0             # 0..1 slide-in for a freshly shown step

        self.mouse = NullMouse()
        # Build the controller the SAME way the tracker does, from config.
        # Constructing it bare gave it the constructor's own defaults — a
        # cursor at a third of the real speed, the old 1.2s high-five engage
        # and a longer swipe — so the walkthrough taught gestures that felt
        # nothing like the app it was introducing, and every tuning change
        # silently missed it.
        try:
            from airmouse import make_controller
            self.ctrl = make_controller(self.mouse, cfg)
        except Exception:
            self.ctrl = GestureController(self.mouse)
        self.roles = RoleAssigner(dominant=cfg.get("dominant_hand", "right"))
        self.diagram = HandDiagram(self.accent)
        self.stage = DemoStage(self.accent)
        self._photo = None
        self._seen = self._blank_counts()

        self.cur = [W * 0.52, H * 0.55]      # the fake pointer
        self.press = 0.0
        self._drag_from = None
        self._done_at = None
        self._now = time.perf_counter()      # the clock the drawing reads

        self.cap = None
        self.tracker = None
        self.cam_msg = "Your camera feed will appear here"

        self._build()
        self._open_camera()
        self._show_step()
        self._tick_job = self.root.after(16, self._tick)

    @staticmethod
    def _blank_counts():
        return {"clicks": 0, "rclicks": 0, "volume": 0, "nav": 0,
                "back": 0, "forward": 0, "minimised": 0}

    # -- window ------------------------------------------------------------
    def _build(self):
        r = self.root
        init_fonts(r)                 # FONTS is empty until this runs
        self.F = big_fonts()
        # Real font objects, kept because they are measured every frame: a
        # label centred by eye drifts the moment its text changes.
        self._btn_font = tkfont.Font(root=r, font=self.F["btn"])
        r.title("AirMouse — getting started")
        r.configure(bg=BG)
        r.resizable(False, False)
        r.protocol("WM_DELETE_WINDOW", self._skip_all)
        self.cv = tk.Canvas(r, width=W, height=H, bg=BG,
                            highlightthickness=0, bd=0)
        self.cv.pack()
        # Real mouse and keyboard stay live as an escape hatch: someone whose
        # camera is busy, or who cannot make a gesture, must never be trapped.
        self.cv.bind("<Button-1>", lambda e: self._click_at(e.x, e.y))
        r.bind("<Return>", lambda e: self._next())
        r.bind("<space>", lambda e: self._next())
        r.bind("<Escape>", lambda e: self._skip_all())

    # -- camera ------------------------------------------------------------
    def _open_camera(self):
        try:
            from airmouse import open_camera        # lazy: avoids a cycle
            self.cap = open_camera(self.cfg.get("camera_index", 0))
            if not self.cap.isOpened():
                self.cap = None
        except Exception:
            self.cap = None
        try:
            self.tracker = HandTracker(HAND_MODEL)
        except Exception:
            self.tracker = None
        if self.cap is None or self.tracker is None:
            self.cam_msg = ("Another app is using your webcam.\nClose Discord, "
                            "Zoom or Teams and start again.\n\nYou can still "
                            "click Next with your mouse.")

    def close(self):
        """Release the webcam. The tracker cannot start until this happens."""
        if self._tick_job is not None:
            try:
                self.root.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        for obj, meth in ((self.cap, "release"), (self.tracker, "close")):
            try:
                if obj is not None:
                    getattr(obj, meth)()
            except Exception:
                pass
        self.cap = self.tracker = None

    # -- steps -------------------------------------------------------------
    def _show_step(self):
        st = STEPS[self.i]
        self.diagram.set_pose(st["pose"], st["lit"], st.get("motion"))
        self.stage.set_mode(st.get("stage", "none"), st.get("stage_label", ""),
                            "back" if st.get("motion") == "right"
                            else "forward")
        self._seen = self._blank_counts()
        self._done_at = None          # when this step was satisfied
        self.enter = 1.0              # drives the slide-and-fade in

    def _next(self):
        if self.i + 1 >= len(STEPS):
            self._finish(True)
            return
        self.i += 1
        self._show_step()

    def _skip_all(self):
        self._finish(True)

    def _finish(self, done):
        self.completed = done
        self.close()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _over_stage(self, x, y):
        return _inside(STAGE, x, y)

    def _drag_window(self, info):
        """Let the pretend window be dragged by its title bar.

        Uses the same held-pinch the real drag uses, so this is practice
        rather than a mime: grab the bar, move, let go. The window is clamped
        inside its panel so it cannot be lost off the edge."""
        stage = self.stage
        if stage.mode != "click" or not stage.pressed:
            stage.dragging = False
            return
        held = info.get("left_owner") == "pinch"
        cx = STAGE[0] + STAGE[2] / 2 + stage.win[0]
        cy = STAGE[1] + STAGE[3] / 2 + 14 + stage.win[1]
        x, y = self.cur
        on_bar = abs(x - cx) <= 66 and abs(y - (cy - 26)) <= 16
        if held and (stage.dragging or on_bar):
            if self._drag_from is not None:
                stage.win[0] += x - self._drag_from[0]
                stage.win[1] += y - self._drag_from[1]
                stage.win[0] = max(-120, min(120, stage.win[0]))
                # not far enough down to reach the instruction under it
                stage.win[1] = max(-40, min(24, stage.win[1]))
            stage.dragging = True
            self._drag_from = [x, y]
        else:
            stage.dragging = False
            self._drag_from = [x, y] if held else None

    def _maybe_auto(self, now):
        """Advance on a timer, but ONLY where a step asks for it.

        Auto-advance is otherwise banned here: jumping the moment a gesture
        registered left no time to notice what had happened. Two steps opt in
        anyway, for reasons that outweigh that. Step one must, because you
        cannot press Next before you have been taught to click. And the
        back/forward pair must, because they are one gesture split in two and
        stopping between them to press a button breaks the rhythm.
        """
        after = STEPS[self.i].get("auto_after")
        if (after and self.satisfied[self.i] and self._done_at is not None
                and now - self._done_at >= after):
            self._done_at = None
            self._next()

    def _rail_hit(self, x, y):
        """Which sidebar row is under the point, or None."""
        if x > RAIL:
            return None
        for n in range(len(STEPS)):
            if abs(y - (RAIL_TOP + n * RAIL_GAP)) <= RAIL_GAP / 2 - 2:
                return n
        return None

    def _click_at(self, x, y):
        """A click landed at (x, y) — fake cursor or real mouse."""
        if _inside(NEXT_BTN, x, y):
            self._next()
            return True
        if _inside(SKIP_BTN, x, y):
            self._skip_all()
            return True
        # Going BACK is always allowed; going forward is not. Revisiting a
        # gesture you have already done is exactly what someone does when
        # they half-remember it, and making them restart the walkthrough for
        # that would be absurd. Skipping ahead would let you land on a step
        # whose prerequisites you never learned.
        row = self._rail_hit(x, y)
        if row is not None and row < self.i:
            self.i = row
            self._show_step()
            return True
        return False

    # -- per-frame ---------------------------------------------------------
    def _state(self, info):
        """Collapse a controller frame into the flags the steps test, and
        drive the demo stage from the same events."""
        stage = self.stage
        for e in self.mouse.events:
            if e[0] == "move":
                self.cur[0] = min(W - 4, max(4, self.cur[0] + e[1]))
                self.cur[1] = min(H - 4, max(4, self.cur[1] + e[2]))
            elif e[0] == "down":
                self._seen["clicks"] += 1
                self.press = 1.0
                stage.flash = 1.0
                if stage.mode == "click" and not stage.pressed \
                        and self._over_stage(*self.cur):
                    stage.pressed = True       # button hit -> becomes a window
                self._click_at(*self.cur)
            elif e[0] == "rdown":
                self._seen["rclicks"] += 1
                stage.flash = 1.0
            elif e[0] == "volume":
                self._seen["volume"] += e[1]
                stage.level = min(1.0, max(0.0, stage.level + e[1] * 0.04))
            elif e[0] == "wheel":
                stage.level += e[1] / 24.0
            elif e[0] in ("back", "forward"):
                self._seen["nav"] += 1
                self._seen[e[0]] += 1
                stage.slide = 1.0 if e[0] == "back" else -1.0
            elif e[0] == "minimize":
                self._seen["minimised"] += 1
                stage.gone = True
        self.mouse.events.clear()
        if stage.mode == "brake":
            stage.level = info.get("brake_scale", 1.0)
        self._drag_window(info)
        return {"engaged": info["state"] not in (IDLE, ENGAGING),
                "clicks": self._seen["clicks"],
                "rclicks": self._seen["rclicks"],
                "volume": self._seen["volume"],
                "brake": info.get("brake", 0.0),
                "armed": bool(info.get("fist_armed")),
                "scrolling": info.get("mode") == SCROLL,
                "nav": self._seen["nav"],
                "back": self._seen["back"],
                "forward": self._seen["forward"],
                "minimised": self._seen["minimised"]}

    def _tick(self):
        now = time.perf_counter()
        dt = 0.033 if self._last is None else min(0.1, now - self._last)
        self._last = self._now = now

        frame = info = None
        if self.cap is not None and self.tracker is not None:
            ok, raw = self.cap.read()
            if ok and raw is not None:
                frame = cv2.flip(raw, 1)
                h, w = frame.shape[:2]
                self.tracker.detect_async(cv2.cvtColor(frame,
                                                       cv2.COLOR_BGR2RGB))
                hands = [{"pts": to_pixel_points(hd["landmarks"], w, h),
                          "label": hd["label"], "score": hd["score"]}
                         for hd in self.tracker.latest_hands()]
                roles = self.roles.assign(hands, w, now)
                cursor = roles["cursor"]
                for hd in hands:
                    draw_landmarks(frame, hd["pts"],
                                   color=(0, 200, 0) if hd["pts"] is cursor
                                   else (150, 150, 150))
                info = self.ctrl.update(cursor, (w, h), now, attending=True,
                                        suspend=cursor is None and bool(hands))
                self._pinch_bubble(frame, cursor, info)
                self.cam_msg = ("" if cursor is not None else
                                "Hold your right hand up where\nthe camera "
                                "can see it")

        if info is not None:
            state = self._state(info)
            if not self.satisfied[self.i] and STEPS[self.i]["done"](state):
                self.satisfied[self.i] = True
                self._done_at = now
                # acknowledge it in the middle of the screen too, where the
                # eyes actually are
                self.diagram.flash = 1.0
        self._maybe_auto(now)

        self.press = max(0.0, self.press - dt * 2.6)
        self.enter = max(0.0, self.enter - dt * 3.2)
        self.diagram.tick(dt)
        self.stage.tick(dt)
        self._render(frame)
        try:
            self._tick_job = self.root.after(16, self._tick)
        except Exception:
            self._tick_job = None

    def _pinch_bubble(self, frame, pts, info):
        """Draw a bubble where this step's two fingertips meet.

        Landmarks alone don't say how close is close enough — the ring grows
        as the gap closes and snaps solid the moment the pinch registers, so
        "nearly" and "done" look different."""
        tips = PINCH_TIPS.get(STEPS[self.i]["key"], PINCH_TIPS["next"])
        if pts is None:
            return
        # cv2 wants integer points and will not coerce them itself.
        ax, ay = int(pts[tips[0]][0]), int(pts[tips[0]][1])
        bx, by = int(pts[tips[1]][0]), int(pts[tips[1]][1])
        mx, my = (ax + bx) // 2, (ay + by) // 2
        gap = math.hypot(ax - bx, ay - by)
        span = max(1e-6, math.hypot(pts[0][0] - pts[9][0],
                                    pts[0][1] - pts[9][1]))
        close = max(0.0, min(1.0, 1.0 - (gap / span) / 0.55))
        held = (info.get("left_owner") == "pinch" or info.get("right_down")
                or info.get("volume_down"))
        cv2.line(frame, (ax, ay), (bx, by),
                 (120, 230, 160) if held else (90, 190, 250), 2, cv2.LINE_AA)
        if held:
            cv2.circle(frame, (mx, my), 26, (120, 230, 160), -1, cv2.LINE_AA)
            cv2.circle(frame, (mx, my), 34, (120, 230, 160), 2, cv2.LINE_AA)
        elif close > 0.05:
            r = int(10 + 16 * close)
            cv2.circle(frame, (mx, my), r, (90, 190, 250),
                       max(1, int(1 + 2 * close)), cv2.LINE_AA)

    # -- drawing -----------------------------------------------------------
    def _render(self, frame):
        cv = self.cv
        cv.delete("all")
        st = STEPS[self.i]
        done = self.satisfied[self.i]
        F = self.F
        # everything in the main column eases in together when a step changes
        slide = 26 * self.enter * self.enter

        self._draw_rail(cv, F)

        x, y, w, h = PILL
        cv.create_polygon(round_pts(x + slide, y, x + w + slide, y + h, h / 2),
                          smooth=True, fill=hex_lerp(BG, self.accent, 0.18),
                          outline="")
        cv.create_text(x + w / 2 + slide, y + h / 2,
                       text=f"STEP {self.i + 1} OF {len(STEPS)}",
                       fill=self.accent, font=F["micro"])

        cv.create_text(MX + slide, HEAD_Y, text=st["big"], anchor="nw",
                       fill=TEXT, font=F["huge"], justify="left")
        cv.create_text(MX + slide, SUB_Y, text=st["sub"], anchor="nw",
                       fill=DIM, font=F["sub"], justify="left")
        if done:
            self._draw_ok(cv, st, slide, F)

        self.diagram.draw(cv, DIAG, F)

        self._draw_camera(cv, frame, F)
        # An empty panel reads as something failing to load, so the stage only
        # appears on steps that actually demonstrate something.
        if self.stage.mode != "none":
            _card(cv, STAGE, CARD)
            self.stage.draw(cv, STAGE, F)

        # A footer rule closes the composition and gives the buttons a band to
        # sit in. Without it they float in the corner of an empty bottom third.
        cv.create_line(MX, FOOT_Y, W - 44, FOOT_Y, fill=LINE)
        self._draw_buttons(cv, done, F)
        self._draw_cursor(cv)

    def _draw_ok(self, cv, st, slide, F):
        """"Yes, that was it" — said where the eyes already are.

        Everything that used to change on success was in a corner: a pip in
        the sidebar, the fill of a button 700px away. The centre and upper
        left of the screen — the headline, the subtitle, the hand, the demo
        panel — were pixel-identical before and after, so the one moment the
        walkthrough exists to produce was invisible. This lands directly
        under the subtitle, at button size, and slides in rather than
        appearing, because arriving is what catches an eye that is elsewhere.
        """
        a = 1.0
        if self._done_at is not None:
            a = min(1.0, max(0.0, (self._now - self._done_at) * 4.0))
        x = MX + slide + 16 * (1.0 - a)
        col = hex_lerp(BG, OK, 0.25 + 0.75 * a)
        cv.create_oval(x, OK_Y - 11, x + 22, OK_Y + 11, outline=col, width=2)
        cv.create_line(x + 6, OK_Y, x + 10, OK_Y + 5, x + 16, OK_Y - 6,
                       fill=col, width=2, capstyle=tk.ROUND,
                       joinstyle=tk.ROUND)
        cv.create_text(x + 34, OK_Y, text=st.get("ok", "Done"), anchor="w",
                       fill=col, font=F["btn"])

    def _draw_rail(self, cv, F):
        cv.create_rectangle(0, 0, RAIL, H, fill=SIDEBAR, outline="")
        cv.create_line(RAIL, 0, RAIL, H, fill=LINE)

        # brand: a little open hand, not four bars — the first pass read as
        # a barcode
        cv.create_polygon(round_pts(28, 30, 66, 68, 12), smooth=True,
                          fill=hex_lerp(SIDEBAR, self.accent, 0.22),
                          outline="")
        for fx, top in ((40, 45), (45, 41), (50, 41), (55, 45)):
            cv.create_line(fx, 55, fx, top, fill=self.accent, width=3,
                           capstyle=tk.ROUND)
        cv.create_line(36, 56, 40, 50, fill=self.accent, width=3,
                       capstyle=tk.ROUND)                     # thumb
        cv.create_polygon(round_pts(38, 52, 57, 61, 4), smooth=True,
                          fill=self.accent, outline="")       # palm
        cv.create_text(80, 49, text="AirMouse", anchor="w", fill=TEXT,
                       font=F["brand"])
        cv.create_line(28, 88, RAIL - 28, 88, fill=LINE)

        top, gap = RAIL_TOP, RAIL_GAP
        for n, st in enumerate(STEPS):
            cy = top + n * gap
            done = self.satisfied[n]
            here = n == self.i
            back = n < self.i
            if back and _inside((0, cy - gap / 2, RAIL, gap), *self.cur):
                # a completed step you can click your way back to
                cv.create_polygon(round_pts(20, cy - 22, RAIL - 20, cy + 22,
                                            R_CARD), smooth=True,
                                  fill=hex_lerp(SIDEBAR, CARD, 0.9),
                                  outline="")
            if n < len(STEPS) - 1:                # connector
                cv.create_line(46, cy + 13, 46, cy + gap - 13,
                               fill=OK if done else LINE, width=2)
            # WHERE YOU ARE and WHAT YOU HAVE DONE are two different things.
            # Drawing them as one style meant that the moment a step was
            # satisfied it became indistinguishable from the ones behind it —
            # the highlight vanished exactly when the user looked for it.
            if here:
                cv.create_polygon(round_pts(28, cy - 21, RAIL - 28, cy + 21,
                                            R_CARD), smooth=True,
                                  fill=hex_lerp(SIDEBAR, self.accent, 0.09),
                                  outline="")
                cv.create_line(30, cy - 15, 30, cy + 15, fill=self.accent,
                               width=3, capstyle=tk.ROUND)
            if done:
                fill, ring, num = OK, OK, BG
            elif here:
                fill, ring, num = self.accent, self.accent, BG
            else:
                fill, ring, num = SIDEBAR, LINE, DIM
            cv.create_oval(35, cy - 11, 57, cy + 11, fill=fill, outline=ring,
                           width=2)
            if here and done:                     # ticked, and still the one
                cv.create_oval(31, cy - 15, 61, cy + 15, outline=self.accent,
                               width=2)
            if done:
                cv.create_line(41, cy, 45, cy + 5, 52, cy - 6, fill=BG,
                               width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)
            else:
                cv.create_text(46, cy, text=str(n + 1), fill=num,
                               font=F["rail_dim"])
            cv.create_text(70, cy - 8, text=st["name"], anchor="w",
                           fill=TEXT if (here or done) else DIM,
                           font=F["rail"] if here else F["rail_dim"])
            # the pose in three words — the rail doubles as a cheat sheet you
            # can glance at without leaving the step you are on
            cv.create_text(70, cy + 10, text=st["hand"], anchor="w",
                           fill=hex_lerp(SIDEBAR, DIM, 0.75 if here else 0.5),
                           font=F["count"])

        # TIP CARD, sized to its own text and hung from the bottom margin the
        # Next button shares. A fixed box left half the card empty on most
        # steps, which makes the sidebar look unfinished rather than airy.
        tip = STEPS[self.i]["tip"]
        tx, tw = TIP_X, TIP_W
        probe = cv.create_text(-9999, -9999, text=tip, anchor="nw",
                               font=F["tip"], width=tw - 32, justify="left")
        box = cv.bbox(probe)
        cv.delete(probe)
        text_h = (box[3] - box[1]) if box else 34
        ty = BOTTOM - (44 + text_h + 20)
        cv.create_polygon(round_pts(tx, ty, tx + tw, BOTTOM, R_PANEL),
                          smooth=True,
                          fill=hex_lerp(SIDEBAR, self.accent, 0.07),
                          outline=hex_lerp(SIDEBAR, self.accent, 0.3), width=1)
        cv.create_oval(tx + 16, ty + 16, tx + 30, ty + 30, outline=self.accent,
                       width=2)
        cv.create_line(tx + 23, ty + 20, tx + 23, ty + 26, fill=self.accent,
                       width=2, capstyle=tk.ROUND)
        cv.create_text(tx + 38, ty + 23, text="TIP", anchor="w",
                       fill=self.accent, font=F["micro"])
        cv.create_text(tx + 16, ty + 44, text=tip, anchor="nw",
                       fill=DIM, font=F["tip"], width=tw - 32, justify="left")

    def _draw_camera(self, cv, frame, F):
        x, y, w, h = CAM
        _card(cv, CAM, PANEL)
        if frame is not None:
            small = cv2.resize(frame, (w - 20, h - 20))
            img = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            self._photo = ImageTk.PhotoImage(img)   # keep a ref or it blanks
            cv.create_image(x + 10, y + 10, image=self._photo, anchor="nw")
        if self.cam_msg:
            cv.create_text(x + w / 2, y + h / 2, text=self.cam_msg, fill=DIM,
                           font=F["sub"], width=w - 70, justify="center")
        # viewfinder brackets — says "this is a camera" without a caption
        for sx, sy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            cx = x + 14 + (w - 28) * sx
            cy = y + 14 + (h - 28) * sy
            dx, dy = (1 if sx == 0 else -1), (1 if sy == 0 else -1)
            cv.create_line(cx, cy, cx + dx * 22, cy, fill=self.accent, width=2)
            cv.create_line(cx, cy, cx, cy + dy * 22, fill=self.accent, width=2)

    def _draw_buttons(self, cv, done, F):
        x, y, w, h = NEXT_BTN
        over = _inside(NEXT_BTN, *self.cur)
        # Next is ALWAYS clickable — it just lights up once the gesture has
        # been done. Gating it would trap anyone the tracker cannot read.
        base = self.accent if done else CARD
        fill = hex_lerp(base, "#ffffff", 0.14 if over else 0.0)
        fill = hex_lerp(fill, "#000000", 0.2 * self.press if over else 0.0)
        cv.create_polygon(round_pts(x, y, x + w, y + h, R_CARD), smooth=True,
                          fill=fill, outline=self.accent if done else LINE,
                          width=2)
        # A STEP THAT LEAVES ON ITS OWN HAS TO SAY SO. Two steps advance on a
        # timer, and a screen that simply changes under you is the complaint
        # that got auto-advance banned everywhere else. A depleting bar
        # across the button turns the same three seconds into a countdown.
        st = STEPS[self.i]
        after = st.get("auto_after")
        left = 0.0
        if done and after and self._done_at is not None:
            left = max(0.0, 1.0 - (self._now - self._done_at) / after)
            if left > 0:
                cv.create_line(x + 12, y + h - 9, x + 12 + (w - 24) * left,
                               y + h - 9, width=4, capstyle=tk.ROUND,
                               fill=hex_lerp(base, "#000000", 0.4))
        last = self.i + 1 >= len(STEPS)
        label = "Finish" if last else "Next step"
        fg = BG if done else TEXT
        # Measure the label and lay the pair out as one group. Hard-coded
        # offsets suited "Next step" and left "Finish" — the one button the
        # walkthrough ends on — off-centre with a hole before its arrow.
        lw = self._btn_font.measure(label)
        start = x + w / 2 - (lw + 14 + 16) / 2
        cy = y + h / 2
        cv.create_text(start, cy, text=label, anchor="w", fill=fg,
                       font=F["btn"])
        ax = start + lw + 14
        cv.create_line(ax, cy, ax + 16, cy, fill=fg, width=2,
                       capstyle=tk.ROUND, arrow=tk.LAST, arrowshape=(8, 10, 4))
        # REPLACE the hint, never just delete it: the slot losing its text at
        # the exact instant the region should be gaining information is the
        # opposite of feedback.
        if not done:
            hint, tint = "try it, or skip ahead", DIM
        elif after:
            hint, tint = "moving on…", DIM
        else:
            hint, tint = "Nice — press Next", OK
        cv.create_text(x + w / 2, y - 18, text=hint, fill=tint,
                       font=F["count"])

        sx, sy, sw, sh = SKIP_BTN
        cv.create_polygon(round_pts(sx, sy, sx + sw, sy + sh, R_CARD),
                          smooth=True,
                          fill=hex_lerp(BG, CARD, 1.0 if
                                        _inside(SKIP_BTN, *self.cur) else 0.0),
                          outline=LINE, width=1)
        cv.create_text(sx + sw / 2, sy + sh / 2, text="Skip all", fill=DIM,
                       font=F["count"])

    def _draw_cursor(self, cv):
        """The pointer, drawn last so it floats over everything."""
        x, y = self.cur
        s = 1.0 + 0.22 * self.press
        if self.press > 0:
            rr = 10 + 20 * (1.0 - self.press)
            cv.create_oval(x - rr, y - rr, x + rr, y + rr, width=2,
                           outline=hex_lerp(self.accent, BG, 1 - self.press))
        cv.create_polygon(x, y, x, y + 21 * s, x + 6 * s, y + 15 * s,
                          x + 10 * s, y + 23 * s, x + 14 * s, y + 21 * s,
                          x + 10 * s, y + 13 * s, x + 16 * s, y + 12 * s,
                          fill="#ffffff", outline=BG, width=2)


def _card(cv, box, fill=CARD):
    x, y, w, h = box
    cv.create_polygon(round_pts(x, y, x + w, y + h, R_PANEL), smooth=True,
                      fill=fill, outline=LINE, width=1)


def _inside(box, x, y):
    bx, by, bw, bh = box
    return bx <= x <= bx + bw and by <= y <= by + bh


def run(cfg, accent=None):
    """Show the walkthrough. Returns True once it has been seen through or
    deliberately skipped, False if it could not open at all.

    The camera is released before this returns, so the caller is free to
    start the tracker immediately afterwards.
    """
    try:
        root = tk.Tk()
    except Exception:
        return False
    tut = Tutorial(root, cfg, accent)
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{max(0, (sw - W) // 2)}+{max(0, (sh - H) // 2 - 20)}")
    root.attributes("-topmost", True)
    try:
        root.mainloop()
    finally:
        tut.close()
    return tut.completed
