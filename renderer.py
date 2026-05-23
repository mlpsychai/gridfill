"""Rendering: TkRenderer paints to a live canvas; SvgRenderer emits SVG text.

Both consume the same Document and produce visually identical output.
Coordinates in the Document are SVG-space; TkRenderer scales on the way out.
"""

from __future__ import annotations

import tkinter as tk

from state import Document, Stroke


# ---------- Canvas constants ----------

CANVAS_W = 1920
CANVAS_H = 1080
CELL_SIZE = 10
MAJOR_EVERY = 10            # major lattice every 100px

MAJOR_STEP = CELL_SIZE * MAJOR_EVERY


# ---------- Tk renderer ----------

class TkRenderer:
    """Draws a Document onto a tk.Canvas at the given display scale."""

    def __init__(self, canvas: tk.Canvas, display_scale: float) -> None:
        self.canvas = canvas
        self.scale = display_scale

    # --- primitives ---

    def draw_grid(self, color: str | None) -> None:
        if color is None:
            return
        s = self.scale
        for k in range(0, CANVAS_W + 1, CELL_SIZE):
            self.canvas.create_line(k * s, 0, k * s, CANVAS_H * s,
                                    fill=color, width=1)
        for k in range(0, CANVAS_H + 1, CELL_SIZE):
            self.canvas.create_line(0, k * s, CANVAS_W * s, k * s,
                                    fill=color, width=1)

    def draw_cell(self, col: int, row: int, color: str) -> int:
        s = self.scale
        x0 = col * CELL_SIZE * s
        y0 = row * CELL_SIZE * s
        return self.canvas.create_rectangle(
            x0, y0, x0 + CELL_SIZE * s, y0 + CELL_SIZE * s,
            fill=color, outline="", width=0,
        )

    def draw_stamp(self, sx: float, sy: float, color: str,
                   size: tuple[int, int]) -> int:
        """Stamp one W×H rectangle centered at (sx, sy) in SVG coords.

        Legacy: kept for replay of pre-existing Stroke objects; no live
        tool produces Strokes anymore — free-draw now paints into
        doc.cell_fills via tools.paint_footprint."""
        w, h = size
        s = self.scale
        x0 = (sx - w / 2) * s
        y0 = (sy - h / 2) * s
        x1 = (sx + w / 2) * s
        y1 = (sy + h / 2) * s
        return self.canvas.create_rectangle(
            x0, y0, x1, y1, fill=color, outline="", width=0,
        )

    def draw_stroke(self, stroke: Stroke) -> None:
        """Replay a saved stroke by stamping along its path. Honors the
        stroke's dash pattern via a fresh DashPhase walk.

        Legacy: no live tool produces Strokes anymore. Retained so any
        Strokes still in a Document (or future imports) render correctly."""
        # Local import to avoid a circular at module load.
        from tools import DashPhase
        dash = DashPhase()
        first = True
        for sx, sy in stroke.points:
            if first:
                # Always stamp the very first point.
                dash.advance(sx, sy, stroke.pattern)
                self.draw_stamp(sx, sy, stroke.color, stroke.size)
                first = False
                continue
            if dash.advance(sx, sy, stroke.pattern):
                self.draw_stamp(sx, sy, stroke.color, stroke.size)

    # --- full repaint ---

    def render_document(self, doc: Document, grid_color: str | None) -> None:
        self.canvas.delete("all")
        for (col, row), color in doc.cell_fills.items():
            self.draw_cell(col, row, color)
        self.draw_grid(grid_color)
        for st in doc.strokes:
            self.draw_stroke(st)


# ---------- SVG renderer ----------

class SvgRenderer:
    """Emits an SVG document from a Document. Coordinates are already SVG-space."""

    def render(self, doc: Document, grid_color: str | None) -> str:
        parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {CANVAS_W} {CANVAS_H}" '
            f'width="{CANVAS_W}" height="{CANVAS_H}">',
            '  <g shape-rendering="crispEdges">',
        ]

        parts.append('    <g id="cells">')
        for (col, row), color in doc.cell_fills.items():
            x = col * CELL_SIZE
            y = row * CELL_SIZE
            parts.append(
                f'      <rect x="{x}" y="{y}" '
                f'width="{CELL_SIZE}" height="{CELL_SIZE}" fill="{color}"/>'
            )
        parts.append('    </g>')

        if grid_color is not None:
            parts.append(
                f'    <g id="grid" stroke="{grid_color}" '
                'stroke-width="1" fill="none">'
            )
            for k in range(0, CANVAS_W + 1, CELL_SIZE):
                parts.append(f'      <line x1="{k}" y1="0" x2="{k}" y2="{CANVAS_H}"/>')
            for k in range(0, CANVAS_H + 1, CELL_SIZE):
                parts.append(f'      <line x1="0" y1="{k}" x2="{CANVAS_W}" y2="{k}"/>')
            parts.append('    </g>')
        parts.append('  </g>')

        parts.append('  <g id="strokes">')
        # Stamp-based strokes: emit each W×H stamp as its own rect, walking
        # the dash pattern the same way TkRenderer does.
        from tools import DashPhase
        for st in doc.strokes:
            w, h = st.size
            dash = DashPhase()
            stamp_pts: list[tuple[float, float]] = []
            for i, (sx, sy) in enumerate(st.points):
                if i == 0:
                    dash.advance(sx, sy, st.pattern)
                    stamp_pts.append((sx, sy))
                elif dash.advance(sx, sy, st.pattern):
                    stamp_pts.append((sx, sy))
            if not stamp_pts:
                continue
            parts.append(f'    <g fill="{st.color}">')
            for sx, sy in stamp_pts:
                x = sx - w / 2
                y = sy - h / 2
                parts.append(
                    f'      <rect x="{x:.1f}" y="{y:.1f}" '
                    f'width="{w}" height="{h}"/>'
                )
            parts.append('    </g>')
        parts.append('  </g>')

        parts.append('</svg>\n')
        return "\n".join(parts)
