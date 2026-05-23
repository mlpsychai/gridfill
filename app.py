"""Application shell: wires Document, Tools, Renderer, and UI together.

The Tk root owns:
  - a Document (state)
  - an UndoStack against that Document
  - a BrushContext (live brush settings)
  - one Tool per mode, picked by the mode StringVar
  - a TkRenderer for the canvas, an SvgRenderer for export
  - a Toolbar and PaletteStrip widget

Event routing is one-line: forward to the active tool.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

# When grid_fill.py launches us, sys.path may not include this directory.
sys.path.insert(0, str(Path(__file__).parent))

from renderer import (
    CANVAS_H,
    CANVAS_W,
    CELL_SIZE,
    SvgRenderer,
    TkRenderer,
)
from state import BrushPattern, ClearAction, Document, UndoStack
from tools import (
    BrushContext,
    CellTool,
    DashPhase,
    EraserTool,
    FreeDrawTool,
    SheetPressTool,
    Tool,
    snapped_footprint,
)
from ui import (
    ActionsCard, AdminPanel, Drawer, LineStrip, PaletteStrip, PalettePicker,
    SelectedPaletteStrip, Toolbar,
)

CHROME_W = 0
CHROME_H = 36
DEFAULT_FILL = "#E07A5F"


class GridFiller(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Grid Filler — 1920×1080")
        self.configure(bg="#1e1e1e")

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        # Initial geometry: 90% of screen, then fit canvas inside. Live
        # fit-to-window recompute happens in _on_root_configure.
        win_w = int(sw * 0.9)
        win_h = int(sh * 0.9)
        self.geometry(f"{win_w}x{win_h}")
        max_by_w = (win_w - CHROME_W) / CANVAS_W
        max_by_h = (win_h - CHROME_H) / CANVAS_H
        self.display_scale = max(0.1, min(1.0, max_by_w, max_by_h))
        self._last_fit_size: tuple[int, int] = (0, 0)

        # State
        self.doc = Document()
        self.undo_stack = UndoStack(self.doc)
        self.brush = BrushContext(
            color=DEFAULT_FILL,
            line_size=(8, 8),
            erase_radius=20,
            pattern=BrushPattern("Solid", 0, 0),
        )
        self.dash = DashPhase()

        # Tk-bound vars used by the toolbar
        self.mode_var = tk.StringVar(value="cell")
        # Surface preset: paper color + whether the grid lattice draws.
        # Default is "Sandbox" — sandy paper with a pre-painted wooden rim
        # approximating a top-down sandbox shape.
        self.surface_var = tk.StringVar(value="Sandbox")

        self._cursor_ring: int | None = None
        self._last_cursor_xy: tuple[float, float] | None = None

        self._build_canvas()
        self.renderer = TkRenderer(self.canvas, self.display_scale)
        self.svg_renderer = SvgRenderer()
        self.renderer.draw_grid(self._grid_color())

        # Tools — one per mode
        self.tools: dict[str, Tool] = {
            "cell": CellTool(self.doc, self.undo_stack, self.brush,
                             self.renderer, self.dash, self._redraw_all),
            "free": FreeDrawTool(self.doc, self.undo_stack, self.brush,
                                 self.renderer, self.dash, self._redraw_all),
            "sheet": SheetPressTool(self.doc, self.undo_stack, self.brush,
                                    self.renderer, self.dash, self._redraw_all),
            "erase": EraserTool(self.doc, self.undo_stack, self.brush,
                                self.renderer, self.dash, self._redraw_all),
        }

        # Chrome widgets are parented to the canvas so their bg can match
        # the active surface paper color — giving the illusion of true
        # transparency. Reparenting to `self` (root) showed a dark grey
        # rectangle behind every toggle/label.
        self.toolbar = Toolbar(
            self.canvas,
            mode_var=self.mode_var,
            surface_var=self.surface_var,
            surfaces=list(self.SURFACES.keys()),
            get_pattern=lambda: self.brush.pattern,
            set_pattern=self._set_pattern,
            get_color=lambda: self.brush.color,
            on_mode_change=self._on_mode_change,
            on_surface_change=self._apply_surface,
            on_auto_toggle=self._toggle_auto,
        )
        self.actions_card = ActionsCard(
            self.canvas,
            on_undo=self._undo,
            on_redo=self._redo,
            on_clear=self._clear,
            on_save=self._save_svg,
        )
        self.palette = PaletteStrip(self.canvas, on_pick=self._set_color)
        self.line_strip = LineStrip(self.canvas, on_pick=self._set_line_size)

        # Master Drawer: single arrow that expands/collapses every sub-
        # toggle in the chrome column. Each chrome class advertises its
        # own entries via drawer_entries(). SWATCH + PALETTE are nested
        # one level deeper under a COLOR sub-drawer.
        from ui import (
            TOGGLE_COL_X, TOGGLE_X_NESTED, TOGGLE_Y_COLOR, TOGGLE_Y_PALETTE,
        )
        self.drawer = Drawer(self.canvas)
        for chrome in (self.toolbar, self.line_strip, self.actions_card):
            for entry in chrome.drawer_entries():
                self.drawer.register(**entry)
        self.color_drawer = Drawer(
            self.canvas,
            x=TOGGLE_COL_X, y=TOGGLE_Y_COLOR,
            label="COLOR",
            parent_drawer=self.drawer,
        )
        for entry in self.palette.drawer_entries():
            self.color_drawer.register(**entry)
        # PALETTE: curated palette browser (35 named sets in 5 categories).
        # Picks route through the same _set_color callback as SWATCH so the
        # brush updates uniformly regardless of which panel was used.
        # Selecting a whole palette (clicking its name) shows the strip in
        # the top-right via SelectedPaletteStrip.
        self.selected_palette = SelectedPaletteStrip(
            self.canvas, on_pick=self._set_color,
        )
        self.palette_picker = PalettePicker(
            self.canvas, on_pick=self._set_color,
            on_palette_pick=self._on_palette_pick,
        )
        for entry in self.palette_picker.drawer_entries():
            self.color_drawer.register(**entry)

        self._bind_keys()
        # Fit-to-window: recompute scale whenever the root window resizes.
        self.bind("<Configure>", self._on_root_configure, add="+")
        # Apply the default surface (background + initial template if any).
        self._apply_surface()
        if self.surface_var.get() == "Sandbox":
            self._load_sandbox_template()

    # ---------- Canvas ----------

    def _build_canvas(self) -> None:
        w = int(CANVAS_W * self.display_scale)
        h = int(CANVAS_H * self.display_scale)
        self.canvas = tk.Canvas(self, width=w, height=h, bg="white",
                                highlightthickness=0, cursor="none")
        self.canvas.pack(padx=0, pady=0)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", lambda e: self._hide_cursor_ring())

    def _to_svg(self, x: float, y: float) -> tuple[float, float]:
        return x / self.display_scale, y / self.display_scale

    # ---------- Fit-to-window ----------

    def _on_root_configure(self, ev: tk.Event) -> None:
        """Recompute display_scale to fit the canvas inside the current
        window. Tk fires <Configure> for every widget; ignore non-root and
        no-op size changes to avoid render thrash."""
        if ev.widget is not self:
            return
        size = (self.winfo_width(), self.winfo_height())
        if size == self._last_fit_size:
            return
        self._last_fit_size = size
        self._refit_canvas(*size)

    def _refit_canvas(self, win_w: int, win_h: int) -> None:
        max_by_w = (win_w - CHROME_W) / CANVAS_W
        max_by_h = (win_h - CHROME_H) / CANVAS_H
        new_scale = max(0.1, min(1.0, max_by_w, max_by_h))
        if abs(new_scale - self.display_scale) < 1e-4:
            return
        self.display_scale = new_scale
        self.renderer.scale = new_scale
        cw = int(CANVAS_W * new_scale)
        ch = int(CANVAS_H * new_scale)
        self.canvas.configure(width=cw, height=ch)
        self._redraw_all()

    # ---------- Keys ----------

    def _bind_keys(self) -> None:
        self.bind_all("<Control-z>", lambda e: self._undo())
        self.bind_all("<Control-Z>", lambda e: self._undo())
        self.bind_all("<Control-Shift-z>", lambda e: self._redo())
        self.bind_all("<Control-Shift-Z>", lambda e: self._redo())
        self.bind_all("<Control-y>", lambda e: self._redo())
        self.bind_all("<Control-s>", lambda e: self._save_svg())
        # Secret admin panel — Ctrl+Shift+A. No visible affordance.
        self.bind_all("<Control-Shift-A>", lambda e: self._open_admin_panel())
        self.bind_all("<Control-Shift-a>", lambda e: self._open_admin_panel())
        self._admin_panel: AdminPanel | None = None
        # Spacebar disengages auto mode (no-op when auto is already off).
        self.bind_all("<space>", lambda e: self._disengage_auto())
        # Esc cancels arming or disengages an active auto session.
        self.bind_all("<Escape>", lambda e: self._on_auto_escape())
        self._auto_on: bool = False
        self._auto_state: str = "off"
        self._swallow_next_release: bool = False

    def _open_admin_panel(self) -> None:
        """Toggle the hidden admin panel. Re-renders against current doc each open."""
        if self._admin_panel is not None and self._admin_panel.winfo_exists():
            self._admin_panel.destroy()
            self._admin_panel = None
            return
        self._admin_panel = AdminPanel(self, self.doc)

    # ---------- Brush callbacks ----------

    def _active_tool(self) -> Tool:
        return self.tools[self.mode_var.get()]

    def _set_pattern(self, pattern: BrushPattern) -> None:
        self.brush.pattern = pattern

    def _set_color(self, hexc: str, label: str = "") -> None:
        self.brush.color = hexc
        if hasattr(self, "toolbar"):
            self.toolbar.show_color(hexc, label)
        if hasattr(self, "palette"):
            self.palette.set_active_color(hexc, label)
        if hasattr(self, "palette_picker"):
            self.palette_picker.set_active_color(hexc, label)
        if hasattr(self, "line_strip"):
            self.line_strip.set_active_color(hexc)
        # Grid lattice tracks the active color; repaint so the change is visible.
        if hasattr(self, "renderer"):
            self._redraw_all()

    def _on_palette_pick(self, palette: dict) -> None:
        """Called when the user clicks a palette name in PalettePicker.
        Shows the palette's colors as a strip in the top-right corner."""
        self.selected_palette.set_palette(palette)

    def _on_mode_change(self) -> None:
        self._update_cursor_ring()

    def _set_line_size(self, size: tuple[int, int], label: str = "") -> None:
        """Pick handler from LineStrip — sets the active line size and
        refreshes UI affordances that surface it."""
        self.brush.line_size = size
        if hasattr(self, "line_strip"):
            self.line_strip.set_active_size(size)
        # Free-draw cursor previews the W×H stamp — refresh in place.
        self._update_cursor_ring()

    # ---------- Event routing ----------

    def _on_press(self, ev: tk.Event) -> None:
        sx, sy = self._to_svg(ev.x, ev.y)
        # If auto mode is arming, this click is the launch-point selection,
        # not a normal paint stroke. Engage auto and consume the click.
        if getattr(self, "_auto_state", "off") == "arming":
            self._engage_auto_at(sx, sy)
            return
        self._active_tool().press(sx, sy)

    def _on_drag(self, ev: tk.Event) -> None:
        sx, sy = self._to_svg(ev.x, ev.y)
        self._last_cursor_xy = (ev.x, ev.y)
        self._update_cursor_ring(ev.x, ev.y)
        # While arming, ignore drags so the user can move to their target.
        if getattr(self, "_auto_state", "off") == "arming":
            return
        self._active_tool().drag(sx, sy)

    def _on_release(self, ev: tk.Event) -> None:
        # Swallow the release paired with the engaging click so it doesn't
        # immediately end the synthetic press we just started for auto mode.
        if getattr(self, "_swallow_next_release", False):
            self._swallow_next_release = False
            return
        sx, sy = self._to_svg(ev.x, ev.y)
        self._active_tool().release(sx, sy)

    def _on_hover(self, ev: tk.Event) -> None:
        self._last_cursor_xy = (ev.x, ev.y)
        self._update_cursor_ring(ev.x, ev.y)
        # In auto mode, hover acts like a drag — the mouse is "always clicked."
        if getattr(self, "_auto_on", False):
            sx, sy = self._to_svg(ev.x, ev.y)
            self._active_tool().drag(sx, sy)

    # ---------- Auto mode ----------
    #
    # State machine:
    #   OFF       — idle. Click Auto button to enter ARMING.
    #   ARMING    — waiting for the user to click the canvas to choose
    #               the launch point. Esc or another button click cancels.
    #   ON        — engaged. Hover events fire drag(). Spacebar or button
    #               click releases.
    #
    # The launch point matters: the active tool's press() happens at the
    # canvas click location, not wherever the cursor happened to be when
    # the user reached for the toolbar button.

    def _set_auto_state(self, state: str) -> None:
        self._auto_state = state
        if hasattr(self, "toolbar"):
            self.toolbar.set_auto_state(state)
        # Crosshair cursor while arming so the user knows a click is awaited.
        if hasattr(self, "canvas"):
            self.canvas.configure(cursor="crosshair" if state == "arming" else "none")

    def _toggle_auto(self) -> None:
        """Top-level Auto button handler — drives the state machine."""
        state = getattr(self, "_auto_state", "off")
        if state == "off":
            self._arm_auto()
        elif state == "arming":
            self._cancel_auto_arming()
        else:  # "on"
            self._disengage_auto()

    def _arm_auto(self) -> None:
        """Move from OFF → ARMING. Waits for the next canvas click."""
        self._set_auto_state("arming")

    def _cancel_auto_arming(self) -> None:
        """ARMING → OFF without firing a press."""
        self._set_auto_state("off")

    def _engage_auto_at(self, sx: float, sy: float) -> None:
        """ARMING → ON, anchored at (sx, sy) in SVG coords."""
        self._set_auto_state("on")
        self._auto_on = True
        # Mark the next button-release as "from the engaging click" so we
        # can swallow it instead of releasing the synthetic press we just made.
        self._swallow_next_release = True
        self._active_tool().press(sx, sy)

    def _disengage_auto(self) -> None:
        """ON → OFF — release the synthetic press. No-op if already off."""
        if getattr(self, "_auto_state", "off") != "on":
            return
        self._auto_on = False
        self._set_auto_state("off")
        # Release at the cursor's current canvas position.
        x = self.canvas.winfo_pointerx() - self.canvas.winfo_rootx()
        y = self.canvas.winfo_pointery() - self.canvas.winfo_rooty()
        sx, sy = self._to_svg(x, y)
        self._active_tool().release(sx, sy)

    def _on_auto_escape(self) -> None:
        """Esc handler: cancel arming, or disengage if already on."""
        state = getattr(self, "_auto_state", "off")
        if state == "arming":
            self._cancel_auto_arming()
        elif state == "on":
            self._disengage_auto()

    # ---------- Cursor ring ----------

    def _cursor_bbox(self, sx: float, sy: float) -> tuple[str, float, float, float, float]:
        """Return (shape, x0, y0, x1, y1) in screen coords for the cursor
        ring. All four tools (cell, free, sheet, erase) share the same
        snapped W×H footprint — only the outline color distinguishes the
        eraser from the paint tools (handled in _update_cursor_ring)."""
        s = self.display_scale
        col0, row0, cw, ch = snapped_footprint(sx, sy, self.brush.line_size)
        x0 = col0 * CELL_SIZE * s
        y0 = row0 * CELL_SIZE * s
        x1 = (col0 + cw) * CELL_SIZE * s
        y1 = (row0 + ch) * CELL_SIZE * s
        return ("rect", x0, y0, x1, y1)

    def _update_cursor_ring(self, cx: float | None = None,
                            cy: float | None = None) -> None:
        # When called without coords (e.g. after a brush-size change),
        # refresh in place using the last known cursor position.
        if cx is None or cy is None:
            if self._last_cursor_xy is None:
                return
            cx, cy = self._last_cursor_xy
        sx, sy = self._to_svg(cx, cy)
        shape, x0, y0, x1, y1 = self._cursor_bbox(sx, sy)

        mode = self.mode_var.get()
        # Eraser: hollow ring in red so it doesn't obscure what it's about
        # to delete. All other tools: filled in the brush color so the
        # user previews exactly what will land.
        if mode == "erase":
            fill = ""
            outline = "#C84B3F"
        else:
            fill = self.brush.color
            outline = self.brush.color

        # Rebuild the cursor item if its shape type changed (rect ↔ oval).
        current_shape = (
            self.canvas.type(self._cursor_ring)
            if self._cursor_ring is not None else None
        )
        wanted = "oval" if shape == "oval" else "rectangle"
        if current_shape != wanted:
            if self._cursor_ring is not None:
                self.canvas.delete(self._cursor_ring)
            ctor = (self.canvas.create_oval if shape == "oval"
                    else self.canvas.create_rectangle)
            self._cursor_ring = ctor(
                x0, y0, x1, y1, fill=fill, outline=outline, width=1,
            )
        else:
            self.canvas.coords(self._cursor_ring, x0, y0, x1, y1)
            self.canvas.itemconfigure(self._cursor_ring, fill=fill, outline=outline)
            self.canvas.tag_raise(self._cursor_ring)

    def _hide_cursor_ring(self) -> None:
        if self._cursor_ring is not None:
            self.canvas.delete(self._cursor_ring)
            self._cursor_ring = None

    # ---------- Actions ----------

    # name -> (paper background, grid-visible)
    SURFACES: dict[str, tuple[str, bool]] = {
        "White paper": ("#FFFFFF", False),
        "Cornsilk": ("#FFF8DC", False),
        "Light cornsilk": ("#FFFCF0", False),
        "Sandbox": ("#E8D9A8", False),
        "Grid paper": ("#FFFFFF", True),
    }

    def _surface(self) -> tuple[str, bool]:
        return self.SURFACES.get(self.surface_var.get(), self.SURFACES["White paper"])

    def _grid_color(self) -> str | None:
        """Active brush color when the surface shows a grid, else None."""
        _, grid_on = self._surface()
        return self.brush.color if grid_on else None

    def _apply_surface(self) -> None:
        """Push the current surface choice into canvas background + repaint.
        Chrome widgets are parented to the canvas, so they need their bg
        re-fetched after the canvas changes color or they'd still show the
        previous paper as a coloured rectangle behind themselves."""
        paper, _ = self._surface()
        self.canvas.configure(bg=paper)
        for chrome in (
            getattr(self, "toolbar", None),
            getattr(self, "actions_card", None),
            getattr(self, "palette", None),
            getattr(self, "palette_picker", None),
            getattr(self, "selected_palette", None),
            getattr(self, "line_strip", None),
            getattr(self, "drawer", None),
            getattr(self, "color_drawer", None),
        ):
            if chrome is not None and hasattr(chrome, "refresh_bg"):
                chrome.refresh_bg()
        self._redraw_all()

    # ---------- Sandbox template ----------

    # Wooden rim color, two-cell thickness, inset from canvas edges.
    SANDBOX_WOOD = "#8B5A2B"
    SANDBOX_MARGIN_COLS = 20   # cells of empty margin around the box
    SANDBOX_MARGIN_ROWS = 12
    SANDBOX_WALL = 2           # rim thickness in cells

    def _load_sandbox_template(self) -> None:
        """Paint a rectangular wooden rim approximating a top-down sandbox.

        The interior is left empty so the sandy paper background shows
        through as the sand bed. Cells go directly into the Document; the
        load itself is recorded as a single undoable action so the user
        can wipe the template with one Ctrl+Z."""
        from state import BatchCellAction, SetCellAction

        cols = CANVAS_W // CELL_SIZE   # 192
        rows = CANVAS_H // CELL_SIZE   # 108
        c0 = self.SANDBOX_MARGIN_COLS
        c1 = cols - self.SANDBOX_MARGIN_COLS - 1
        r0 = self.SANDBOX_MARGIN_ROWS
        r1 = rows - self.SANDBOX_MARGIN_ROWS - 1
        w = self.SANDBOX_WALL

        batch = BatchCellAction()
        for col in range(c0, c1 + 1):
            for row in range(r0, r1 + 1):
                on_top    = row < r0 + w
                on_bottom = row > r1 - w
                on_left   = col < c0 + w
                on_right  = col > c1 - w
                if not (on_top or on_bottom or on_left or on_right):
                    continue
                change = SetCellAction(col, row, self.SANDBOX_WOOD)
                batch.add(change)
                change.apply(self.doc)
        if batch.changes:
            self.undo_stack.record(batch)
        self._redraw_all()

    def _redraw_all(self) -> None:
        self.renderer.render_document(self.doc, self._grid_color())
        self._cursor_ring = None
        # Re-stamp the cursor ring at the last known position so a redraw
        # doesn't visually drop the cursor preview.
        self._update_cursor_ring()

    def _undo(self) -> None:
        if self.undo_stack.undo() is not None:
            self._redraw_all()

    def _redo(self) -> None:
        if self.undo_stack.redo() is not None:
            self._redraw_all()

    def _clear(self) -> None:
        if self.doc.is_empty():
            return
        if not messagebox.askyesno(
            "Clear", "Discard all fills, sand, and strokes?", parent=self
        ):
            return
        self.undo_stack.do(ClearAction())
        self._redraw_all()

    def _save_svg(self) -> None:
        default = Path(__file__).parent / "grid_1280_filled.svg"
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".svg",
            initialfile=default.name,
            initialdir=str(default.parent),
            filetypes=[("SVG", "*.svg")],
        )
        if not path:
            return
        svg = self.svg_renderer.render(self.doc, self._grid_color())
        Path(path).write_text(svg, encoding="utf-8")
        messagebox.showinfo("Saved", f"Wrote {path}", parent=self)


if __name__ == "__main__":
    GridFiller().mainloop()
