"""Floating Tk widgets — toolbar, vertical palette strip, Copic modal picker.

Each widget owns its own subtree and exposes a small callback surface so the
app can wire it up without the widget knowing about the Document.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

try:
    from copic_palette import COPIC_PALETTE
except ImportError:
    COPIC_PALETTE = []

from state import BrushPattern, Document


# ---------- Helpers ----------

def _parent_bg(parent: tk.Misc) -> str:
    """Read the parent widget's bg color so chrome can sit transparently on
    top of it. Falls back to dark grey if the parent has no bg key."""
    try:
        return parent.cget("bg")
    except tk.TclError:
        return "#1e1e1e"


def _readable_fg(hexc: str) -> str:
    """Pick black or white text for legibility on top of `hexc`.

    Uses the standard relative-luminance heuristic — light backgrounds
    get black text, dark backgrounds get white text. Falls back to white
    on parse error."""
    try:
        h = hexc.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        luma = (0.299 * r + 0.587 * g + 0.114 * b)
        return "#000000" if luma > 160 else "#FFFFFF"
    except Exception:
        return "#FFFFFF"


# ---------- Layout constants ----------

# Distance between a chrome arrow and its adjacent label, measured edge-to-edge.
# Applied uniformly to all five labelled toggles so the spacing reads as one
# rhythm across the canvas.
ARROW_LABEL_GAP = 6

# Single left-edge column. Row 0 is the master Drawer arrow; sub-toggles
# cascade beneath when the drawer is expanded. Nested drawers (e.g. COLOR)
# indent their children by TOGGLE_NEST_INDENT so the visual hierarchy
# reads as a tree.
TOGGLE_COL_X = 8
TOGGLE_ROW_GAP = 32
TOGGLE_NEST_INDENT = 14
TOGGLE_Y_DRAWER   = 8
TOGGLE_Y_TOOLBAR  = TOGGLE_Y_DRAWER + TOGGLE_ROW_GAP    # 40
TOGGLE_Y_LINE     = TOGGLE_Y_TOOLBAR + TOGGLE_ROW_GAP   # 72
TOGGLE_Y_ACTIONS  = TOGGLE_Y_LINE + TOGGLE_ROW_GAP      # 104
TOGGLE_Y_COLOR    = TOGGLE_Y_ACTIONS + TOGGLE_ROW_GAP   # 136
# Palette is nested under COLOR: indented and only visible when both
# the master drawer and COLOR are expanded.
TOGGLE_Y_SWATCH   = TOGGLE_Y_COLOR + TOGGLE_ROW_GAP     # 168
TOGGLE_Y_PALETTE  = TOGGLE_Y_SWATCH + TOGGLE_ROW_GAP    # 200
TOGGLE_X_NESTED   = TOGGLE_COL_X + TOGGLE_NEST_INDENT


# ---------- Pixel-font glyphs ----------

# 3×5 pixel-art glyphs covering every character used by the five chrome
# labels (Toolbar, Palette, Browse, Line, Actions). Each row is read
# left-to-right, top-to-bottom — 1 = filled cell, 0 = empty. Glyphs that
# need 4 columns (M, W, etc.) use a 4-column grid; the renderer reads
# each glyph's column count from row width.
PIXEL_GLYPHS: dict[str, list[list[int]]] = {
    "A": [[0,1,0],[1,0,1],[1,1,1],[1,0,1],[1,0,1]],
    "B": [[1,1,0],[1,0,1],[1,1,0],[1,0,1],[1,1,0]],
    "C": [[0,1,1],[1,0,0],[1,0,0],[1,0,0],[0,1,1]],
    "E": [[1,1,1],[1,0,0],[1,1,0],[1,0,0],[1,1,1]],
    "H": [[1,0,1],[1,0,1],[1,1,1],[1,0,1],[1,0,1]],
    "I": [[1,1,1],[0,1,0],[0,1,0],[0,1,0],[1,1,1]],
    "L": [[1,0,0],[1,0,0],[1,0,0],[1,0,0],[1,1,1]],
    "N": [[1,0,1],[1,1,1],[1,1,1],[1,0,1],[1,0,1]],
    "O": [[0,1,0],[1,0,1],[1,0,1],[1,0,1],[0,1,0]],
    "P": [[1,1,0],[1,0,1],[1,1,0],[1,0,0],[1,0,0]],
    "R": [[1,1,0],[1,0,1],[1,1,0],[1,0,1],[1,0,1]],
    "S": [[0,1,1],[1,0,0],[0,1,0],[0,0,1],[1,1,0]],
    "T": [[1,1,1],[0,1,0],[0,1,0],[0,1,0],[0,1,0]],
    "U": [[1,0,1],[1,0,1],[1,0,1],[1,0,1],[0,1,0]],
    "W": [[1,0,0,1],[1,0,0,1],[1,0,0,1],[1,1,1,1],[1,0,0,1]],
    " ": [[0],[0],[0],[0],[0]],
}


# ---------- Pixel label ----------

class PixelLabel(tk.Canvas):
    """Renders a string as a row of pixel-art glyphs on a transparent
    canvas, matching the GridArrowButton aesthetic. Each glyph is drawn
    cell-by-cell at PIXEL × PIXEL with PIXEL_GAP between cells, and one
    GLYPH_GAP of empty space between glyphs."""

    PIXEL = 2
    PIXEL_GAP = 1
    GLYPH_GAP = 2
    PAD = 2
    FILL = "#000000"

    def __init__(self, parent: tk.Misc, text: str) -> None:
        bg = _parent_bg(parent)
        self._text = text
        self._cell_ids: list[int] = []
        # Compute total width/height before constructing the canvas.
        w, h = self._measure(text)
        super().__init__(
            parent, width=w, height=h,
            bg=bg, highlightthickness=0, bd=0,
        )
        self._draw()

    def _glyph_cell_width(self, glyph: list[list[int]]) -> int:
        return len(glyph[0]) if glyph else 0

    def _measure(self, text: str) -> tuple[int, int]:
        total_cells = 0
        for i, ch in enumerate(text):
            g = PIXEL_GLYPHS.get(ch, PIXEL_GLYPHS[" "])
            total_cells += self._glyph_cell_width(g)
        w = (
            total_cells * self.PIXEL
            + max(0, total_cells - 1) * self.PIXEL_GAP
            + max(0, len(text) - 1) * self.GLYPH_GAP
            + 2 * self.PAD
        )
        h = 5 * self.PIXEL + 4 * self.PIXEL_GAP + 2 * self.PAD
        return w, h

    def _draw(self) -> None:
        for cid in self._cell_ids:
            self.delete(cid)
        self._cell_ids.clear()
        x = self.PAD
        for ch in self._text:
            glyph = PIXEL_GLYPHS.get(ch, PIXEL_GLYPHS[" "])
            cols = self._glyph_cell_width(glyph)
            for r, row in enumerate(glyph):
                for c, on in enumerate(row):
                    if not on:
                        continue
                    px = x + c * (self.PIXEL + self.PIXEL_GAP)
                    py = self.PAD + r * (self.PIXEL + self.PIXEL_GAP)
                    self._cell_ids.append(self.create_rectangle(
                        px, py, px + self.PIXEL, py + self.PIXEL,
                        fill=self.FILL, outline="",
                    ))
            x += cols * (self.PIXEL + self.PIXEL_GAP) + self.GLYPH_GAP

    def refresh_bg(self) -> None:
        """Re-inherit bg from the parent — mirrors GridArrowButton."""
        try:
            self.configure(bg=self.master.cget("bg"))
        except tk.TclError:
            pass

    @property
    def widget_width(self) -> int:
        return int(self.cget("width"))

    @property
    def widget_height(self) -> int:
        return int(self.cget("height"))


# ---------- Drawer ----------

class Drawer:
    """A collapsible master arrow that hides/shows a list of registered
    sub-entries (toggle + optional label). Drawers nest: a sub-entry can
    be another Drawer's master arrow, and collapsing the parent cascades
    `hide_all` through the child so its children disappear too. This
    lets the chrome grow new sub-menus without changing the contract.

    Construction:
      Drawer(parent, x=…, y=…)             — top-level drawer
      Drawer(parent, x=…, y=…, parent_drawer=parent_drawer)
                                            — child drawer, auto-registers
                                              its master arrow with the
                                              parent so it inherits
                                              collapse cascades

    Each Drawer remembers its master-arrow place coords so a parent can
    `hide_all`/`show_all` it just like any other registered entry.
    """

    def __init__(self, parent: tk.Misc, *,
                 x: int = TOGGLE_COL_X, y: int = TOGGLE_Y_DRAWER,
                 label: str | None = None,
                 parent_drawer: "Drawer | None" = None) -> None:
        self.parent = parent
        self.expanded = False
        self._entries: list[dict] = []
        # `_master_x/_master_y` are the home coords for *this* drawer's
        # master arrow — needed if a grandparent collapses us and we have
        # to re-place ourselves on reveal.
        self._master_x = x
        self._master_y = y
        self.toggle_btn = GridArrowButton(
            parent, direction="right", command=self.toggle,
        )
        self.toggle_btn.place(x=x, y=y)

        # Optional pixel-art label adjacent to the master arrow. Used by
        # sub-drawers (e.g. COLOR) so the user knows what's inside.
        self.toggle_label: PixelLabel | None = None
        self._label_x_home: int | None = None
        self._label_y_home: int | None = None
        if label is not None:
            self.toggle_label = PixelLabel(parent, text=label)
            self._label_x_home = x + self.toggle_btn.widget_width + ARROW_LABEL_GAP
            center_dy = (self.toggle_btn.widget_height
                         - self.toggle_label.widget_height) // 2
            self._label_y_home = y + center_dy
            self.toggle_label.place(x=self._label_x_home, y=self._label_y_home)

        # If we're nested, register our master arrow (and label, if any)
        # as an entry on the parent so the parent's collapse hides us too.
        if parent_drawer is not None:
            parent_drawer.register(
                toggle=self.toggle_btn,
                label=self.toggle_label,
                toggle_x=x, toggle_y=y,
                label_x=self._label_x_home, label_y=self._label_y_home,
                child_drawer=self,
            )

    def register(self, *, toggle: tk.Misc, label: tk.Misc | None,
                 toggle_x: int, toggle_y: int,
                 label_x: int | None, label_y: int | None,
                 child_drawer: "Drawer | None" = None) -> None:
        """Register a sub-entry. `child_drawer` lets the parent cascade
        collapse/expand into a nested Drawer."""
        self._entries.append({
            "toggle": toggle, "label": label,
            "toggle_x": toggle_x, "toggle_y": toggle_y,
            "label_x": label_x, "label_y": label_y,
            "child_drawer": child_drawer,
        })
        # Drawer starts collapsed → hide the entry immediately.
        if not self.expanded:
            toggle.place_forget()
            if label is not None:
                label.place_forget()
            if child_drawer is not None:
                child_drawer.hide_all()

    def hide_all(self) -> None:
        """Hide this drawer's master arrow and every entry it owns
        (recursively for child drawers). Used when a parent collapses."""
        self.toggle_btn.place_forget()
        # Collapse our own visual state — if we were expanded, snap shut.
        if self.expanded:
            self.expanded = False
            self.toggle_btn.set_direction("right")
        for e in self._entries:
            e["toggle"].place_forget()
            if e["label"] is not None:
                e["label"].place_forget()
            if e["child_drawer"] is not None:
                e["child_drawer"].hide_all()

    def show_master(self) -> None:
        """Re-place this drawer's master arrow at its home coords.
        Entries stay hidden until the user expands the drawer."""
        self.toggle_btn.place(x=self._master_x, y=self._master_y)
        if self.toggle_label is not None:
            self.toggle_label.place(
                x=self._label_x_home, y=self._label_y_home,
            )

    def toggle(self) -> None:
        self.expanded = not self.expanded
        if self.expanded:
            self.toggle_btn.set_direction("left")
            for e in self._entries:
                e["toggle"].place(x=e["toggle_x"], y=e["toggle_y"])
                if e["label"] is not None:
                    e["label"].place(x=e["label_x"], y=e["label_y"])
                if e["child_drawer"] is not None:
                    e["child_drawer"].show_master()
        else:
            self.toggle_btn.set_direction("right")
            for e in self._entries:
                e["toggle"].place_forget()
                if e["label"] is not None:
                    e["label"].place_forget()
                if e["child_drawer"] is not None:
                    e["child_drawer"].hide_all()

    def refresh_bg(self) -> None:
        self.toggle_btn.refresh_bg()
        if self.toggle_label is not None:
            self.toggle_label.refresh_bg()


# ---------- Grid-square arrow button ----------

class GridArrowButton(tk.Canvas):
    """A clickable arrow rendered as filled grid squares on a transparent
    canvas background. Matches the app's lattice metaphor — the arrow is
    *made of cells*, not a glyph. Background draws as the parent's bg so
    the widget reads as transparent against the chrome.

    Directions: "up" | "down" | "left" | "right". `set_direction` flips
    the arrow without rebuilding the widget.
    """

    CELL = 4         # px per grid square in the arrow
    GAP = 1          # px between cells
    FILL = "#000000"
    HOVER_FILL = "#000000"

    # 5×5 pixel-art arrows. 1 = draw a cell, 0 = empty.
    SHAPES: dict[str, list[list[int]]] = {
        "right": [
            [0, 0, 1, 0, 0],
            [0, 0, 1, 1, 0],
            [1, 1, 1, 1, 1],
            [0, 0, 1, 1, 0],
            [0, 0, 1, 0, 0],
        ],
        "left": [
            [0, 0, 1, 0, 0],
            [0, 1, 1, 0, 0],
            [1, 1, 1, 1, 1],
            [0, 1, 1, 0, 0],
            [0, 0, 1, 0, 0],
        ],
        "up": [
            [0, 0, 1, 0, 0],
            [0, 1, 1, 1, 0],
            [1, 1, 1, 1, 1],
            [0, 0, 1, 0, 0],
            [0, 0, 1, 0, 0],
        ],
        "down": [
            [0, 0, 1, 0, 0],
            [0, 0, 1, 0, 0],
            [1, 1, 1, 1, 1],
            [0, 1, 1, 1, 0],
            [0, 0, 1, 0, 0],
        ],
    }

    def __init__(self, parent: tk.Misc, *, direction: str = "right",
                 command: Callable[[], None] | None = None) -> None:
        rows = len(self.SHAPES[direction])
        cols = len(self.SHAPES[direction][0])
        # Pad 2px on each side so cells don't crowd the widget edge.
        w = cols * self.CELL + (cols - 1) * self.GAP + 4
        h = rows * self.CELL + (rows - 1) * self.GAP + 4
        # Inherit parent bg so the canvas reads as transparent chrome.
        bg = parent.cget("bg") if "bg" in parent.keys() else "#1e1e1e"
        super().__init__(
            parent, width=w, height=h,
            bg=bg, highlightthickness=0, bd=0,
            cursor="hand2",
        )
        self._direction = direction
        self._command = command
        self._cell_ids: list[int] = []
        self._draw()
        self.bind("<Button-1>", lambda e: self._command() if self._command else None)
        self.bind("<Enter>", lambda e: self._set_fill(self.HOVER_FILL))
        self.bind("<Leave>", lambda e: self._set_fill(self.FILL))

    def _draw(self) -> None:
        for cid in self._cell_ids:
            self.delete(cid)
        self._cell_ids.clear()
        shape = self.SHAPES[self._direction]
        for r, row in enumerate(shape):
            for c, on in enumerate(row):
                if not on:
                    continue
                x0 = 2 + c * (self.CELL + self.GAP)
                y0 = 2 + r * (self.CELL + self.GAP)
                self._cell_ids.append(self.create_rectangle(
                    x0, y0, x0 + self.CELL, y0 + self.CELL,
                    fill=self.FILL, outline="",
                ))

    def _set_fill(self, color: str) -> None:
        for cid in self._cell_ids:
            self.itemconfigure(cid, fill=color)

    def set_direction(self, direction: str) -> None:
        if direction == self._direction:
            return
        self._direction = direction
        self._draw()

    def refresh_bg(self) -> None:
        """Re-inherit bg from the parent. Call when the parent's color
        changes (e.g. when the surface paper switches)."""
        try:
            self.configure(bg=self.master.cget("bg"))
        except tk.TclError:
            pass

    @property
    def widget_width(self) -> int:
        """Pixel width of the canvas itself, for label-placement math."""
        return int(self.cget("width"))

    @property
    def widget_height(self) -> int:
        return int(self.cget("height"))

    def tkraise(self, aboveThis=None) -> None:
        # tk.Canvas overrides tkraise to mean "raise a canvas item by id".
        # We want widget-stacking-order raise — call up to Misc explicitly.
        tk.Misc.tkraise(self, aboveThis)

    lift = tkraise


# ---------- Toolbar ----------

class Toolbar:
    """Floating top-left toolbar with style/brush/pattern/color/actions."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        mode_var: tk.StringVar,
        surface_var: tk.StringVar,
        surfaces: list[str],
        get_pattern: Callable[[], BrushPattern],
        set_pattern: Callable[[BrushPattern], None],
        get_color: Callable[[], str],
        on_mode_change: Callable[[], None],
        on_surface_change: Callable[[], None],
        on_auto_toggle: Callable[[], None],
    ) -> None:
        self.parent = parent
        self.mode_var = mode_var
        self.surface_var = surface_var
        self.surfaces = surfaces
        self._get_pattern = get_pattern
        self._set_pattern = set_pattern
        self._get_color = get_color
        self._on_mode_change = on_mode_change
        self._on_surface_change = on_surface_change
        self._on_auto_toggle = on_auto_toggle

        self.visible = False
        self._build_toggle()
        self._build_bar()

    TOGGLE_X = TOGGLE_COL_X
    TOGGLE_Y = TOGGLE_Y_TOOLBAR

    def _label_x(self) -> int:
        return self.TOGGLE_X + self.toggle_btn.widget_width + ARROW_LABEL_GAP

    def _label_y(self) -> int:
        # Center the label vertically on the arrow tip.
        return self.TOGGLE_Y + (
            self.toggle_btn.widget_height - self.toggle_label.widget_height
        ) // 2

    def _build_toggle(self) -> None:
        self.toggle_btn = GridArrowButton(
            self.parent, direction="right",
            command=self.toggle,
        )
        self.toggle_btn.place(x=self.TOGGLE_X, y=self.TOGGLE_Y)
        # Adjacent pixel-art label so the arrow's purpose is legible.
        self.toggle_label = PixelLabel(self.parent, text="TOOLBAR")
        self.toggle_label.place(x=self._label_x(), y=self._label_y())

    def refresh_bg(self) -> None:
        """Repaint toggle + label so they blend with the parent's current bg."""
        self.toggle_btn.refresh_bg()
        self.toggle_label.refresh_bg()

    def drawer_entries(self) -> list[dict]:
        return [{
            "toggle": self.toggle_btn,
            "label": self.toggle_label,
            "toggle_x": self.TOGGLE_X, "toggle_y": self.TOGGLE_Y,
            "label_x": self._label_x(), "label_y": self._label_y(),
        }]

    def _build_bar(self) -> None:
        self.bar = tk.Frame(self.parent, bg="#252526",
                            padx=8, pady=6, relief="solid", borderwidth=1)

        self.style_label_text = tk.StringVar(value="Style: Cell fill")
        style_mb = tk.Menubutton(
            self.bar, textvariable=self.style_label_text,
            bg="#2d2d30", fg="#e0e0e0",
            activebackground="#3a3a3c", activeforeground="#fff",
            relief="flat", padx=10, pady=4, indicatoron=False, cursor="hand2",
        )
        style_menu = tk.Menu(style_mb, tearoff=0, bg="#2d2d30", fg="#e0e0e0")
        for value, label in [("cell", "Cell fill"), ("free", "Free draw"),
                             ("sheet", "Sheet press"), ("erase", "Eraser")]:
            style_menu.add_radiobutton(
                label=label, value=value, variable=self.mode_var,
                command=lambda v=value, l=label: self._on_style_pick(v, l),
            )
        style_mb.configure(menu=style_menu)
        style_mb.pack(side=tk.LEFT, padx=4)

        self.pattern_label_text = tk.StringVar(
            value=f"Type: {self._get_pattern().label}"
        )
        type_mb = tk.Menubutton(
            self.bar, textvariable=self.pattern_label_text,
            bg="#2d2d30", fg="#e0e0e0",
            activebackground="#3a3a3c", activeforeground="#fff",
            relief="flat", padx=10, pady=4, indicatoron=False, cursor="hand2",
        )
        type_menu = tk.Menu(type_mb, tearoff=0, bg="#2d2d30", fg="#e0e0e0")
        type_menu.add_command(
            label="Solid",
            command=lambda: self._pick_pattern(BrushPattern("Solid", 0, 0)),
        )
        type_menu.add_separator()

        dashed_sub = tk.Menu(type_menu, tearoff=0, bg="#2d2d30", fg="#e0e0e0")
        for n in (4, 8, 12, 16, 24, 32, 48):
            label = f"Dash {n} · Gap {n}"
            dashed_sub.add_command(
                label=label,
                command=lambda lbl=label, d=n, g=n:
                    self._pick_pattern(BrushPattern(lbl, d, g)),
            )
        type_menu.add_cascade(label="Dashed", menu=dashed_sub)

        dotted_sub = tk.Menu(type_menu, tearoff=0, bg="#2d2d30", fg="#e0e0e0")
        for d, g in [(1, 3), (2, 4), (2, 6), (3, 8), (4, 10)]:
            label = f"Dot {d} · Gap {g}"
            dotted_sub.add_command(
                label=label,
                command=lambda lbl=label, dd=d, gg=g:
                    self._pick_pattern(BrushPattern(lbl, dd, gg)),
            )
        type_menu.add_cascade(label="Dotted", menu=dotted_sub)

        asym_sub = tk.Menu(type_menu, tearoff=0, bg="#2d2d30", fg="#e0e0e0")
        for d, g in [(4, 12), (8, 16), (8, 24), (16, 8), (24, 8), (32, 16)]:
            label = f"{d} · {g}"
            asym_sub.add_command(
                label=label,
                command=lambda lbl=label, dd=d, gg=g:
                    self._pick_pattern(BrushPattern(lbl, dd, gg)),
            )
        type_menu.add_cascade(label="Asymmetric", menu=asym_sub)

        type_mb.configure(menu=type_menu)
        type_mb.pack(side=tk.LEFT, padx=4)

        ttk.Separator(self.bar, orient="vertical").pack(
            side=tk.LEFT, fill=tk.Y, padx=8
        )
        self.swatch = tk.Label(self.bar, bg=self._get_color(), width=3,
                               relief="sunken", borderwidth=2)
        self.swatch.pack(side=tk.LEFT, padx=(8, 4))
        self.color_label = tk.Label(self.bar, text="—",
                                    bg="#252526", fg="#ddd", width=22, anchor="w")
        self.color_label.pack(side=tk.LEFT, padx=2)

        ttk.Separator(self.bar, orient="vertical").pack(
            side=tk.LEFT, fill=tk.Y, padx=8
        )
        self.surface_label_text = tk.StringVar(
            value=f"Surface: {self.surface_var.get()}"
        )
        surface_mb = tk.Menubutton(
            self.bar, textvariable=self.surface_label_text,
            bg="#2d2d30", fg="#e0e0e0",
            activebackground="#3a3a3c", activeforeground="#fff",
            relief="flat", padx=10, pady=4, indicatoron=False, cursor="hand2",
        )
        surface_menu = tk.Menu(surface_mb, tearoff=0, bg="#2d2d30", fg="#e0e0e0")
        for name in self.surfaces:
            surface_menu.add_radiobutton(
                label=name, value=name, variable=self.surface_var,
                command=lambda n=name: self._on_surface_pick(n),
            )
        surface_mb.configure(menu=surface_menu)
        surface_mb.pack(side=tk.LEFT, padx=4)

        ttk.Separator(self.bar, orient="vertical").pack(
            side=tk.LEFT, fill=tk.Y, padx=8
        )
        # Auto mode toggle. ON = simulated continuous click. Disengage with
        # the spacebar (bound globally in app.py).
        self._auto_label = tk.StringVar(value="Auto: OFF")
        self.auto_btn = tk.Button(
            self.bar, textvariable=self._auto_label,
            bg="#2d2d30", fg="#e0e0e0",
            activebackground="#3a3a3c", activeforeground="#fff",
            relief="flat", borderwidth=0, padx=10, pady=4, cursor="hand2",
            command=self._on_auto_toggle,
        )
        self.auto_btn.pack(side=tk.LEFT, padx=4)

    def set_auto_state(self, state: str) -> None:
        """Reflect the app's auto-mode state on the toolbar button.

        States: 'off' (idle), 'arming' (waiting for canvas click),
        'on' (engaged, mouse acts as continuously clicked).
        """
        if state == "on":
            self._auto_label.set("Auto: ON  (Space to stop)")
            self.auto_btn.configure(bg="#C84B3F", fg="#fff",
                                    activebackground="#E0593F")
        elif state == "arming":
            self._auto_label.set("Auto: click canvas… (Esc to cancel)")
            self.auto_btn.configure(bg="#D4A017", fg="#1e1e1e",
                                    activebackground="#E8B82F")
        else:
            self._auto_label.set("Auto: OFF")
            self.auto_btn.configure(bg="#2d2d30", fg="#e0e0e0",
                                    activebackground="#3a3a3c")

    def _on_style_pick(self, value: str, label: str) -> None:
        self.style_label_text.set(f"Style: {label}")
        self._on_mode_change()

    def _pick_pattern(self, pattern: BrushPattern) -> None:
        self._set_pattern(pattern)
        self.pattern_label_text.set(f"Type: {pattern.label}")

    def _on_surface_pick(self, name: str) -> None:
        self.surface_label_text.set(f"Surface: {name}")
        self._on_surface_change()

    def show_color(self, hexc: str, label: str = "") -> None:
        """Update the read-only swatch + label to reflect the active color."""
        self.swatch.configure(bg=hexc)
        self.color_label.configure(text=label or hexc)

    def toggle(self) -> None:
        self.visible = not self.visible
        if self.visible:
            self.bar.place(x=40, y=4)
            self.bar.tkraise()
            self.toggle_btn.set_direction("left")
            self.toggle_btn.tkraise()
            # Hide the "Toolbar" hint label so the open bar can occupy the space.
            self.toggle_label.place_forget()
        else:
            self.bar.place_forget()
            self.toggle_btn.set_direction("right")
            self.toggle_label.place(x=self._label_x(), y=self._label_y())


