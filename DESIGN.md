# Settings App — Design Notes

Design and implementation notes for the AirMouse settings app. This is the
reference for anyone extending the UI: it records the visual system, the
motion rules, and the constraints that shaped both.

Status: **beta.** The design is settled, and every section now tunes a
feature the tracker actually consumes (see *Controls ahead of features*).

---

## 1. Design goals

The settings app is a small desktop tool, not a dashboard. Three goals, in
priority order:

1. **Straightforward.** One obvious way to do everything, few controls per
   screen, plain-language labels. If a screen needs explaining, it needs
   redesigning.
2. **Calm and warm.** Matte, desaturated, warm-dark neutrals with a single
   bold accent. Nothing shouts.
3. **Alive to the touch.** Every interactive element animates on state
   change. The motion is the character of the app — a static build of this
   design would miss the point entirely.

The intended feel is a well-made hobby tool: professional enough to be
taken seriously, playful in the hand. Explicitly *not* a toy — no candy
colours, no emoji, no mascots.

## 2. Visual system

Matte means no gradients, no gloss, no pure black or white. Depth comes
from soft light, never from outlines.

### Colour

| Token         | Hex       | Use |
|---------------|-----------|-----|
| `BG`          | `#211e1b` | window background (warm charcoal) |
| `PANEL`       | `#282420` | sidebar, header |
| `CARD`        | `#2f2a25` | setting cards |
| `CARD_HOVER`  | `#383129` | hovered row |
| `LINE`        | `#453c33` | hairlines, inactive slider track |
| `TEXT`        | `#ebe4da` | primary text (warm off-white) |
| `DIM`         | `#9d938a` | captions, helper text |
| `accent`      | `#c25e4a` | the one bold colour — matte brick |
| `accent-soft` | `#7d5347` | accent at rest (selected nav pill) |
| `OK`          | `#96ab7c` | muted sage — saved confirmation |
| `WARN`        | `#c9a35c` | muted ochre — needs restart, failed write |
| `DANGER`      | `#b96754` | muted clay — destructive actions only |

Roughly 95% of pixels come from the neutral ramp. The accent is spent only
on the control you just touched, the primary button, highlighted phrases,
and the saved confirmation — scarcity is what makes it feel considered.
Sections do **not** get their own colours; identity comes from the icon and
title. Text on the accent is `BG`, never white.

Two alternate accents (amber `#e0823d`, teal `#4f9488`) ship in `ACCENTS`
and can be switched from the header.

### Type

Two families, both shipped with Windows, both chosen for character as much
as legibility:

| Role | Family | Used for |
|---|---|---|
| Display | **Bahnschrift SemiBold** (DIN-style) | titles, highlighted phrases, value bubbles |
| Body | **Candara** (warm humanist) | captions, nav labels, buttons |

Resolution walks a fallback chain (`DISPLAY_STACK` / `BODY_STACK`) so a
machine missing one lands on a comparable face rather than a system
default. A test asserts the stacks never contain the ubiquitous UI faces
(Segoe UI, Arial, Calibri, Verdana, Tahoma) — the type is meant to have a
voice.

Sizes: app 16, section 14, card 11, chip 10, bubble 9, body 10, nav 11,
small 9. Sentence case throughout; no ALL-CAPS labels.

### Highlighted phrases

Captions are written with asterisk markup. The marked phrase renders in the
display face, in the accent colour, lit from behind by a soft glow. The
point is scanning: the eye lands on the phrase and gets the answer to "what
does this do?" without reading the sentence.

```python
"*Buttons grab your cursor* — like a TV remote. *Push through* any time."
"*How hard it holds* once a button has caught the cursor."
```

Rules: one or two phrases per caption, never a whole sentence; the phrase is
the *answer*, not a restatement of the label; the word gap widens around a
phrase so its glow doesn't wash into the next word; phrases wrap safely
(each line segment gets its own glow); cards auto-grow so a wrapped caption
can never clip.

### Glow, not borders

Tk has no alpha compositing of its own, so glow is baked with PIL: draw the
shape, Gaussian-blur it, keep it **RGBA**, and let Tk composite it over
whatever is beneath (`glow_frames`, `glow_photo`, `surface_photo`). Frames
are pre-baked per intensity and cached, so animating a glow is an image
swap.

Rules that follow, all load-bearing:

- **Glows must stay RGBA.** An early version pre-composited each glow onto
  the assumed background colour, which turned every glow into an opaque
  rectangle that painted over neighbouring text. Never bake a background
  into a glow.
- **Cards are soft floating surfaces, never outlined blocks.** The fill sits
  a hair above the background and a blurred drop-shadow dissolves the edge.
  If you can see a card's edge as a line, it's wrong.
- **Never fake a glow with stacked flat shapes** — concentric plates read as
  borders, which is the opposite of the intent.
- Any Canvas widget placed on a card must use the card's fill as its own
  `bg` and sit fully inside the card, or its rectangle shows as a box.
- Keep `PhotoImage` references alive on the widget or Tk garbage-collects
  them and the artwork silently vanishes.

### Shape and space

Card radius 14, control radius 8, pills fully rounded. Cards are inset
`CARD_PAD` (12px) inside their canvas so the drop-shadow has room to fall;
6px between cards; sidebar 190px. One calm column per section — generous,
but keep the column filled; dead space at the bottom reads as unfinished.

