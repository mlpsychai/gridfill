#!/usr/bin/env python3
"""Entry point for the Grid Filler app.

Interactive fill tool for a 1920×1080 charcoal lattice. State, rendering,
tools, and UI live in separate modules; this file just launches the shell.

Modules:
  state.py      — Document, primitives, Actions, UndoStack
  renderer.py   — TkRenderer (canvas) and SvgRenderer (export)
  tools.py      — CellTool, FreeDrawTool, SandTool, EraserTool
  ui.py         — Toolbar, PaletteStrip, CopicPicker
  app.py        — GridFiller application shell
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app import GridFiller


if __name__ == "__main__":
    GridFiller().mainloop()
