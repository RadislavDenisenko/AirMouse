"""AirMouse settings — the real app (DESIGN.md v2).

Eight sections, one window: Pointer, Scrolling, Gestures, Magnet, Lens,
Launcher, Attention, Camera. Every control is generated from the manifest in
settings_store.SECTIONS, so the UI can only ever offer settings that map to
real config.json keys, and every write is atomic and preserves keys this app
doesn't own (the tracker stores its calibration in the same file).

Look and motion come from settings_ui.py — the shared widget kit. Nothing
here re-invents a control.

Run: run_settings.bat, the B key in AirMouse, or
venv\\Scripts\\python.exe settings_app.py
"""

import os
import sys
import threading
import tkinter as tk

import app_index
import settings_store as store
from config_defaults import APP_VERSION
from settings_ui import (ACCENTS, DEFAULT_ACCENT, BG, PANEL, CARD, CARD_HOVER,
                         LINE, TEXT, DIM, OK, WARN, FONTS, Animator,
                         MagnetToggle, PillButton, RichCaption, Slider, Toggle,
                         hex_lerp, init_fonts, round_pts, surface_photo)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SLOT_NAMES = ("1 finger up", "2 fingers up", "3 fingers up", "4 fingers up")


class SettingsApp:
    CONTENT_W = 600
    SIDEBAR_W = 190
    HEADER_H = 54
    FOOTER_H = 32
    CARD_PAD = 12

    def __init__(self, root):
        self.root = root
        self.anim = Animator(root)
        self.accent_i = DEFAULT_ACCENT
        self.cfg = store.load()
        self.section = "Pointer"
        self._pending = {}          # dotted -> value, flushed on a debounce
        self._save_job = None
        self._hold_job = None
        self._apps = []             # Start Menu scan result (worker thread)
        self._apps_ready = False

        root.title(f"AirMouse settings - v{APP_VERSION} beta")
        root.configure(bg=BG)
        root.geometry("880x660")
        root.minsize(780, 560)

        self._build_header()
        self._build_sidebar()
        self.footer = self._build_footer()
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(side="top", fill="both", expand=True)
        self._build_section()
        self._scan_apps_async()
        self._register_drop()

    # ------------------------------------------------------------ plumbing ---
    @property
    def accent(self):
        return ACCENTS[self.accent_i][1]

    @property
    def accent_soft(self):
        return ACCENTS[self.accent_i][2]

    def _value(self, ctrl):
        """Current value of a control (derived controls included)."""
        if ctrl.get("derived"):
            return store.read_derived(self.cfg, ctrl["key"])
        return store.get_in(self.cfg, ctrl["key"],
                            store.default_for(ctrl["key"]))

    def _set(self, ctrl, value):
        """Update the in-memory config and queue a debounced atomic write."""
        key = ctrl["key"]
        if ctrl.get("derived"):
            updates = store.write_derived(key, value)
        else:
            updates = {key: value}
        for dotted, val in updates.items():
            store.set_in(self.cfg, dotted, val)
        self._pending.update(updates)
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(300, self._flush)

    def _flush(self):
        self._save_job = None
        if not self._pending:
            return
        updates, self._pending = self._pending, {}
        ok = store.save_updates(updates)
        live = all(store.is_live(k) for k in updates)
        if not ok:
            self.flash("Could not write config.json", tone=WARN)
        elif live:
            self.flash("Saved - live in the app")
        else:
            self.flash("Saved - restart AirMouse to apply")

    # -------------------------------------------------------------- header ---
    def _build_header(self):
        cv = tk.Canvas(self.root, height=self.HEADER_H, bg=PANEL,
                       highlightthickness=0)
        cv.pack(side="top", fill="x")
        self.header = cv
        cv.bind("<Configure>", lambda _e: self._paint_header())
        self._paint_header()

    def _paint_header(self):
        cv = self.header
        cv.delete("all")
        w = max(780, cv.winfo_width())
        mx, my = 26, self.HEADER_H / 2 + 3
        cv.create_arc(mx - 9, my - 6, mx + 9, my + 10, start=180, extent=180,
                      style="arc", outline=self.accent, width=2)
        for dx, tall in ((-5, 8), (0, 11), (5, 8)):
            cv.create_line(mx + dx, my - 2, mx + dx, my - 2 - tall,
                           fill=self.accent, width=2, capstyle="round")
        cv.create_text(46, self.HEADER_H / 2, anchor="w", text="AirMouse",
                       font=FONTS["app"], fill=TEXT)
        # honest badge: this is a beta and the UI should say so
        bx = 46 + 90
        cv.create_polygon(round_pts(bx, self.HEADER_H / 2 - 9, bx + 38,
                                    self.HEADER_H / 2 + 9, 9), smooth=True,
                          fill=hex_lerp(PANEL, WARN, 0.28), outline="")
        cv.create_text(bx + 19, self.HEADER_H / 2, text="beta",
                       font=FONTS["small"], fill=WARN)
        cv.create_text(w - 158, self.HEADER_H / 2, anchor="w", text="Accent",
                       font=FONTS["small"], fill=DIM)
        for i, (_n, hexv, _s) in enumerate(ACCENTS):
            x, y = w - 110 + i * 30, self.HEADER_H / 2
            ring = cv.create_oval(x - 11, y - 11, x + 11, y + 11, width=2,
                                  outline=TEXT if i == self.accent_i else "")
            dot = cv.create_oval(x - 8, y - 8, x + 8, y + 8, fill=hexv,
                                 outline="")
            for item in (ring, dot):
                cv.tag_bind(item, "<Button-1>",
                            lambda _e, idx=i: self._set_accent(idx))
        cv.create_line(0, self.HEADER_H - 1, w, self.HEADER_H - 1, fill=LINE)

    def _set_accent(self, i):
        if i == self.accent_i:
            return
        self.accent_i = i
        self._paint_header()
        self._paint_sidebar()
        self._build_section()

    # ------------------------------------------------------------- sidebar ---
    def _build_sidebar(self):
        cv = tk.Canvas(self.root, width=self.SIDEBAR_W, bg=PANEL,
                       highlightthickness=0)
        cv.pack(side="left", fill="y")
        self.sidebar = cv
        self._pill_y = self._section_y(self._index(self.section))
        self._paint_sidebar()
        cv.bind("<Button-1>", self._sidebar_click)

    @staticmethod
    def _index(name):
        return [s["name"] for s in store.SECTIONS].index(name)

    @staticmethod
    def _section_y(i):
        return 18 + i * 44

    def _paint_sidebar(self, pill_y=None):
        cv = self.sidebar
        cv.delete("all")
        if pill_y is not None:
            self._pill_y = pill_y
        cv.create_polygon(round_pts(10, self._pill_y, self.SIDEBAR_W - 10,
                                    self._pill_y + 36, 10), smooth=True,
                          fill=hex_lerp(PANEL, self.accent_soft, 0.55),
                          outline="")
        for i, sec in enumerate(store.SECTIONS):
            y = self._section_y(i) + 18
            sel = sec["name"] == self.section
            self._glyph(cv, sec["name"], 24, y, self.accent if sel else DIM)
            cv.create_text(48, y, anchor="w", text=sec["name"],
                           font=FONTS["nav"], fill=TEXT if sel else DIM)
        cv.create_line(self.SIDEBAR_W - 1, 0, self.SIDEBAR_W - 1, 3000,
                       fill=LINE)

    def _sidebar_click(self, e):
        for i, sec in enumerate(store.SECTIONS):
            y = self._section_y(i)
            if y <= e.y <= y + 40 and sec["name"] != self.section:
                self.section = sec["name"]
                self.anim.run("navpill", self._pill_y, y, 160,
                              lambda v: self._paint_sidebar(pill_y=v))
                self._build_section()
                return

    @staticmethod
    def _glyph(cv, name, x, y, c):
        k = {"width": 2, "fill": c, "capstyle": "round"}
        if name == "Pointer":
            cv.create_line(x - 5, y + 6, x + 4, y - 5, **k)
            cv.create_line(x + 4, y - 5, x - 1, y - 4, **k)
            cv.create_line(x + 4, y - 5, x + 3, y + 1, **k)
        elif name == "Scrolling":
            cv.create_line(x - 5, y - 2, x, y - 7, x + 5, y - 2, **k)
            cv.create_line(x - 5, y + 2, x, y + 7, x + 5, y + 2, **k)
        elif name == "Gestures":
            cv.create_line(x - 5, y + 6, x - 5, y - 2, **k)
            cv.create_line(x, y + 6, x, y - 6, **k)
            cv.create_line(x + 5, y + 6, x + 5, y - 3, **k)
        elif name == "Magnet":
            cv.create_arc(x - 6, y - 4, x + 6, y + 8, start=180, extent=180,
                          style="arc", outline=c, width=2)
            cv.create_line(x - 6, y + 2, x - 6, y - 6, **k)
            cv.create_line(x + 6, y + 2, x + 6, y - 6, **k)
        elif name == "Lens":
            cv.create_oval(x - 7, y - 7, x + 3, y + 3, outline=c, width=2)
            cv.create_line(x + 3, y + 3, x + 7, y + 7, **k)
        elif name == "Launcher":
            cv.create_line(x, y - 7, x - 5, y + 6, **k)
            cv.create_line(x, y - 7, x + 5, y + 6, **k)
            cv.create_line(x - 5, y + 6, x + 5, y + 6, **k)
        elif name == "Attention":
            cv.create_oval(x - 7, y - 4, x + 7, y + 4, outline=c, width=2)
            cv.create_oval(x - 2, y - 2, x + 2, y + 2, fill=c, outline="")
        elif name == "Camera":
            cv.create_rectangle(x - 7, y - 4, x + 7, y + 6, outline=c, width=2)
            cv.create_oval(x - 3, y - 2, x + 3, y + 4, outline=c, width=2)
            cv.create_line(x - 3, y - 4, x - 1, y - 7, x + 3, y - 7, **k)

    # -------------------------------------------------------------- footer ---
    def _build_footer(self):
        cv = tk.Canvas(self.root, height=self.FOOTER_H, bg=BG,
                       highlightthickness=0)
        cv.pack(side="bottom", fill="x")
        cv.create_line(0, 0, 3000, 0, fill=LINE)
        return cv

    def flash(self, msg="Saved", tone=None):
        """Footer confirmation: slides up while fading in, a check draws
        itself, then it fades. Never a popup."""
        cv = self.footer
        tone = tone or OK
        st = {"a": 0.0, "c": 0.0}

        def paint():
            cv.delete("flash")
            a = st["a"]
            if a <= 0.01:
                return
            y = self.FOOTER_H / 2 + 4 * (1 - min(1.0, a))
            col = hex_lerp(BG, tone, a)
            p, x0 = st["c"], 16
            if p > 0:
                p1 = min(1.0, p / 0.4)
                cv.create_line(x0, y, x0 + 4 * p1, y + 4 * p1, width=2,
                               fill=col, capstyle="round", tags="flash")
            if p > 0.4:
                p2 = (p - 0.4) / 0.6
                cv.create_line(x0 + 4, y + 4, x0 + 4 + 7 * p2, y + 4 - 9 * p2,
                               width=2, fill=col, capstyle="round", tags="flash")
            cv.create_text(x0 + 20, y, anchor="w", text=msg,
                           font=FONTS["body"], fill=col, tags="flash")

        def set_a(v):
            st["a"] = v
            paint()

        def set_c(v):
            st["c"] = v
            paint()

        self.anim.run("flash_a", 0.0, 1.0, 160, set_a)
        self.anim.run("flash_c", 0.0, 1.0, 260, set_c)
        if self._hold_job is not None:
            self.root.after_cancel(self._hold_job)
        self._hold_job = self.root.after(
            2200, lambda: self.anim.run("flash_a", 1.0, 0.0, 500, set_a))

    # --------------------------------------------------------------- cards ---
    def _card(self, parent, height, title=None, hint=None, hint_reserve=0):
        """hint_reserve: pixels occupied by a control on the title row's
        right, so the hint is placed clear of it instead of underneath."""
        cv = tk.Canvas(parent, width=self.CONTENT_W, height=height, bg=BG,
                       highlightthickness=0)
        cv.pack(anchor="w", pady=(0, 6))
        cv._h = height
        cv._title, cv._hint = title, hint
        cv._hint_reserve = hint_reserve
        self._paint_surface(cv)
        return cv

    def _paint_surface(self, cv):
        cv.delete("surface")
        photo, pad = surface_photo(self.CONTENT_W - 2 * self.CARD_PAD,
                                   cv._h - 2 * self.CARD_PAD, CARD,
                                   radius=14, blur=9.0, shadow=0.5, offset=4)
        cv._surface = photo
        cv.create_image(self.CARD_PAD - pad, self.CARD_PAD - pad, anchor="nw",
                        image=photo, tags="surface")
        cv.tag_lower("surface")
        if cv._title:
            cv.create_text(self.CARD_PAD + 16, self.CARD_PAD + 14, anchor="w",
                           text=cv._title, font=FONTS["card"], fill=TEXT,
                           tags="surface")
        if cv._hint:
            cv.create_text(self.CONTENT_W - self.CARD_PAD - 16
                           - getattr(cv, "_hint_reserve", 0),
                           self.CARD_PAD + 14, anchor="e", text=cv._hint,
                           font=FONTS["small"], fill=DIM, tags="surface")

    def _caption(self, cv, y, markup, width=None):
        rich = RichCaption(cv, self.CARD_PAD + 16, y,
                           width or (self.CONTENT_W - 2 * self.CARD_PAD - 40),
                           markup, self.accent)
        h = max(cv._h, int(y + rich.height) + 14 + self.CARD_PAD)
        if h != cv._h:
            cv._h = h
            cv.configure(height=h)
            self._paint_surface(cv)
        return rich

    # ------------------------------------------------------------ sections ---
    def _build_section(self):
        self._slot_rows = []
        for child in self.body.winfo_children():
            child.destroy()
        sec = store.SECTIONS[self._index(self.section)]

        outer = tk.Frame(self.body, bg=BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        holder = tk.Frame(canvas, bg=BG)
        win = canvas.create_window(24, 12, anchor="nw", window=holder)

        def _fit(_e=None):
            # may fire from a deferred `after` once this section has already
            # been torn down by a nav change — that's not an error
            if not canvas.winfo_exists():
                return
            canvas.configure(scrollregion=canvas.bbox("all"))
        holder.bind("<Configure>", _fit)
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-e.delta // 120, "units"))

        tk.Label(holder, text=sec["name"], font=FONTS["section"], fg=TEXT,
                 bg=BG).pack(anchor="w", pady=(0, 2))
        intro = tk.Label(holder, text=sec["intro"], font=FONTS["body"],
                         fg=DIM, bg=BG, wraplength=self.CONTENT_W - 40,
                         justify="left")
        intro.pack(anchor="w", pady=(0, 8))
        if sec.get("pending"):
            tk.Label(holder, text=sec["pending"], font=FONTS["small"],
                     fg=WARN, bg=BG).pack(anchor="w", pady=(0, 6))

        advanced = []
        for ctrl in sec["controls"]:
            if ctrl.get("advanced"):
                advanced.append(ctrl)
            else:
                self._build_control(holder, ctrl)

        if advanced:
            self._build_expander(holder, advanced)

        PillButton(holder, self.anim, "Reset this section",
                   command=lambda s=sec: self._reset_section(s),
                   bg=BG).pack(anchor="w", pady=(4, 16))
        self.root.after(30, _fit)

    def _build_expander(self, holder, controls):
        """Collapsed 'More' row; the chevron rotates and the rows unfold."""
        box = tk.Frame(holder, bg=BG)
        box.pack(anchor="w", fill="x")
        head = tk.Canvas(box, width=self.CONTENT_W, height=30, bg=BG,
                         highlightthickness=0, cursor="hand2")
        head.pack(anchor="w")
        state = {"open": False, "rot": 0.0}
        inner = tk.Frame(box, bg=BG)

        def paint(rot):
            state["rot"] = rot
            head.delete("all")
            x, y = self.CARD_PAD + 18, 15
            import math as _m
            a = _m.radians(90 * rot)
            for sx, sy in ((-4, -3), (4, -3)):
                # a chevron that rotates from > to v
                rx = sx * _m.cos(a) - sy * _m.sin(a)
                ry = sx * _m.sin(a) + sy * _m.cos(a)
                head.create_line(x, y, x + rx, y + ry, width=2, fill=DIM,
                                 capstyle="round")
            head.create_text(x + 14, y, anchor="w",
                             text="Fewer options" if state["open"]
                             else "More options", font=FONTS["body"], fill=DIM)

        def toggle(_e=None):
            state["open"] = not state["open"]
            if state["open"]:
                if not inner.winfo_children():
                    for c in controls:
                        self._build_control(inner, c)
                inner.pack(anchor="w", fill="x")
            else:
                inner.pack_forget()
            self.anim.run(("exp", id(box)), state["rot"],
                          1.0 if state["open"] else 0.0, 140, paint)

        head.bind("<Button-1>", toggle)
        paint(0.0)

    def _build_control(self, holder, ctrl):
        kind = ctrl["kind"]
        P = self.CARD_PAD
        if kind == "note":
            cv = self._card(holder, 74, title=ctrl["label"])
            self._caption(cv, P + 34, ctrl["caption"])
            return
        if kind == "slots":
            self._build_slots(holder, ctrl)
            return

        key = ctrl.get("key", "")
        dflt = store.default_for(key) if not ctrl.get("derived") else None
        hint = None
        if kind == "slider" and dflt is not None:
            hint = f"default {ctrl.get('fmt', '{}').format(float(dflt))}"
        if key and not ctrl.get("derived") and not store.is_live(key):
            hint = (hint + " · after restart") if hint else "after restart"

        if kind == "toggle":
            cv = self._card(holder, 92, title=ctrl["label"], hint=hint,
                            hint_reserve=Toggle.W + 14)
            w = Toggle(cv, self.anim, self.accent, on=bool(self._value(ctrl)),
                       command=lambda v, c=ctrl: self._set(c, bool(v)))
            cv.create_window(self.CONTENT_W - P - 16, P + 20, anchor="e",
                             window=w)
            self._caption(cv, P + 44, ctrl["caption"])
        elif kind == "magnet_toggle":
            cv = self._card(holder, 130, title=ctrl["label"], hint=hint,
                            hint_reserve=MagnetToggle.W - 30)
            w = MagnetToggle(cv, self.anim, self.accent,
                             on=bool(self._value(ctrl)),
                             command=lambda v, c=ctrl: self._set(c, bool(v)))
            cv.create_window(self.CONTENT_W - P - 12, P + 34, anchor="e",
                             window=w)
            self._caption(cv, P + 70, ctrl["caption"])
        elif kind == "stepper":
            cv = self._card(holder, 92, title=ctrl["label"], hint=hint,
                            hint_reserve=126)
            self._build_stepper(cv, ctrl)
            self._caption(cv, P + 44, ctrl["caption"])
        else:  # slider
            cv = self._card(holder, 128, title=ctrl["label"], hint=hint)
            val = float(self._value(ctrl))
            s = Slider(cv, self.anim, self.accent,
                       width=self.CONTENT_W - 2 * P - 32,
                       lo=ctrl["lo"], hi=ctrl["hi"], value=val,
                       default=float(dflt) if dflt is not None else val,
                       fmt=ctrl.get("fmt", "{:.0f}"),
                       command=lambda v, c=ctrl: self._set(c, round(v, 3)))
            cv.create_window(P + 16, P + 26, anchor="nw", window=s)
            self._caption(cv, P + 72, ctrl["caption"])

    def _build_stepper(self, cv, ctrl):
        """Minus / value / plus — used where a slider would imply precision
        that doesn't exist (camera index)."""
        P = self.CARD_PAD
        state = {"v": int(self._value(ctrl) or 0)}
        box = tk.Canvas(cv, width=110, height=30, bg=CARD,
                        highlightthickness=0)
        cv.create_window(self.CONTENT_W - P - 16, P + 20, anchor="e",
                         window=box)

        def paint():
            box.delete("all")
            box.create_polygon(round_pts(0, 2, 28, 28, 8), smooth=True,
                               fill=CARD_HOVER, outline="")
            box.create_text(14, 15, text="-", font=FONTS["card"], fill=TEXT)
            box.create_text(55, 15, text=str(state["v"]), font=FONTS["card"],
                            fill=self.accent)
            box.create_polygon(round_pts(82, 2, 110, 28, 8), smooth=True,
                               fill=CARD_HOVER, outline="")
            box.create_text(96, 15, text="+", font=FONTS["card"], fill=TEXT)

        def click(e):
            lo, hi = int(ctrl["lo"]), int(ctrl["hi"])
            if e.x < 30:
                state["v"] = max(lo, state["v"] - 1)
            elif e.x > 80:
                state["v"] = min(hi, state["v"] + 1)
            else:
                return
            paint()
            self._set(ctrl, state["v"])

        box.bind("<Button-1>", click)
        paint()

    # ------------------------------------------------------ launcher slots ---
    def _build_slots(self, holder, ctrl):
        cv = self._card(holder, 210, title=ctrl["label"])
        P = self.CARD_PAD
        cmds, labels = store.load_slots()
        self._slot_rows = []

        for i, name in enumerate(SLOT_NAMES):
            y = P + 40 + i * 34
            cv.create_text(P + 16, y, anchor="w", text=name,
                           font=FONTS["body"], fill=DIM)
            shown = labels[i] or (app_index.friendly_label(cmds[i])
                                  if cmds[i] else "")
            cv.create_text(P + 120, y, anchor="w",
                           text=shown or "nothing bound", font=FONTS["body"],
                           fill=self.accent if shown else DIM,
                           tags=f"slot{i}")
            self._slot_rows.append((i, (cv, y)))
            PillButton(cv, self.anim, "Change",
                       command=lambda idx=i: self._pick_for_slot(idx),
                       bg=CARD).place(x=self.CONTENT_W - P - 176, y=y - 15)
            PillButton(cv, self.anim, "Clear",
                       command=lambda idx=i: self._clear_slot(idx),
                       bg=CARD).place(x=self.CONTENT_W - P - 92, y=y - 15)
        self._caption(cv, P + 40 + 4 * 34 - 6, ctrl["caption"])

    SOURCES = (("apps", "Installed apps"), ("presets", "Shortcuts"),
               ("running", "Running now"))

    def _pick_for_slot(self, idx):
        """Modal picker with three sources — installed apps, curated
        shortcuts (URIs that behave better than the .exe), and apps running
        right now — plus Browse and type-a-path fallbacks."""
        win = tk.Toplevel(self.root)
        win.title(f"Bind {SLOT_NAMES[idx]}")
        win.configure(bg=BG)
        win.geometry("560x560")
        win.transient(self.root)
        win.attributes("-topmost", True)

        tk.Label(win, text=f"What should {SLOT_NAMES[idx]} launch?",
                 font=FONTS["card"], fg=TEXT, bg=BG).pack(anchor="w", padx=16,
                                                          pady=(14, 6))
        state = {"source": "apps", "rows": []}

        tabs = tk.Frame(win, bg=BG)
        tabs.pack(anchor="w", padx=16)
        tab_btns = {}

        hint = tk.Label(win, text="", font=FONTS["body"], fg=DIM, bg=BG,
                        wraplength=520, justify="left")

        query = tk.Entry(win, font=FONTS["body"], bg=CARD, fg=TEXT,
                         insertbackground=TEXT, relief="flat",
                         highlightthickness=1, highlightbackground=LINE,
                         highlightcolor=self.accent)
        listbox = tk.Listbox(win, font=FONTS["body"], bg=CARD, fg=TEXT,
                             selectbackground=self.accent, selectforeground=BG,
                             relief="flat", highlightthickness=0,
                             activestyle="none", borderwidth=0)

        HINTS = {
            "apps": "Everything in your Start Menu. Double-click to bind.",
            "presets": "Shortcuts that open things the reliable way — Steam's "
                       "library opens even when Steam is already running.",
            "running": "Apps open right now. Binds the program itself, not "
                       "the window.",
        }

        def entries():
            q = query.get().strip()
            src = state["source"]
            if src == "apps":
                items = app_index.search(self._apps, q) if q else self._apps
                return [(a["name"], a["path"], "") for a in items[:400]]
            if src == "presets":
                ql = q.lower()
                return [(n, c, note) for (n, c, note) in app_index.PRESETS
                        if not ql or ql in n.lower()]
            wins = app_index.list_open_windows()
            items = app_index.search(wins, q) if q else wins
            return [(w["name"], w["path"], "") for w in items]

        def repopulate(*_a):
            state["rows"] = entries()
            listbox.delete(0, tk.END)
            for name, _cmd, note in state["rows"]:
                listbox.insert(tk.END, f"  {name}" +
                               (f"   -  {note}" if note else ""))
            if not state["rows"]:
                listbox.insert(tk.END, "  (still scanning...)"
                               if state["source"] == "apps"
                               and not self._apps_ready
                               else "  (nothing matches)")

        def choose(src):
            state["source"] = src
            for key, btn in tab_btns.items():
                btn.configure(bg=self.accent if key == src else CARD,
                              fg=BG if key == src else TEXT)
            hint.configure(text=HINTS[src])
            query.delete(0, tk.END)
            repopulate()
            query.focus_set()

        for key, text in self.SOURCES:
            b = tk.Button(tabs, text=text, relief="flat", bg=CARD, fg=TEXT,
                          font=FONTS["body"], padx=12, pady=4,
                          activebackground=CARD_HOVER, borderwidth=0,
                          command=lambda k=key: choose(k))
            b.pack(side="left", padx=(0, 6))
            tab_btns[key] = b

        hint.pack(anchor="w", padx=16, pady=(8, 6))
        query.pack(fill="x", padx=16)
        listbox.pack(fill="both", expand=True, padx=16, pady=10)

        def commit(*_a):
            sel = listbox.curselection()
            if not sel or sel[0] >= len(state["rows"]):
                return
            name, cmd, _note = state["rows"][sel[0]]
            self._set_slot(idx, cmd, name)
            win.destroy()

        def browse():
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                parent=win, title="Pick a program or file to launch",
                filetypes=[("Programs and shortcuts", "*.exe *.lnk *.bat *.cmd *.url"),
                           ("All files", "*.*")])
            if path:
                self._set_slot(idx, os.path.normpath(path))
                win.destroy()

        def manual():
            typed = query.get().strip()
            if typed:
                self._set_slot(idx, typed)
                win.destroy()

        query.bind("<KeyRelease>", repopulate)
        listbox.bind("<Double-Button-1>", commit)

        btns = tk.Frame(win, bg=BG)
        btns.pack(fill="x", padx=16, pady=(0, 6))
        PillButton(btns, self.anim, "Bind it", command=commit,
                   bg=BG).pack(side="left")
        PillButton(btns, self.anim, "Browse file", command=browse,
                   bg=BG).pack(side="left", padx=6)
        PillButton(btns, self.anim, "Use what I typed", command=manual,
                   bg=BG).pack(side="left")
        PillButton(btns, self.anim, "Cancel", command=win.destroy,
                   bg=BG).pack(side="right")
        tk.Label(win, text="Tip: you can also drag a shortcut straight onto a "
                           "slot in the Launcher list.", font=FONTS["small"],
                 fg=DIM, bg=BG).pack(anchor="w", padx=16, pady=(0, 12))
        choose("apps")

    def _set_slot(self, idx, cmd, label=""):
        cmds, labels = store.load_slots()
        cmds[idx] = cmd
        labels[idx] = label or app_index.friendly_label(cmd)
        store.set_in(self.cfg, "launcher.commands", cmds)
        store.set_in(self.cfg, "launcher.labels", labels)
        store.save_slots(cmds, labels)
        self.flash(f"{SLOT_NAMES[idx]} -> {labels[idx]} (live)")
        self._build_section()

    def _clear_slot(self, idx):
        cmds, labels = store.load_slots()
        cmds[idx], labels[idx] = "", ""
        store.set_in(self.cfg, "launcher.commands", cmds)
        store.set_in(self.cfg, "launcher.labels", labels)
        store.save_slots(cmds, labels)
        self.flash(f"{SLOT_NAMES[idx]} cleared")
        self._build_section()

    # ------------------------------------------------------- drag and drop ---
    def _register_drop(self):
        """Dropping a shortcut/exe onto a slot row binds it. Needs the tkdnd
        Tcl extension (from tkinterdnd2); without it every other way of
        binding still works, so this stays best-effort."""
        self.dnd = False
        try:
            self.root.tk.call("package", "require", "tkdnd")
            self.root.tk.call("tkdnd::drop_target", "register",
                              self.root._w, "DND_Files")
            self.root.bind("<<Drop>>", self._on_drop)
            self.dnd = True
        except tk.TclError:
            pass

    def _on_drop(self, event):
        paths = store.parse_drop_paths(str(getattr(event, "data", "")))
        if not paths:
            return
        idx = self._slot_under_pointer()
        if idx is None:
            self.flash("Drop it onto one of the four slots", tone=WARN)
            return
        self._set_slot(idx, os.path.normpath(paths[0]))

    def _slot_under_pointer(self):
        """Which slot row the cursor is over, or None."""
        rows = getattr(self, "_slot_rows", None)
        if not rows:
            return None
        py = self.root.winfo_pointery()
        for idx, (widget, y) in rows:
            try:
                top = widget.winfo_rooty() + y - 15
            except tk.TclError:
                continue
            if top <= py <= top + 30:
                return idx
        return None

    def _scan_apps_async(self):
        """Start Menu scan on a worker thread — never block the UI."""
        def work():
            try:
                found = app_index.scan_start_menu()
            except Exception:
                found = []
            self._apps = found
            self._apps_ready = True
        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------------- reset ---
    def _reset_section(self, sec):
        updates = {}
        for ctrl in sec["controls"]:
            key = ctrl.get("key")
            if not key or ctrl["kind"] in ("note", "slots"):
                continue
            if ctrl.get("derived"):
                cur = store.read_derived(store.load(os.devnull), key)
                updates.update(store.write_derived(key, cur))
            else:
                d = store.default_for(key)
                if d is not None:
                    updates[key] = d
        for dotted, val in updates.items():
            store.set_in(self.cfg, dotted, val)
        self._pending.update(updates)
        self._flush()
        self._build_section()


def make_root():
    """A Tk root that supports file drag-and-drop when tkinterdnd2 is
    installed, plain Tk otherwise — settings must open either way."""
    try:
        from tkinterdnd2 import TkinterDnD
        return TkinterDnD.Tk()
    except ImportError:
        return tk.Tk()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = make_root()
    init_fonts(root)
    app = SettingsApp(root)

    if "--shot" in sys.argv:
        args = sys.argv[sys.argv.index("--shot") + 1:]
        path = args[0]
        sections = args[1].split(",") if len(args) > 1 else [app.section]

        def snap():
            from PIL import ImageGrab
            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
            for name in sections:
                app.section = name
                app._pill_y = app._section_y(app._index(name))
                app._paint_sidebar()
                app._build_section()
                root.update_idletasks()
                root.update()
                x, y = root.winfo_rootx(), root.winfo_rooty()
                out = path if len(sections) == 1 else \
                    path.replace(".png", f"_{name.lower()}.png")
                ImageGrab.grab(bbox=(x, y, x + root.winfo_width(),
                                     y + root.winfo_height())).save(out)
                print(f"shot -> {out}")
            root.destroy()
        root.after(1500, snap)

    root.mainloop()


if __name__ == "__main__":
    main()