Icons are simple line glyphs drawn on Canvas, single colour, `DIM` at rest
and accent when selected. No emoji anywhere in the UI.

## 3. Layout

```
┌────────────────────────────────────────────────────────┐
│ ✳ AirMouse                                  Accent ○○○ │  header
├───────────┬────────────────────────────────────────────┤
│  Pointer  │  Scrollable column of rounded matte cards. │
│  Scrolling│  The selected nav item's pill SLIDES to    │
│  Gestures │  the new selection.                        │
│  Magnet   │                                            │
│  Launcher │                                            │
│  Attention│                                            │
│  Camera   │                                            │
├───────────┴────────────────────────────────────────────┤
│ ✓ Saved — live in the app            (fades out)       │  footer
└────────────────────────────────────────────────────────┘
```

Confirmations live in the footer and fade. The app never opens a message
box for a success state.

## 4. Motion

Every state change animates. Timings are short, easings soft; the app
should feel physical, never showy. Where a toolkit widget can't animate,
the control is hand-drawn on a Canvas and animated manually — motion is not
optional.

| Interaction | Animation |
|---|---|
| Toggle | knob slides ~140 ms ease-out with ~10% overshoot; track cross-fades to accent |
| Slider drag | fill tracks the knob; value bubble pops in (~100 ms), fades ~400 ms after release |
| Slider reset (double-click) | knob glides home ~180 ms |
| Button press | shifts down 1px and darkens ~80 ms, springs back on release |
| Nav change | selection pill slides ~160 ms; content cross-fades |
| Card hover | background eases ~100 ms |
| Saved confirmation | slides up 4px, fades in, check draws itself, auto-fades after ~2 s |
| Expander | chevron rotates ~120 ms; rows unfold |

Nothing loops forever, nothing bounces more than once, nothing exceeds
~250 ms. Animations are interruptible — a state change mid-flight retargets
smoothly rather than snapping. The tween engine (`Animator`) keys jobs by
control, so mashing a switch stays smooth.

### The Magnetism switch

The one deliberately elaborate control, and the reference for how much
character a single interaction can carry. A horseshoe magnet parks tilted
away beside the switch; it is the actor, and the switch only reacts.

**On:** the magnet swings in over ~200 ms with an *ease-in* (attraction
accelerating as it closes), sparks on contact (~150 ms), the knob is flung
across to meet it (~170 ms, a harder overshoot than the normal toggle), and
the switch flares to 135% glow before settling to a steady lit state.

**Off:** the magnet retreats (~220 ms), the knob flicks back 40 ms later,
and the glow fades.

The compound sequence is the one sanctioned exception to the 250 ms rule;
each individual stage still obeys it. Every flip bumps a sequence token so
chained stages abort if the state changed underneath them.

## 5. Architecture

- **`settings_ui.py`** — the widget kit: `MagnetToggle`, `Toggle`, `Slider`,
  `PillButton`, `RichCaption`, `Animator`, plus the glow/surface bakers.
  Every screen uses these; controls are never re-invented per screen.
- **`settings_store.py`** — the data layer. `SECTIONS` is a **control
  manifest**: each control declares its config key, range, default, caption
  markup, and `advanced` / `derived` flags. The UI is generated from it, so
  a control that doesn't map to a real setting is impossible — a test
  asserts every key resolves against `config_defaults.py`. Writes are
  atomic (temp file + replace) and preserve unknown keys, because the
  tracker stores its own calibration in the same file.
- **`settings_app.py`** — the app. Live-apply with a 300 ms debounce.
  Settings the tracker only reads at startup are tagged "after restart"
  rather than pretending to be live (`LIVE_PREFIXES` is the source of
  truth). Advanced controls hide behind a "More options" expander.
- **Derived controls** map one visible control onto several keys — see
  `read_derived` / `write_derived`. Attention "Looseness" drives the yaw and
  pitch thresholds together; "Left-handed" writes `dominant_hand`.

### Implementation notes worth keeping

- Never assign `self._w` on a Tk widget — that attribute is Tkinter's
  internal Tcl path name, and overwriting it breaks every subsequent call
  on that widget with a confusing "invalid command name" error.
- `init_fonts` mutates the `FONTS` dict in place rather than rebinding it,
  so modules that imported the dict see the resolved fonts.
- When a control occupies a card's title row, pass `hint_reserve` so the
  "after restart" hint isn't drawn underneath it.

## 6. Controls ahead of features

Every section currently drives something real: the **Magnet** section shipped
before cursor magnetism existed, and the tracker has consumed those settings
since magnetism landed. Both `launcher.*` and `magnet.*` are in
`LIVE_PREFIXES`, so their controls apply without a restart.

The rule that episode produced is the part worth keeping. A control that
tunes nothing is worse than a missing one — it invites you to tune a feature
and conclude it does nothing. So if a section has to ship ahead of its
feature, it carries a visible tag saying so: set `pending` on the section in
`SECTIONS` and `settings_app` draws that text under the section intro in the
warning colour. No section sets it today, and that is the state to return to.

## 7. Conventions

- Every setting reachable in two clicks; no nested dialogs.
- Every control shows its default and can be reset (double-click a slider,
  or "Reset this section").
- One friendly sentence under each control, with the key phrase highlighted.
- Never add a control without a real key behind it.
- Warm dark theme only; there is no light theme.