# ---------- Actions card ----------

class ActionsCard:
    """Floating left-column card with Undo / Redo / Clear / Save SVG."""

    TOGGLE_X = TOGGLE_COL_X
    TOGGLE_Y = TOGGLE_Y_ACTIONS

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_undo: Callable[[], None],
        on_redo: Callable[[], None],
        on_clear: Callable[[], None],
        on_save: Callable[[], None],
    ) -> None:
        self.parent = parent
        self.visible = False
        self._build_toggle()
        self._build_card(on_undo, on_redo, on_clear, on_save)

    def _label_x(self) -> int:
        return self.TOGGLE_X + self.toggle_btn.widget_width + ARROW_LABEL_GAP

    def _label_y(self) -> int:
        return self.TOGGLE_Y + (
            self.toggle_btn.widget_height - self.toggle_label.widget_height
        ) // 2

    def _card_x(self) -> int:
        # Card slides out just to the right of the toggle column.
        return self.TOGGLE_X + self.toggle_btn.widget_width + ARROW_LABEL_GAP

    def _build_toggle(self) -> None:
        self.toggle_btn = GridArrowButton(
            self.parent, direction="right",
            command=self.toggle,
        )
        self.toggle_btn.place(x=self.TOGGLE_X, y=self.TOGGLE_Y)
        self.toggle_label = PixelLabel(self.parent, text="ACTIONS")
        self.toggle_label.place(x=self._label_x(), y=self._label_y())

    def refresh_bg(self) -> None:
        self.toggle_btn.refresh_bg()
        self.toggle_label.refresh_bg()

    def drawer_entries(self) -> list[dict]:
        return [{
            "toggle": self.toggle_btn,
            "label": self.toggle_label,
            "toggle_x": self.TOGGLE_X, "toggle_y": self.TOGGLE_Y,
            "label_x": self._label_x(), "label_y": self._label_y(),
        }]

    def _build_card(self, on_undo, on_redo, on_clear, on_save) -> None:
        self.card = tk.Frame(self.parent, bg="#252526",
                             padx=8, pady=6, relief="solid", borderwidth=1)
        ttk.Button(self.card, text="Undo", command=on_undo).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.card, text="Redo", command=on_redo).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.card, text="Clear", command=on_clear).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.card, text="Save SVG…", command=on_save).pack(side=tk.LEFT, padx=4)

    def toggle(self) -> None:
        self.visible = not self.visible
        if self.visible:
            self.card.place(x=self._card_x(), y=self.TOGGLE_Y)
            self.card.tkraise()
            self.toggle_btn.set_direction("left")
            self.toggle_btn.tkraise()
            self.toggle_label.place_forget()
        else:
            self.card.place_forget()
            self.toggle_btn.set_direction("right")
            self.toggle_label.place(x=self._label_x(), y=self._label_y())


