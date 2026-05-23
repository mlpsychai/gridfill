"""Input-handling strategies. One tool per drawing mode.

Tools receive press/drag/release events in SVG coordinates and mutate the
Document through Actions. They own no UI; the app routes events to them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from renderer import CANVAS_H, CANVAS_W, CELL_SIZE, MAJOR_EVERY, TkRenderer
from state import (
    BatchCellAction,
    BrushPattern,
    Document,
    EraseAction,
    SetCellAction,
    Stroke,
    UndoStack,
)


# ---------- Brush context ----------

@dataclass
class BrushContext:
    """Live brush settings, read by tools at each event."""

    color: str = "#E07A5F"
    # W×H stamp size for all three drawing tools (cell fill, free draw,
    # sheet press). Top-left of the footprint snaps to the 10px lattice;
    # width/height are ceiled to whole cells.
    line_size: tuple[int, int] = (8, 8)
    erase_radius: int = 20
    pattern: BrushPattern = BrushPattern("Solid", 0, 0)
    # Sheet-press density: probability that a given minor cell inside the
    # footprint gets painted. 1.0 = total fill.
    sheet_density: float = 1.0


# ---------- Dash phase accumulator ----------

class DashPhase:
    """Tracks travel distance for dash on/off decisions along a drag."""

    def __init__(self) -> None:
        self._phase = 0.0
        self._last: tuple[float, float] | None = None

    def reset(self) -> None:
        self._phase = 0.0
        self._last = None

    def advance(self, sx: float, sy: float, pattern: BrushPattern) -> bool:
        if pattern.is_solid:
            return True
        if self._last is None:
            self._last = (sx, sy)
            return True
        lx, ly = self._last
        dist = ((sx - lx) ** 2 + (sy - ly) ** 2) ** 0.5
        self._last = (sx, sy)
        self._phase = (self._phase + dist) % (pattern.dash + pattern.gap)
        return self._phase < pattern.dash


# ---------- Helpers ----------

def cell_at(sx: float, sy: float) -> tuple[int, int]:
    col = int(sx // CELL_SIZE)
    row = int(sy // CELL_SIZE)
    col = max(0, min(col, CANVAS_W // CELL_SIZE - 1))
    row = max(0, min(row, CANVAS_H // CELL_SIZE - 1))
    return col, row


def snapped_footprint(sx: float, sy: float,
                      size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Snap a W×H stamp to the grid. Top-left corner snaps to the nearest
    10px minor cell. Returns (col0, row0, cells_w, cells_h) where
    cells_w = ceil(W/CELL_SIZE) and the origin is clamped so the footprint
    stays inside the canvas."""
    col0, row0 = cell_at(sx, sy)
    w, h = size
    cells_w = max(1, -(-w // CELL_SIZE))    # ceil-div
    cells_h = max(1, -(-h // CELL_SIZE))
    max_col = CANVAS_W // CELL_SIZE - cells_w
    max_row = CANVAS_H // CELL_SIZE - cells_h
    col0 = max(0, min(col0, max_col))
    row0 = max(0, min(row0, max_row))
    return col0, row0, cells_w, cells_h


def paint_footprint(doc: Document, renderer: TkRenderer,
                    batch: BatchCellAction, color: str,
                    col0: int, row0: int, cells_w: int, cells_h: int,
                    density: float = 1.0) -> None:
    """Paint a W×H block of cells starting at (col0, row0), each cell
    painted with probability `density`. Mutates doc.cell_fills in place,
    appends SetCellActions to `batch`, and draws each cell via
    renderer.draw_cell()."""
    for dr in range(cells_h):
        for dc in range(cells_w):
            if density < 1.0 and random.random() >= density:
                continue
            col = col0 + dc
            row = row0 + dr
            prev = doc.cell_fills.get((col, row))
            if prev == color:
                continue
            change = SetCellAction(col, row, color, prev_color=prev)
            doc.cell_fills[(col, row)] = color
            batch.add(change)
            renderer.draw_cell(col, row, color)


# ---------- Tool base ----------

class Tool:
    """Strategy base. Subclasses override the three event hooks."""

    name = "tool"

    def __init__(self, doc: Document, undo: UndoStack,
                 brush: BrushContext, renderer: TkRenderer,
                 dash: DashPhase, on_change: Callable[[], None]) -> None:
        self.doc = doc
        self.undo = undo
        self.brush = brush
        self.renderer = renderer
        self.dash = dash
        self.on_change = on_change  # called when a redraw is needed

    def press(self, sx: float, sy: float) -> None: ...
    def drag(self, sx: float, sy: float) -> None: ...
    def release(self, sx: float, sy: float) -> None: ...


# ---------- Cell fill ----------

class CellTool(Tool):
    name = "cell"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._batch: BatchCellAction | None = None

    def _paint(self, sx: float, sy: float) -> None:
        if not self.dash.advance(sx, sy, self.brush.pattern):
            return
        col0, row0, cw, ch = snapped_footprint(sx, sy, self.brush.line_size)
        if self._batch is None:
            self._batch = BatchCellAction()
        paint_footprint(self.doc, self.renderer, self._batch,
                        self.brush.color, col0, row0, cw, ch)

    def press(self, sx: float, sy: float) -> None:
        self.dash.reset()
        self._batch = None
        self._paint(sx, sy)

    def drag(self, sx: float, sy: float) -> None:
        self._paint(sx, sy)

    def release(self, sx: float, sy: float) -> None:
        if self._batch is not None and self._batch.changes:
            self.undo.record(self._batch)
        self._batch = None


# ---------- Free draw ----------

class FreeDrawTool(Tool):
    """Drag-stamp tool. Stamps a W×H footprint of cells at every
    dash-permitted point along the cursor's path. Output lives in
    doc.cell_fills alongside cell-fill output, sharing the same undo
    semantics and SVG export."""

    name = "free"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._batch: BatchCellAction | None = None

    def press(self, sx: float, sy: float) -> None:
        self.dash.reset()
        self.dash.advance(sx, sy, self.brush.pattern)
        self._batch = BatchCellAction()
        col0, row0, cw, ch = snapped_footprint(sx, sy, self.brush.line_size)
        paint_footprint(self.doc, self.renderer, self._batch,
                        self.brush.color, col0, row0, cw, ch)

    def drag(self, sx: float, sy: float) -> None:
        if not self.dash.advance(sx, sy, self.brush.pattern):
            return
        if self._batch is None:
            self._batch = BatchCellAction()
        col0, row0, cw, ch = snapped_footprint(sx, sy, self.brush.line_size)
        paint_footprint(self.doc, self.renderer, self._batch,
                        self.brush.color, col0, row0, cw, ch)

    def release(self, sx: float, sy: float) -> None:
        if self._batch is not None and self._batch.changes:
            self.undo.record(self._batch)
        self._batch = None


# ---------- Eraser ----------

class EraserTool(Tool):
    """Erases cells inside a snapped W×H footprint taken from
    brush.line_size — the same footprint the paint tools use. Erasing and
    painting now share one geometry primitive."""

    name = "erase"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._action: EraseAction | None = None

    def _erase_at(self, sx: float, sy: float) -> bool:
        col0, row0, cells_w, cells_h = snapped_footprint(
            sx, sy, self.brush.line_size,
        )
        changed = False
        if self._action is None:
            self._action = EraseAction()

        for dr in range(cells_h):
            for dc in range(cells_w):
                key = (col0 + dc, row0 + dr)
                if key in self.doc.cell_fills:
                    self._action.removed_cells[key] = self.doc.cell_fills.pop(key)
                    changed = True

        # Stroke removal: hit-test each stroke point against the footprint
        # AABB in SVG coords. Strokes are legacy but still respected for
        # any docs that contain them.
        x0 = col0 * CELL_SIZE
        y0 = row0 * CELL_SIZE
        x1 = x0 + cells_w * CELL_SIZE
        y1 = y0 + cells_h * CELL_SIZE
        kept_strokes: list[Stroke] = []
        for st in self.doc.strokes:
            hit = any(x0 <= px <= x1 and y0 <= py <= y1 for px, py in st.points)
            if hit:
                self._action.removed_strokes.append(st)
                changed = True
            else:
                kept_strokes.append(st)
        self.doc.strokes = kept_strokes

        return changed

    def press(self, sx: float, sy: float) -> None:
        self._action = None
        if self._erase_at(sx, sy):
            self.on_change()

    def drag(self, sx: float, sy: float) -> None:
        if self._erase_at(sx, sy):
            self.on_change()

    def release(self, sx: float, sy: float) -> None:
        if self._action is not None and not self._action.is_empty():
            self.undo.record(self._action)
        self._action = None


# ---------- Sheet press ----------

def major_cell_at(sx: float, sy: float) -> tuple[int, int]:
    """Return the (col, row) of the top-left minor cell of the major-cell patch
    under (sx, sy). A major cell is MAJOR_EVERY × MAJOR_EVERY minor cells."""
    col = int(sx // CELL_SIZE) // MAJOR_EVERY * MAJOR_EVERY
    row = int(sy // CELL_SIZE) // MAJOR_EVERY * MAJOR_EVERY
    max_col = CANVAS_W // CELL_SIZE - MAJOR_EVERY
    max_row = CANVAS_H // CELL_SIZE - MAJOR_EVERY
    col = max(0, min(col, max_col))
    row = max(0, min(row, max_row))
    return col, row


class SheetPressTool(Tool):
    """Stamp a W×H footprint (from brush.line_size) of cells. Each cell
    inside the footprint is painted with probability brush.sheet_density.
    A drag does not re-stamp the same footprint origin so density stays
    honest."""

    name = "sheet"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._batch: BatchCellAction | None = None
        self._stamped_origins: set[tuple[int, int]] = set()

    def _stamp(self, sx: float, sy: float) -> None:
        col0, row0, cw, ch = snapped_footprint(sx, sy, self.brush.line_size)
        if (col0, row0) in self._stamped_origins:
            return
        self._stamped_origins.add((col0, row0))
        if self._batch is None:
            self._batch = BatchCellAction()
        paint_footprint(self.doc, self.renderer, self._batch,
                        self.brush.color, col0, row0, cw, ch,
                        density=self.brush.sheet_density)

    def press(self, sx: float, sy: float) -> None:
        self._batch = None
        self._stamped_origins = set()
        self._stamp(sx, sy)

    def drag(self, sx: float, sy: float) -> None:
        self._stamp(sx, sy)

    def release(self, sx: float, sy: float) -> None:
        if self._batch is not None and self._batch.changes:
            self.undo.record(self._batch)
        self._batch = None
        self._stamped_origins = set()
