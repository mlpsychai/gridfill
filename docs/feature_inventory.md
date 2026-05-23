# Grid Filler — Feature Inventory

> Generated 2026-05-18 from `grid_fill.py` (entry) and the modules it launches: `app.py`, `ui.py`, `tools.py`, `state.py`, `renderer.py`. Standing directive (`claude.md`): bring usability, accessibility, and aesthetic up to high visual and other standards. This table is the audit surface — every row is a concrete target.

Canvas spec: 1920 × 1080, 192 × 108 cells × 10 px. Display scale is window-fit (`app.py:67`, `app.py:207`). Charcoal lattice; surface presets in `app.py:457`.

## Usability

| # | Feature | Subcomponents | Location | Status |
|---|---|---|---|---|
| U1 | **Tool modes** | Cell, Free-draw, Sheet-press, Eraser; one Tool strategy per mode; mode `StringVar` drives event routing | `tools.py:143–322`, `app.py:96–106`, `app.py:251` | Working |
| U2 | **Brush context** | Color, W×H line size, erase radius, dash pattern, sheet-press density | `tools.py:27–40`, `app.py:73–78` | Working |
| U3 | **Cursor preview** | Snapped W×H footprint preview; eraser shows hollow red outline, paint tools fill in active color; `cursor="none"` so only the preview shows | `app.py:395–451` | Working; hover-only — no touch / keyboard preview |
| U4 | **Undo / Redo** | `UndoStack` against `Document`; `Ctrl+Z`, `Ctrl+Shift+Z`, `Ctrl+Y`; `ActionsCard` UI | `state.py`, `app.py:222–227`, `app.py:542–548`, `ui.py:672` | Working |
| U5 | **Clear** | Guarded by `messagebox.askyesno` confirmation; one undoable `ClearAction` | `app.py:550–558` | Working |
| U6 | **Save SVG** | `Ctrl+S` or button → `filedialog.asksaveasfilename`; `SvgRenderer.render(doc, grid_color)` | `app.py:228`, `app.py:560–573`, `renderer.py` | Working; no autosave, no project file |
| U7 | **Auto mode** | OFF → ARMING → ON state machine; click sets launch point, hover acts as drag, Space / Esc / button releases; crosshair cursor while arming | `app.py:325–391` | Working; advanced — no onboarding affordance |
| U8 | **Fit-to-window** | `<Configure>` listener recomputes `display_scale`, clamped [0.1, 1.0]; canvas resizes live | `app.py:195–218` | Working |
| U9 | **Surface presets** | White paper, Cornsilk, Light cornsilk, Sandbox (with pre-painted wooden rim template), Grid paper | `app.py:457–492`, `app.py:502–533` | Working; preset list is hard-coded |
| U10 | **Master drawer** | Single arrow expands/collapses all chrome toggles; nested COLOR sub-drawer for SWATCH + PALETTE | `app.py:138–166`, `ui.py:183` | Working; discoverability untested |
| U11 | **Palette browser** | 35 curated palettes in 5 categories (`curated_palettes.py`); 350-color, 18-group Copic Sketch picker (modal); strip widget for the active palette in top-right | `ui.py:910` (PalettePicker), `ui.py:1306` (CopicPicker), `ui.py:1174` (SelectedPaletteStrip), `copic_palette.py`, `curated_palettes.py` | Working |
| U12 | **Line / brush size strip** | Discrete W×H presets; active size mirrored back into cursor preview | `ui.py:1610`, `app.py:279–286` | Working |
| U13 | **Hidden admin panel** | `Ctrl+Shift+A` toggles in-app `AdminPanel` (color prevalence for *current* document only) | `app.py:229–247`, `ui.py:1466` | Working; superseded by web dashboard in `admin/` |
| U14 | **Persistence — telemetry** | JSONL spool to `~/.gridfill/events/<session_id>.jsonl`; throttled stroke-points; atexit layer snapshot + session_end | `telemetry.py`, `docs/admin_panel_plan.md` | Working — 6 sessions on disk |

**Usability gaps to name:**
- No keyboard binding to switch tool modes (cell/free/sheet/erase).
- No keyboard binding to cycle palette colors or change brush size.
- No status bar; current tool, color, and size are scattered across chrome widgets, not unified.
- No project save/load — only SVG export.
- Auto-mode state changes are not announced anywhere except the Auto button's appearance.

## Accessibility

| # | Feature | Subcomponents | Location | Status |
|---|---|---|---|---|
| A1 | **Keyboard equivalents** | `Ctrl+Z`, `Ctrl+Shift+Z`, `Ctrl+Y`, `Ctrl+S`, `Ctrl+Shift+A`, `Space`, `Esc` | `app.py:222–238` | Partial — covers global actions; tool / color / size selection are **mouse-only** |
| A2 | **Focus management** | None explicit; Tk default focus traversal | — | **Missing** — no `Tab` order, no visible focus rings on chrome widgets |
| A3 | **Cursor visibility** | Custom W×H ring; canvas cursor set to `none` so system cursor doesn't double up | `app.py:182`, `app.py:344` | Working for sighted mouse users; **no high-contrast mode** |
| A4 | **Color labels** | Active color label (e.g. `B14 · Light Blue`) surfaced via `_set_color` into toolbar, palette, picker, line strip | `app.py:257–269` | Working — color isn't conveyed by hue alone |
| A5 | **Color-blind safety** | Eraser uses a fixed red (`#C84B3F`) outline that may collide with reds in the brush palette | `app.py:425` | **Gap** — needs a non-hue cue (shape, dash) |
| A6 | **Confirmation dialogs** | Clear action guarded; save success acknowledged via `messagebox.showinfo` | `app.py:553`, `app.py:573` | Working |
| A7 | **Text rendering** | Custom `PixelLabel` glyphs (`ui.py:75–179`) — bitmap font; not OS-scaled | `ui.py:75–179` | **Gap** — does not respect OS font-size or DPI preference |
| A8 | **Screen reader support** | None | — | **Missing** — Tk Canvas elements are not exposed to AT |
| A9 | **Motion / animation** | Cursor ring repositions on every motion event; no animation easing | `app.py:408` | OK for vestibular safety |
| A10 | **Touch / pen input** | Mouse-only event bindings (`<Button-1>`, `<B1-Motion>`) | `app.py:184–188` | **Gap** — no `<Touch>` or stylus pressure routing |