# ---------- Palette strip ----------

class PaletteStrip:
    """Vertical Copic palette pinned to the left edge.

    Has a header indicator at the top showing the active color with a
    white outline ring so the user can see at a glance which Copic is
    loaded as they scan the strip.
    """

    SWATCH = 16
    MARGIN = 2
    GROUP_GAP = 6
    # Doubled from 12 → 24 so every Copic group fits above the fold
    # without scrolling.
    COLS = 24
    # Header indicator: a larger swatch with a high-contrast outline ring.
    INDICATOR_H = 28
    INDICATOR_RING = "#FFFFFF"   # white outline ring — "stands out" highlight
    INDICATOR_RING_PX = 3

    def __init__(self, parent: tk.Misc,
                 on_pick: Callable[[str, str], None]) -> None:
        self.parent = parent
        self._on_pick = on_pick
        self.visible = False

        # Palette is nested under the COLOR sub-drawer, so it uses
        # TOGGLE_X_NESTED (indented one step from the column).
        self.toggle_btn = GridArrowButton(
            parent, direction="right", command=self.toggle,
        )
        self.toggle_btn.place(x=TOGGLE_X_NESTED, y=TOGGLE_Y_SWATCH)
        label_x = TOGGLE_X_NESTED + self.toggle_btn.widget_width + ARROW_LABEL_GAP
        self.toggle_label = PixelLabel(parent, text="SWATCH")
        center_dy = (self.toggle_btn.widget_height
                     - self.toggle_label.widget_height) // 2
        self.toggle_label.place(x=label_x, y=TOGGLE_Y_SWATCH + center_dy)
        # Cache the centered offset for the open/close handler that
        # re-places the label after a panel toggle.
        self._toggle_label_y = TOGGLE_Y_SWATCH + center_dy

        self.frame = tk.Frame(parent, bg="#1a1a1a", borderwidth=1, relief="solid")
        self.inner = tk.Frame(self.frame, bg="#1a1a1a")
        self.inner.pack(side=tk.TOP, anchor="nw")
        self._build_indicator()
        self._build_swatches()

    def _build_indicator(self) -> None:
        """Top-of-strip active-color indicator with a white outline ring."""
        # Outer frame is the ring; inner frame is the color swatch.
        strip_width = self.COLS * (self.SWATCH + 2 * self.MARGIN)
        header = tk.Frame(self.inner, bg="#1a1a1a", padx=4, pady=6)
        header.pack(side=tk.TOP, fill=tk.X)

        self._ind_ring = tk.Frame(
            header, bg=self.INDICATOR_RING,
            width=strip_width - 8,
            height=self.INDICATOR_H + 2 * self.INDICATOR_RING_PX,
        )
        self._ind_ring.pack(side=tk.TOP)
        self._ind_ring.pack_propagate(False)

        self._ind_swatch = tk.Frame(
            self._ind_ring, bg="#E07A5F",   # initial brush color
            width=strip_width - 8 - 2 * self.INDICATOR_RING_PX,
            height=self.INDICATOR_H,
        )
        self._ind_swatch.pack(
            padx=self.INDICATOR_RING_PX, pady=self.INDICATOR_RING_PX
        )
        self._ind_swatch.pack_propagate(False)

        self._ind_label = tk.Label(
            header, text="—",
            bg="#1a1a1a", fg="#e0e0e0",
            font=("TkDefaultFont", 8),
        )
        self._ind_label.pack(side=tk.TOP, pady=(4, 0))

    def set_active_color(self, hexc: str, label: str = "") -> None:
        """Update the top-of-strip indicator. Called by the app on color pick."""
        self._ind_swatch.configure(bg=hexc)
        self._ind_label.configure(text=label or hexc)

    def _build_swatches(self) -> None:
        for group in COPIC_PALETTE:
            row = None
            for i, (code, hexc, name) in enumerate(group["colors"]):
                if i % self.COLS == 0:
                    row = tk.Frame(self.inner, bg="#1a1a1a")
                    row.pack(side=tk.TOP, anchor="w")
                sw = tk.Frame(
                    row, bg=hexc,
                    width=self.SWATCH, height=self.SWATCH,
                    cursor="hand2",
                )
                sw.pack(side=tk.LEFT, padx=self.MARGIN, pady=self.MARGIN)
                sw.pack_propagate(False)
                sw.bind(
                    "<Button-1>",
                    lambda e, h=hexc, c=code, n=name:
                        self._on_pick(h, f"{c} · {n}"),
                )
                sw.bind(
                    "<Enter>",
                    lambda e, c=code, n=name, h=hexc:
                        self.parent.title(f"{c} — {n} ({h})"),
                )
                sw.bind(
                    "<Leave>",
                    lambda e: self.parent.title("Grid Filler — 1920×1080"),
                )
            tk.Frame(self.inner, bg="#1a1a1a",
                     width=self.SWATCH, height=self.GROUP_GAP).pack(side=tk.TOP)

    def toggle(self) -> None:
        self.visible = not self.visible
        # Slide the strip out just to the right of the toggle column so
        # the open panel doesn't obscure the column itself. Nested col x.
        panel_x = TOGGLE_X_NESTED + self.toggle_btn.widget_width + ARROW_LABEL_GAP
        if self.visible:
            self.frame.place(x=panel_x, y=TOGGLE_Y_SWATCH)
            self.frame.tkraise()
            self.toggle_btn.set_direction("left")
            self.toggle_btn.tkraise()
            self.toggle_label.place_forget()
        else:
            self.frame.place_forget()
            self.toggle_btn.set_direction("right")
            self.toggle_label.place(
                x=TOGGLE_X_NESTED + self.toggle_btn.widget_width + ARROW_LABEL_GAP,
                y=self._toggle_label_y,
            )

    def refresh_bg(self) -> None:
        self.toggle_btn.refresh_bg()
        self.toggle_label.refresh_bg()

    def drawer_entries(self) -> list[dict]:
        label_x = (TOGGLE_X_NESTED + self.toggle_btn.widget_width
                   + ARROW_LABEL_GAP)
        swatch_center_dy = (self.toggle_btn.widget_height
                            - self.toggle_label.widget_height) // 2
        return [{
            "toggle": self.toggle_btn,
            "label": self.toggle_label,
            "toggle_x": TOGGLE_X_NESTED, "toggle_y": TOGGLE_Y_SWATCH,
            "label_x": label_x,
            "label_y": TOGGLE_Y_SWATCH + swatch_center_dy,
        }]


