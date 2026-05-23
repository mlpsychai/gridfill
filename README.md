# gridfill

A Tkinter pixel-drawing tool. 192 × 108 cell lattice, charcoal grid, curated palette system, undo stack, SVG export.

Built for the desktop. Containerized for the browser via noVNC.

---

## Run locally

```bash
python3 grid_fill.py
```

Requires Python 3.10+ and the `tk` system package (`apt install python3-tk` on Debian/Ubuntu). No pip dependencies.

## Run in a browser

```bash
docker build -t gridfill .
docker run --rm -p 6080:6080 gridfill
```

Then open **http://localhost:6080/vnc.html?autoconnect=true&resize=scale**.

The container runs the Tk app against Xvfb, exposes the display over VNC, and serves a noVNC client on port 6080.

---

## What it does

| Area | Highlights |
|---|---|
| **Tools** | Cell, Free-draw, Sheet-press, Eraser. Cursor ring previews the snapped W×H footprint in the active color. |
| **Palettes** | 35 curated palettes across 5 categories; full 350-color Copic Sketch picker; live palette strip in the top-right. |
| **Surfaces** | White, cornsilk, light cornsilk, sandbox (with wooden-rim template), grid paper. |
| **Auto mode** | Arming → ON state machine; click sets a launch point, hover acts as drag, Space/Esc releases. |
| **Export** | SVG via `Ctrl+S`. Same renderer pipeline as the canvas — strokes, cells, grid, layering preserved. |
| **Undo** | `Ctrl+Z` / `Ctrl+Shift+Z` / `Ctrl+Y` against a single `UndoStack`. |
| **Hidden admin** | `Ctrl+Shift+A` toggles an in-app color-prevalence panel for the current document. |

Full feature inventory with file references: [`docs/feature_inventory.md`](docs/feature_inventory.md).

## Architecture

| Module | Role |
|---|---|
| `grid_fill.py` | Entry point — launches `app.GridFiller().mainloop()`. |
| `app.py` | Application shell. Wires Document, Tools, Renderer, and UI; owns the Tk root. |
| `ui.py` | Widget library — Toolbar, PaletteStrip, PalettePicker, Drawer, AdminPanel, pixel-font labels. |
| `tools.py` | One Tool strategy per mode (Cell, FreeDraw, SheetPress, Eraser). |
| `renderer.py` | `TkRenderer` (canvas) and `SvgRenderer` (export) — shared geometry. |
| `state.py` | `Document`, primitives, Actions, `UndoStack`. |
| `telemetry.py` | JSONL event spool to `~/.gridfill/events/<session_id>.jsonl`. |
| `copic_palette.py`, `curated_palettes.py` | Static palette data. |

## Telemetry

The app writes stroke and session events to `~/.gridfill/events/<session_id>.jsonl`. This is local-only; nothing leaves the host unless you wire up the admin pipeline (not shipped). Inside the container the spool lives at `/root/.gridfill/events/`; mount a volume there to persist it across runs.

## License

MIT — see [`LICENSE`](LICENSE).