**Accessibility gaps to name:**
- Mode switching, palette picking, and brush sizing require pointing.
- No high-contrast theme; toolbar icons rely on pixel-font glyphs at fixed sizes.
- Eraser's red outline is the only non-color cue across the toolset.
- No assistive-tech surface — the Tk Canvas is opaque to readers.

## Aesthetic

| # | Feature | Subcomponents | Location | Status |
|---|---|---|---|---|
| AE1 | **Charcoal lattice rendering** | Two-tier grid (faint minor + major every 100 px); active brush color drives lattice when Grid-paper surface is on | `renderer.py`, `app.py:468–471` | Working |
| AE2 | **Surface paper tones** | White / Cornsilk / Light cornsilk / Sandbox (sandy `#E8D9A8`) / Grid paper; chrome widgets reparented to canvas to inherit paper color (transparent illusion) | `app.py:457–492` | Working; preset list is short |
| AE3 | **Sandbox template** | Wooden rim (`#8B5A2B`), two-cell thick, inset 20 cols × 12 rows; one undoable batch | `app.py:494–533` | Working; only Sandbox has a starter template |
| AE4 | **Cursor ring aesthetic** | Filled rect in brush color (paint) or hollow red rect (erase); 1px outline; matches snapped footprint geometry | `app.py:419–447` | Working |
| AE5 | **Drawer animation** | Single arrow icon, expand/collapse via `Drawer` class; nested COLOR drawer for SWATCH + PALETTE | `ui.py:183–310` | Working; no easing |
| AE6 | **Toolbar typography** | Pixel-font glyphs rendered into `PixelLabel` canvases; consistent bitmap aesthetic | `ui.py:75–179`, `ui.py:104` | Working — coherent retro look; trades crispness at large window sizes |
| AE7 | **Palette swatch design** | `PaletteStrip` for active palette; `SelectedPaletteStrip` top-right; `PalettePicker` modal with grouped browser, filter, hover tooltip, swatch-click selection | `ui.py:749`, `ui.py:1174`, `ui.py:910` | Working |
| AE8 | **Copic picker layout** | 1480×820 modal; flowable swatch grid that re-flows on `<Configure>`; mousewheel scoped to picker canvas | `ui.py:1306` | Working — bug-fixed pre-existing wheel leak |
| AE9 | **Stroke / sand layering** | Cells → strokes → sand circles → cursor ring; consistent z-order on canvas and SVG export | `tools.py`, `renderer.py` | Working |
| AE10 | **SVG export fidelity** | Same renderer pipeline as canvas; cells + grid + strokes layered identically | `renderer.py` (`SvgRenderer`) | Working — saved SVGs roundtrip into `test_suite.py` |
| AE11 | **Chrome backdrop transparency** | Chrome parented to canvas, `refresh_bg()` re-fetches paper color on surface change to avoid dark-rectangle artefact | `app.py:480–491` | Working |
| AE12 | **Dash patterns** | `BrushPattern` (Solid + dash/gap); `DashPhase` accumulator drives on/off decisions along a drag | `tools.py:43–66`, `state.py` (`BrushPattern`) | Working |

**Aesthetic gaps to name:**
- Pixel-font glyphs do not scale with display size — readable at 1.0, soft at lower scales.
- No dark / high-contrast / sepia theme separate from "surface preset."
- Surface preset list is hard-coded; adding a paper tone requires code edit.
- No visual indication of the active tool inside the canvas itself — must look up to the toolbar.

---

## Module / LOC summary

| Module | LOC | Role |
|---|---|---|
| `grid_fill.py` | 26 | Entry point |
| `app.py` | 577 | Application shell — wires Document, Tools, Renderer, UI |
| `ui.py` | 1,815 | Toolbar, drawers, palettes, pickers, admin panel — **largest single file** |
| `state.py` | 207 | `Document`, primitives, Actions, `UndoStack` |
| `renderer.py` | 166 | `TkRenderer` (canvas), `SvgRenderer` (export) |
| `tools.py` | 322 | `CellTool`, `FreeDrawTool`, `SheetPressTool`, `EraserTool`, `BrushContext`, `DashPhase` |
| `telemetry.py` | 195 | JSONL spool |
| `copic_palette.py` | 447 | 350-color Copic Sketch reference |
| `curated_palettes.py` | 323 | 35 hand-curated palettes |

## Recommended audit order

1. **Accessibility A1, A2, A10** — keyboard parity for mode / color / size, focus rings, and touch/pen routing. The most exclusionary gaps; cheapest to close because event routing is already centralized in `app.py:290–323`.
2. **Usability U7, U10** — Auto mode and the master drawer are powerful but undiscoverable. A first-launch onboarding overlay or status-bar prompt would surface them without touching the chrome layout.
3. **Aesthetic AE6** — pixel-font scaling. Either render at higher resolution and downsample, or expose a glyph-size token so chrome respects display scale.
4. **`ui.py` decomposition** — at 1,815 lines, it owns toolbar, drawers, palettes, picker, and admin in one file. Split lines should be drawn *after* the audits above, so the seams reflect actual UX boundaries rather than convenient cuts.