# ---------- Palette picker ----------

try:
    from curated_palettes import CATEGORIES as CURATED_CATEGORIES
    from curated_palettes import CURATED_PALETTES
except ImportError:
    CURATED_CATEGORIES = {}
    CURATED_PALETTES = []


class PalettePicker:
    """Chrome for the curated-palette panel — 35 named palettes grouped
    by category. Each palette renders as a labelled horizontal strip of
    larger swatches; clicking a swatch sets the active brush color.

    Layout mirrors PaletteStrip: indicator at top, scrollable body, same
    toggle/label conventions for the column-row entry point.
    """

    SWATCH_H = 40            # swatch height (each strip's row height)
    SWATCH_MIN_W = 240       # minimum width per swatch in a strip (4× larger)
    STRIP_GAP = 4            # vertical gap between palettes
    CATEGORY_GAP = 12        # vertical gap before each category header
    NAME_W = 160             # left gutter for palette name + desc
    PAD = 8
    INDICATOR_H = 28
    INDICATOR_RING = "#FFFFFF"
    INDICATOR_RING_PX = 3
    # Panel width budget — wide enough for the name gutter plus a strip
    # of up to ~5 swatches at SWATCH_MIN_W.
    PANEL_W = NAME_W + 5 * SWATCH_MIN_W + 4 * 1 + 2 * PAD   # ≈ 1380
    # Panel height — fits 35 palettes × (SWATCH_H + STRIP_GAP) plus
    # category headers + indicator + padding.
    PANEL_H = (
        35 * (SWATCH_H + STRIP_GAP)
        + len(CURATED_CATEGORIES) * (CATEGORY_GAP + 20)
        + INDICATOR_H + 2 * INDICATOR_RING_PX
        + 4 * PAD
    )

    def __init__(self, parent: tk.Misc,
                 on_pick: Callable[[str, str], None],
                 on_palette_pick: Callable[[dict], None] | None = None) -> None:
        self.parent = parent
        self._on_pick = on_pick
        self._on_palette_pick = on_palette_pick
        self.visible = False

        # Toggle + label live in the column slot at PALETTE row (nested
        # under COLOR alongside SWATCH).
        self.toggle_btn = GridArrowButton(
            parent, direction="right", command=self.toggle,
        )
        self.toggle_btn.place(x=TOGGLE_X_NESTED, y=TOGGLE_Y_PALETTE)
        label_x = (TOGGLE_X_NESTED + self.toggle_btn.widget_width
                   + ARROW_LABEL_GAP)
        self.toggle_label = PixelLabel(parent, text="PALETTE")
        center_dy = (self.toggle_btn.widget_height
                     - self.toggle_label.widget_height) // 2
        self.toggle_label.place(x=label_x, y=TOGGLE_Y_PALETTE + center_dy)
        self._toggle_label_y = TOGGLE_Y_PALETTE + center_dy

        self.frame = tk.Frame(
            parent, bg="#1a1a1a", borderwidth=1, relief="solid",
            width=self.PANEL_W,
        )
        self.frame.pack_propagate(False)
        self._build_indicator()
        self._build_body()

    def _build_indicator(self) -> None:
        """Top-of-panel active-color indicator — matches the SWATCH chrome."""
        header = tk.Frame(self.frame, bg="#1a1a1a", padx=self.PAD, pady=6)
        header.pack(side=tk.TOP, fill=tk.X)
        ring = tk.Frame(
            header, bg=self.INDICATOR_RING,
            width=self.PANEL_W - 2 * self.PAD,
            height=self.INDICATOR_H + 2 * self.INDICATOR_RING_PX,
        )
        ring.pack(side=tk.TOP)
        ring.pack_propagate(False)
        self._ind_swatch = tk.Frame(
            ring, bg="#E07A5F",
            width=self.PANEL_W - 2 * self.PAD - 2 * self.INDICATOR_RING_PX,
            height=self.INDICATOR_H,
        )
        self._ind_swatch.pack(
            padx=self.INDICATOR_RING_PX, pady=self.INDICATOR_RING_PX,
        )
        self._ind_swatch.pack_propagate(False)
        self._ind_label = tk.Label(
            header, text="—",
            bg="#1a1a1a", fg="#e0e0e0",
            font=("TkDefaultFont", 8),
        )
        self._ind_label.pack(side=tk.TOP, pady=(4, 0))

    def _build_body(self) -> None:
        """Scrollable body — categories with their palettes underneath.
        Built with a Canvas + inner Frame so we can scroll past 35 strips
        if the window is shorter than PANEL_H."""
        body = tk.Frame(self.frame, bg="#1a1a1a")
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(body, bg="#1a1a1a", highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = tk.Frame(canvas, bg="#1a1a1a")
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        # Inner frame's width must follow the canvas's actual width or its
        # children can't expand to fill — they'd shrink to their req sizes.
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(inner_id, width=e.width),
        )
        canvas.bind("<Enter>", lambda e: self._bind_wheel(canvas))
        canvas.bind("<Leave>", lambda e: self._unbind_wheel(canvas))

        # Group palettes by category, preserving the dict's insertion order.
        by_cat: dict[str, list[dict]] = {k: [] for k in CURATED_CATEGORIES}
        for p in CURATED_PALETTES:
            by_cat.setdefault(p["category"], []).append(p)

        for cat, palettes in by_cat.items():
            if not palettes:
                continue
            cat_meta = CURATED_CATEGORIES.get(cat, {"label": cat, "note": ""})
            header_frame = tk.Frame(inner, bg="#1a1a1a", padx=self.PAD)
            header_frame.pack(side=tk.TOP, anchor="w", fill=tk.X,
                              pady=(self.CATEGORY_GAP, 4))
            tk.Label(
                header_frame, text=cat_meta["label"].upper(),
                bg="#1a1a1a", fg="#bbb",
                font=("TkDefaultFont", 8, "bold"),
            ).pack(side=tk.LEFT)
            tk.Label(
                header_frame, text=f"  {cat_meta['note']}",
                bg="#1a1a1a", fg="#666",
                font=("TkDefaultFont", 8),
            ).pack(side=tk.LEFT)

            for p in palettes:
                self._build_palette_row(inner, p)

    def _build_palette_row(self, parent: tk.Misc, palette: dict) -> None:
        """One labelled row: name on the left, strip of clickable swatches."""
        row = tk.Frame(parent, bg="#1a1a1a",
                       padx=self.PAD, pady=self.STRIP_GAP // 2)
        row.pack(side=tk.TOP, anchor="w", fill=tk.X)

        name_col = tk.Frame(row, bg="#1a1a1a", width=self.NAME_W,
                            cursor="hand2")
        name_col.pack(side=tk.LEFT, anchor="n")
        name_col.pack_propagate(False)
        name_lbl = tk.Label(
            name_col, text=palette["name"],
            bg="#1a1a1a", fg="#e0e0e0",
            font=("TkDefaultFont", 9, "bold"),
            anchor="w", justify="left", cursor="hand2",
        )
        name_lbl.pack(side=tk.TOP, anchor="w", fill=tk.X)
        desc_lbl = tk.Label(
            name_col, text=palette["desc"],
            bg="#1a1a1a", fg="#888",
            font=("TkDefaultFont", 7),
            anchor="w", justify="left", wraplength=self.NAME_W - 8,
            cursor="hand2",
        )
        desc_lbl.pack(side=tk.TOP, anchor="w", fill=tk.X)

        # Clicking the name column selects the whole palette (vs clicking
        # an individual swatch, which picks just one color).
        if self._on_palette_pick is not None:
            for w in (name_col, name_lbl, desc_lbl):
                w.bind(
                    "<Button-1>",
                    lambda e, p=palette: self._on_palette_pick(p),
                )

        strip = tk.Frame(row, bg="#1a1a1a", height=self.SWATCH_H)
        strip.pack(side=tk.LEFT, fill=tk.X, expand=True)
        strip.pack_propagate(False)

        for code, hexc, name in palette["colors"]:
            sw = tk.Frame(
                strip, bg=hexc,
                width=self.SWATCH_MIN_W,
                height=self.SWATCH_H,
                cursor="hand2",
            )
            sw.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1)
            sw.pack_propagate(False)
            sw.bind(
                "<Button-1>",
                lambda e, h=hexc, c=code, n=name:
                    self._on_pick(h, f"{c} · {n}"),
            )
            sw.bind(
                "<Enter>",
                lambda e, c=code, n=name, h=hexc, pn=palette["name"]:
                    self.parent.master.title(
                        f"{pn} → {c} — {n} ({h})"
                    ) if hasattr(self.parent, "master") else None,
            )

    def _bind_wheel(self, canvas: tk.Canvas) -> None:
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"),
        )
        canvas.bind_all("<Button-4>",
                        lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>",
                        lambda e: canvas.yview_scroll(1, "units"))

    def _unbind_wheel(self, canvas: tk.Canvas) -> None:
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    def set_active_color(self, hexc: str, label: str = "") -> None:
        self._ind_swatch.configure(bg=hexc)
        self._ind_label.configure(text=label or hexc)

    def toggle(self) -> None:
        self.visible = not self.visible
        panel_x = (TOGGLE_X_NESTED + self.toggle_btn.widget_width
                   + ARROW_LABEL_GAP)
        if self.visible:
            # Clamp panel height to what the canvas can show.
            try:
                parent_h = self.parent.winfo_height()
                h = min(self.PANEL_H, max(200, parent_h - TOGGLE_Y_PALETTE - 8))
            except tk.TclError:
                h = self.PANEL_H
            self.frame.configure(height=h)
            self.frame.place(x=panel_x, y=TOGGLE_Y_PALETTE)
            self.frame.tkraise()
            self.toggle_btn.set_direction("left")
            self.toggle_btn.tkraise()
            self.toggle_label.place_forget()
        else:
            self.frame.place_forget()
            self.toggle_btn.set_direction("right")
            self.toggle_label.place(
                x=TOGGLE_X_NESTED + self.toggle_btn.widget_width + ARROW_LABEL_GAP,
                y=self._toggle_label_y,
            )

    def refresh_bg(self) -> None:
        self.toggle_btn.refresh_bg()
        self.toggle_label.refresh_bg()

    def drawer_entries(self) -> list[dict]:
        label_x = (TOGGLE_X_NESTED + self.toggle_btn.widget_width
                   + ARROW_LABEL_GAP)
        center_dy = (self.toggle_btn.widget_height
                     - self.toggle_label.widget_height) // 2
        return [{
            "toggle": self.toggle_btn,
            "label": self.toggle_label,
            "toggle_x": TOGGLE_X_NESTED, "toggle_y": TOGGLE_Y_PALETTE,
            "label_x": label_x,
            "label_y": TOGGLE_Y_PALETTE + center_dy,
        }]


# ---------- Selected palette strip (top-right) ----------

class SelectedPaletteStrip:
    """Floating top-right strip showing the currently selected curated
    palette's name + clickable swatches. Updates whenever the user
    selects a palette (via clicking its name in the PALETTE picker).
    Each swatch click routes through the same on_pick callback as the
    panel swatches, so picking from here sets the brush color uniformly.
    """

    SWATCH_W = 36
    SWATCH_H = 28
    GUTTER = 8
    PAD_X = 8
    PAD_Y = 6
    EDGE_MARGIN = 8

    def __init__(self, parent: tk.Misc,
                 on_pick: Callable[[str, str], None]) -> None:
        self.parent = parent
        self._on_pick = on_pick
        self._current_palette: dict | None = None
        bg = _parent_bg(parent)
        self.frame = tk.Frame(
            parent, bg=bg, padx=self.PAD_X, pady=self.PAD_Y,
        )
        self.name_label = PixelLabel(self.frame, text=" ")
        self.name_label.pack(side=tk.LEFT, padx=(0, self.GUTTER))
        self.strip = tk.Frame(self.frame, bg=bg)
        self.strip.pack(side=tk.LEFT)
        # Start hidden until something is selected.
        # Reposition on window resize so we stay pinned right.
        parent.bind("<Configure>", self._reposition, add="+")

    def set_palette(self, palette: dict) -> None:
        """Replace the strip's contents with this palette's colors."""
        self._current_palette = palette
        # Rebuild the name label as a fresh PixelLabel (PixelLabel doesn't
        # support text mutation in place — cheaper to swap than refactor).
        self.name_label.destroy()
        self.name_label = PixelLabel(self.frame, text=palette["name"].upper())
        self.name_label.pack(side=tk.LEFT, padx=(0, self.GUTTER),
                             before=self.strip)
        for child in self.strip.winfo_children():
            child.destroy()
        for code, hexc, name in palette["colors"]:
            sw = tk.Frame(
                self.strip, bg=hexc,
                width=self.SWATCH_W, height=self.SWATCH_H,
                cursor="hand2",
            )
            sw.pack(side=tk.LEFT, padx=1)
            sw.pack_propagate(False)
            sw.bind(
                "<Button-1>",
                lambda e, h=hexc, c=code, n=name:
                    self._on_pick(h, f"{c} · {n}"),
            )
        self.frame.update_idletasks()
        self._show()
        self._reposition()

    def _show(self) -> None:
        self.frame.place(x=self._x(), y=self.EDGE_MARGIN)
        self.frame.tkraise()

    def _x(self) -> int:
        try:
            w = self.parent.winfo_width()
        except tk.TclError:
            return 0
        self.frame.update_idletasks()
        return max(self.EDGE_MARGIN,
                   w - self.frame.winfo_reqwidth() - self.EDGE_MARGIN)

    def _reposition(self, _ev=None) -> None:
        if self._current_palette is None:
            return
        self.frame.place_configure(x=self._x(), y=self.EDGE_MARGIN)

    def refresh_bg(self) -> None:
        bg = _parent_bg(self.parent)
        self.frame.configure(bg=bg)
        self.strip.configure(bg=bg)
        if self.name_label is not None:
            self.name_label.refresh_bg()


# ---------- Stub chrome ----------

class StubChrome:
    """A blank arrow + label that occupies a column slot but has no
    behavior yet. Used to reserve a slot for a feature about to land,
    so the drawer hierarchy stays correct while we build the panel."""

    def __init__(self, parent: tk.Misc, *,
                 label: str, toggle_x: int, toggle_y: int) -> None:
        self.parent = parent
        self.TOGGLE_X = toggle_x
        self.TOGGLE_Y = toggle_y
        self.toggle_btn = GridArrowButton(
            parent, direction="right", command=self._noop,
        )
        self.toggle_btn.place(x=toggle_x, y=toggle_y)
        self.toggle_label = PixelLabel(parent, text=label)
        self.toggle_label.place(x=self._label_x(), y=self._label_y())

    def _label_x(self) -> int:
        return self.TOGGLE_X + self.toggle_btn.widget_width + ARROW_LABEL_GAP

    def _label_y(self) -> int:
        return self.TOGGLE_Y + (
            self.toggle_btn.widget_height - self.toggle_label.widget_height
        ) // 2

    def _noop(self) -> None:
        # Placeholder until the corresponding panel is implemented.
        pass

    def refresh_bg(self) -> None:
        self.toggle_btn.refresh_bg()
        self.toggle_label.refresh_bg()

    def drawer_entries(self) -> list[dict]:
        return [{
            "toggle": self.toggle_btn,
            "label": self.toggle_label,
            "toggle_x": self.TOGGLE_X, "toggle_y": self.TOGGLE_Y,
            "label_x": self._label_x(), "label_y": self._label_y(),
        }]


# ---------- Copic modal picker ----------

class CopicPicker(tk.Toplevel):
    """Modal palette browser for the full Copic Sketch set, grouped by family."""

    SWATCH_W = 44
    SWATCH_H = 32
    CARD_W = 64
    CARD_H = 60

    def __init__(self, parent: tk.Misc,
                 on_pick: Callable[[str, str], None]) -> None:
        super().__init__(parent)
        self.title("Copic palette")
        self.on_pick = on_pick
        self.configure(bg="#1e1e1e")
        self.geometry("1480x820")

        self._build_search()
        self._build_body()
        self._current_filter = ""
        self._render(filter_text="")
        self.transient(parent)
        # grab_set requires the window to be viewable — defer until after
        # Tk has mapped the modal to screen.
        self.after(0, self._try_grab)
        self.bind("<Configure>", self._on_configure)
        self._last_width = 0

    def _try_grab(self) -> None:
        try:
            self.grab_set()
        except tk.TclError:
            # Window vanished before grab could land; nothing to do.
            pass

    def _build_search(self) -> None:
        bar = tk.Frame(self, bg="#252526", padx=8, pady=6)
        bar.pack(side=tk.TOP, fill=tk.X)
        tk.Label(bar, text="Filter:", bg="#252526", fg="#ddd").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self.filter_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.filter_var, width=40)
        ent.pack(side=tk.LEFT, padx=4)
        ent.bind(
            "<KeyRelease>",
            lambda e: self._render(self.filter_var.get().strip().lower()),
        )
        tk.Label(bar, text="(code or name)", bg="#252526", fg="#888").pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(bar, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)

    def _build_body(self) -> None:
        outer = tk.Frame(self, bg="#1e1e1e")
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.canvas = tk.Canvas(outer, bg="#1e1e1e", highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = tk.Frame(self.canvas, bg="#1e1e1e")
        self._inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _ev=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def _unbind_wheel(self, _ev=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, ev) -> None:
        self.canvas.yview_scroll(int(-ev.delta / 120), "units")

    def _render(self, filter_text: str) -> None:
        self._current_filter = filter_text
        for child in self.inner.winfo_children():
            child.destroy()

        avail = max(self.canvas.winfo_width() - 16, 600)
        per_row = max(1, avail // self.CARD_W)

        for group in COPIC_PALETTE:
            matches = [
                (code, hexc, name) for code, hexc, name in group["colors"]
                if not filter_text
                or filter_text in code.lower()
                or filter_text in name.lower()
                or filter_text in group["name"].lower()
            ]
            if not matches:
                continue

            header = tk.Frame(self.inner, bg="#1e1e1e", padx=4, pady=6)
            header.pack(fill=tk.X, anchor="w")
            tk.Label(
                header,
                text=f"{group['name']} — {group['desc']}  ({len(matches)})",
                bg="#1e1e1e", fg="#e0e0e0",
                font=("TkDefaultFont", 10, "bold"),
            ).pack(side=tk.LEFT)

            row = tk.Frame(self.inner, bg="#1e1e1e")
            row.pack(fill=tk.X, anchor="w", padx=4)
            for i, (code, hexc, name) in enumerate(matches):
                if i and i % per_row == 0:
                    row = tk.Frame(self.inner, bg="#1e1e1e")
                    row.pack(fill=tk.X, anchor="w", padx=4)
                self._swatch(row, code, hexc, name)

    def _on_configure(self, ev) -> None:
        if ev.widget is not self:
            return
        w = self.winfo_width()
        if abs(w - self._last_width) > self.CARD_W // 2:
            self._last_width = w
            self.after_idle(lambda: self._render(self._current_filter))

    def _swatch(self, parent, code: str, hexc: str, name: str) -> None:
        cell = tk.Frame(parent, bg="#1e1e1e", padx=2, pady=2)
        cell.pack(side=tk.LEFT)
        sw = tk.Label(
            cell, bg=hexc, width=5, height=2, relief="raised", borderwidth=1,
            cursor="hand2",
        )
        sw.pack()
        tk.Label(
            cell, text=code, bg="#1e1e1e", fg="#bbb", font=("TkDefaultFont", 8),
        ).pack()
        tip = f"{code} — {name}\n{hexc}"
        sw.bind("<Button-1>",
                lambda e, h=hexc, c=code, n=name: self._select(h, c, n))
        sw.bind("<Enter>", lambda e, t=tip: self._set_status(t))
        sw.bind("<Leave>", lambda e: self._set_status(""))

    def _set_status(self, text: str) -> None:
        self.title(f"Copic palette  {('— ' + text.splitlines()[0]) if text else ''}")

    def _select(self, hexc: str, code: str, name: str) -> None:
        self.on_pick(hexc, f"{code} · {name}")
        self.destroy()

    def destroy(self) -> None:
        try:
            self._unbind_wheel()
        except Exception:
            pass
        super().destroy()


# ---------- Admin panel (hidden) ----------

class AdminPanel(tk.Toplevel):
    """Hidden admin panel revealed by Ctrl+Shift+A.

    Hosts a single tool: the Color Prevalence Dashboard. Each color used
    in the Document gets its own row containing a bar of filled cubes
    whose length is proportional to that color's share of the painted
    cells. Rows are sorted by prevalence, most-used at top. The
    dashboard itself is rendered as a grid — the medium is the message.
    """

    # Dashboard grid geometry, in pixels.
    CUBE = 14
    CUBE_GAP = 1
    ROW_GAP = 8
    MAX_BAR_CUBES = 40         # bar width cap; one cube == 1/MAX_BAR_CUBES share
    LABEL_W = 200              # left gutter for color name + count
    PAD = 16
    HEADER_H = 32

    def __init__(self, parent: tk.Misc, doc: Document) -> None:
        super().__init__(parent)
        self.doc = doc
        self.title("Admin · Color Prevalence Dashboard")
        self.configure(bg="#0f0f0f")
        self.transient(parent)
        self.bind("<Escape>", lambda e: self.destroy())

        self._build_header()
        self._build_canvas()
        self._render()

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg="#1a1a1a", padx=self.PAD, pady=8)
        bar.pack(side=tk.TOP, fill=tk.X)
        tk.Label(
            bar, text="Color Prevalence Dashboard",
            bg="#1a1a1a", fg="#e0e0e0",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            bar, text="press Esc to close",
            bg="#1a1a1a", fg="#666",
            font=("TkDefaultFont", 8),
        ).pack(side=tk.RIGHT)

    def _build_canvas(self) -> None:
        wrapper = tk.Frame(self, bg="#0f0f0f")
        wrapper.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(
            wrapper, bg="#0f0f0f", highlightthickness=0,
        )
        vsb = ttk.Scrollbar(wrapper, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _color_counts(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for color in self.doc.cell_fills.values():
            counts[color] = counts.get(color, 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    def _render(self) -> None:
        self.canvas.delete("all")
        counts = self._color_counts()
        total = sum(c for _, c in counts)

        if total == 0:
            self.canvas.create_text(
                self.PAD, self.PAD, anchor="nw",
                text="No painted cells yet.",
                fill="#888", font=("TkDefaultFont", 10),
            )
            self.canvas.configure(scrollregion=(0, 0, 400, 80))
            self.geometry("520x180")
            return

        y = self.PAD
        # Header row: column legend.
        self.canvas.create_text(
            self.PAD, y, anchor="nw",
            text="color",
            fill="#888", font=("TkDefaultFont", 8, "bold"),
        )
        self.canvas.create_text(
            self.PAD + self.LABEL_W, y, anchor="nw",
            text=f"prevalence  (1 cube ≈ {100/self.MAX_BAR_CUBES:.1f}% of {total} cells)",
            fill="#888", font=("TkDefaultFont", 8, "bold"),
        )
        y += self.HEADER_H

        row_step = self.CUBE + self.ROW_GAP

        for color, count in counts:
            share = count / total
            # Cubes drawn: at least 1 if the color appears at all, else 0.
            cubes = max(1, round(share * self.MAX_BAR_CUBES))

            # Left gutter: hex code + count + percentage
            label = f"{color}   {count}   {share*100:.1f}%"
            self.canvas.create_text(
                self.PAD, y + self.CUBE // 2,
                anchor="w", text=label,
                fill="#e0e0e0", font=("TkDefaultFont", 9),
            )

            # Bar of filled cubes in the color itself.
            x0 = self.PAD + self.LABEL_W
            for i in range(cubes):
                x = x0 + i * (self.CUBE + self.CUBE_GAP)
                self.canvas.create_rectangle(
                    x, y, x + self.CUBE, y + self.CUBE,
                    fill=color, outline="",
                )

            y += row_step

        # Resize window + scrollregion to fit content.
        content_h = y + self.PAD
        content_w = (
            self.PAD + self.LABEL_W
            + self.MAX_BAR_CUBES * (self.CUBE + self.CUBE_GAP)
            + self.PAD
        )
        self.canvas.configure(scrollregion=(0, 0, content_w, content_h))
        # Cap window height so very colorful canvases stay scrollable.
        win_h = min(content_h + 60, 720)
        self.geometry(f"{content_w + 20}x{win_h}")


# ---------- Line strip ----------

# Presets: (width_px, height_px). Squares first, then ratio'd oblongs in
# both orientations. The widget previews each as its actual W×H shape.
LINE_PRESETS: list[tuple[int, int]] = [
    # Squares
    (1, 1), (2, 2), (4, 4), (8, 8), (16, 16), (32, 32), (64, 64), (100, 100),
    # Horizontal-major oblongs (wider than tall)
    (2, 1), (4, 2), (8, 4), (16, 8), (32, 16), (64, 32),
    # Vertical-major oblongs (taller than wide)
    (1, 2), (2, 4), (4, 8), (8, 16), (16, 32), (32, 64),
]


class LineStrip:
    """Vertical line-size palette pinned to the right edge.

    Mirrors PaletteStrip in layout: a ▶ toggle anchored to the right edge,
    a 12-col grid of swatches (each rendered as the actual W×H shape it
    represents), and a top-of-strip indicator showing the active size with
    a white outline ring.
    """

    SWATCH = 96                  # cell box for each preset preview
    MARGIN = 4
    GROUP_GAP = 8
    COLS = 5                     # 20 presets → 4 rows of 5
    INDICATOR_H = 56
    INDICATOR_RING = "#FFFFFF"
    INDICATOR_RING_PX = 4

    TOGGLE_X = TOGGLE_COL_X
    TOGGLE_Y = TOGGLE_Y_LINE

    def __init__(self, parent: tk.Misc,
                 on_pick: Callable[[tuple[int, int], str], None]) -> None:
        self.parent = parent
        self._on_pick = on_pick
        self.visible = False
        # Active color drives the fill of every preview rect (indicator
        # swatch + all 20 preset swatches), so the line strip mirrors the
        # palette pick in real time.
        self._active_color: str = "#E07A5F"
        # Per-swatch (canvas, rect_item_id) pairs so set_active_color can
        # recolor every preview in O(presets).
        self._swatch_items: list[tuple[tk.Canvas, int]] = []
        self._active_size: tuple[int, int] = (8, 8)

        self.toggle_btn = GridArrowButton(
            parent, direction="right", command=self.toggle,
        )
        self.toggle_btn.place(x=self.TOGGLE_X, y=self.TOGGLE_Y)
        # Adjacent pixel-art label, centered on the arrow tip.
        self.toggle_label = PixelLabel(parent, text="LINE")
        self.toggle_label.place(x=self._label_x(), y=self._label_y())

        self.frame = tk.Frame(parent, bg="#1a1a1a", borderwidth=1, relief="solid")
        self.inner = tk.Frame(self.frame, bg="#1a1a1a")
        self.inner.pack(side=tk.TOP, anchor="nw")
        self._build_indicator()
        self._build_swatches()

    def _label_x(self) -> int:
        return self.TOGGLE_X + self.toggle_btn.widget_width + ARROW_LABEL_GAP

    def _label_y(self) -> int:
        return self.TOGGLE_Y + (
            self.toggle_btn.widget_height - self.toggle_label.widget_height
        ) // 2

    def _panel_x(self) -> int:
        # Strip slides out just to the right of the toggle column.
        return self.TOGGLE_X + self.toggle_btn.widget_width + ARROW_LABEL_GAP

    def refresh_bg(self) -> None:
        self.toggle_btn.refresh_bg()
        self.toggle_label.refresh_bg()

    def drawer_entries(self) -> list[dict]:
        return [{
            "toggle": self.toggle_btn,
            "label": self.toggle_label,
            "toggle_x": self.TOGGLE_X, "toggle_y": self.TOGGLE_Y,
            "label_x": self._label_x(), "label_y": self._label_y(),
        }]

    def _build_indicator(self) -> None:
        strip_width = self.COLS * (self.SWATCH + 2 * self.MARGIN)
        header = tk.Frame(self.inner, bg="#1a1a1a", padx=4, pady=6)
        header.pack(side=tk.TOP, fill=tk.X)

        self._ind_ring = tk.Frame(
            header, bg=self.INDICATOR_RING,
            width=strip_width - 8,
            height=self.INDICATOR_H + 2 * self.INDICATOR_RING_PX,
        )
        self._ind_ring.pack(side=tk.TOP)
        self._ind_ring.pack_propagate(False)

        # The indicator's "swatch" is itself a tiny canvas so we can draw
        # the active preset's actual rectangle inside.
        self._ind_canvas = tk.Canvas(
            self._ind_ring,
            width=strip_width - 8 - 2 * self.INDICATOR_RING_PX,
            height=self.INDICATOR_H,
            bg="#2a2a2a", highlightthickness=0,
        )
        self._ind_canvas.pack(
            padx=self.INDICATOR_RING_PX, pady=self.INDICATOR_RING_PX
        )
        self._ind_label = tk.Label(
            header, text="—",
            bg="#1a1a1a", fg="#e0e0e0",
            font=("TkDefaultFont", 8),
        )
        self._ind_label.pack(side=tk.TOP, pady=(4, 0))
        # Initial draw at the default brush size.
        self.set_active_size((8, 8))

    def set_active_size(self, size: tuple[int, int]) -> None:
        self._active_size = size
        self._ind_canvas.delete("all")
        cw = int(self._ind_canvas.cget("width"))
        ch = int(self._ind_canvas.cget("height"))
        w, h = size
        # Cap preview to canvas size so 100×100 still fits.
        scale = min(1.0, (cw - 8) / max(w, 1), (ch - 8) / max(h, 1))
        pw = max(1, int(w * scale))
        ph = max(1, int(h * scale))
        x0 = (cw - pw) // 2
        y0 = (ch - ph) // 2
        self._ind_canvas.create_rectangle(
            x0, y0, x0 + pw, y0 + ph,
            fill=self._active_color, outline="",
        )
        self._ind_label.configure(text=f"{w} × {h} px")

    def set_active_color(self, hexc: str) -> None:
        """Recolor every preview rect (indicator + all 20 swatches) so the
        line strip mirrors the active brush color in real time."""
        self._active_color = hexc
        # Recolor every preset preview.
        for cv, rect_id in self._swatch_items:
            cv.itemconfigure(rect_id, fill=hexc)
        # Re-render the indicator with the new color at the current size.
        self.set_active_size(self._active_size)

    def _build_swatches(self) -> None:
        row = None
        for i, size in enumerate(LINE_PRESETS):
            if i % self.COLS == 0:
                row = tk.Frame(self.inner, bg="#1a1a1a")
                row.pack(side=tk.TOP, anchor="w")
            self._build_swatch(row, size)
        # Extra trailing pad to match the palette strip's visual rhythm.
        tk.Frame(self.inner, bg="#1a1a1a",
                 width=self.SWATCH, height=self.GROUP_GAP).pack(side=tk.TOP)

    def _build_swatch(self, parent: tk.Misc, size: tuple[int, int]) -> None:
        cell = tk.Frame(
            parent, bg="#1a1a1a",
            width=self.SWATCH + 2 * self.MARGIN,
            height=self.SWATCH + 2 * self.MARGIN,
            cursor="hand2",
        )
        cell.pack(side=tk.LEFT, padx=self.MARGIN, pady=self.MARGIN)
        cell.pack_propagate(False)

        cv = tk.Canvas(
            cell, width=self.SWATCH, height=self.SWATCH,
            bg="#2a2a2a", highlightthickness=0,
        )
        cv.pack(padx=self.MARGIN, pady=self.MARGIN)

        w, h = size
        scale = min(1.0, (self.SWATCH - 4) / max(w, 1),
                    (self.SWATCH - 4) / max(h, 1))
        pw = max(1, int(w * scale))
        ph = max(1, int(h * scale))
        x0 = (self.SWATCH - pw) // 2
        y0 = (self.SWATCH - ph) // 2
        rect_id = cv.create_rectangle(
            x0, y0, x0 + pw, y0 + ph,
            fill=self._active_color, outline="",
        )
        # Register for live recoloring on color pick.
        self._swatch_items.append((cv, rect_id))

        label = f"{w}×{h}"

        def _click(e, sz=size, lbl=label):
            self._on_pick(sz, lbl)
            # Collapse the panel after a pick — the user has chosen, get out
            # of their way.
            if self.visible:
                self.toggle()

        for widget in (cell, cv):
            widget.bind("<Button-1>", _click)
            widget.bind(
                "<Enter>",
                lambda e, lbl=label: self.parent.title(f"Line {lbl} px"),
            )
            widget.bind(
                "<Leave>",
                lambda e: self.parent.title("Grid Filler — 1920×1080"),
            )

    def toggle(self) -> None:
        self.visible = not self.visible
        if self.visible:
            self.frame.place(x=self._panel_x(), y=self.TOGGLE_Y)
            self.frame.tkraise()
            self.toggle_btn.set_direction("left")
            self.toggle_btn.tkraise()
            self.toggle_label.place_forget()
        else:
            self.frame.place_forget()
            self.toggle_btn.set_direction("right")
            self.toggle_label.place(x=self._label_x(), y=self._label_y())
