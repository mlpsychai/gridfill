# Grid Filler Admin Panel — Telemetry Dashboard

## Context

`grid_fill.py` (toolbox/gridfill/) is a single-user Tk app for painting a 1920×1080 charcoal lattice (192 × 108 cells, 10px each). It currently ships a hidden in-app `AdminPanel` (`ui.py:862`) bound to `Ctrl+Shift+A` that shows color prevalence for the *current* document only — no persistence, no temporal record, no spatial heatmap, no session comparison.

The user wants a **standalone local web panel** that visualizes their interaction with the app over time:

1. **Spatial heatmap** of where they paint on the 192×108 lattice (across all sessions, filterable).
2. **Session vector space** — each session reduced to a feature vector (tool-usage distribution, color-usage distribution, stroke stats, cells-per-minute), embedded into Neon/pgvector, projected to 2D for cluster inspection.
3. **Session browser** — list, replay summary stats, drill into a single session.
4. **Per-color layer archive + composite snapshot** — at session end, every distinct color used becomes its own stored layer (showing only that color's cells), plus one composite "total image" layer (the final `doc.cell_fills` in full). All persisted in Neon in three forms: JSON cell-list, SVG, PNG.
5. **Cell-level vector space (Interpretation A: behavioral embedding)** — each of the 20,736 cells in the 192×108 lattice becomes a token with a learned 64-dim embedding, trained skip-gram-style from the user's painting history. Cells that get painted together (across sessions, strokes, and time windows) cluster in vector space. A drawing's *engram* is then the pooled embedding of its painted cells — position-aware but behaviorally weighted.
6. **Cell-level exact-match space (Interpretation B: occupancy vector)** — each drawing is also stored as a 20,736-dim sparse occupancy vector for pixel-exact retrieval. No invariance; literal "find drawings that paint the same cells the same colors." Complements A.

The Tk app has **no event bus, no logging, no observer pattern** today (Explore confirmed). All state lives in `Document.cell_fills` and is mutated directly by tool methods. We need to add a thin instrumentation layer at the natural choke points, persist to Neon, and serve a FastAPI dashboard styled with `configapa_mono.css`.

Single user, single machine — no auth, no deploy story.

## Architecture

```
Tk app (grid_fill.py)
  └── telemetry.py (new) ──┐
                           │  JSONL spool on disk
                           ▼
                   ~/.gridfill/events/YYYYMMDD-HHMMSS.jsonl
                           │
                           │  sync_to_neon.py (manual or on-app-quit)
                           ▼
                  Neon schema: gridfill
                    ├── sessions          (one row per app launch)
                    ├── events            (raw event log, append-only)
                    ├── cell_strikes      (denormalized for heatmap)
                    ├── session_vectors   (pgvector(32), behavioral session signature)
                    ├── layers            (per-color + composite, JSON+SVG+PNG)
                    ├── cell_embeddings   (pgvector(64), one row per cell, learned)
                    ├── drawing_engrams   (pgvector(64), pooled cell-embedding signature)
                    └── drawing_occupancy (pgvector(1024) PCA-reduced from 20,736-dim, exact-match)
                           │
                           │  FastAPI reads
                           ▼
                  localhost:8765 dashboard
                    ├── /              heatmap + filters
                    ├── /sessions      list + drill-down
                    └── /vectors       2D projection (UMAP, computed server-side)
```

**Why JSONL spool + batch sync rather than direct DB writes:** Tk runs in its own thread; we don't want a network hiccup to stall paint events. Spool is append-only, sync runs on app quit or via cron. JSONL is also human-greppable when something looks wrong.

## Phase 1 — Instrumentation (Tk side)

**New file:** `toolbox/gridfill/telemetry.py`

- `Telemetry` class — opens a JSONL writer at `~/.gridfill/events/<session_id>.jsonl` on `__init__`, exposes `log(event_type, payload)`, and flushes on every write (small files, single user, durability > throughput).
- `session_id` = ISO timestamp + 4-char random suffix.
- Writes a `session_start` row with: app version, screen size, palette hash, OS user.
- Atexit hook writes `session_end` with duration + final cell count.

**Hook points in `app.py`** (minimal-invasive, all in `GridFiller.__init__` and the existing handlers):

| Event              | Hook location                  | Payload                                          |
| ------------------ | ------------------------------ | ------------------------------------------------ |
| `session_start`    | `__init__` end                 | session_id, palette_hash, screen dims            |
| `tool_switch`      | `_on_mode_change` (app.py:211) | from_tool, to_tool                               |
| `color_pick`       | `_set_color` (app.py:199)      | hex, label                                       |
| `brush_change`     | `_on_brush_change` (app.py:227)| size or radius or density                        |
| `stroke_start`     | `_on_press` (app.py:247)       | tool, color, sx, sy, cell                        |
| `stroke_point`     | `_on_drag` (app.py:256)        | sx, sy, cell — **throttled to ~20Hz**            |
| `stroke_end`       | `_on_release` (app.py:265)     | tool, color, n_cells_painted, duration_ms        |
| `undo` / `redo`    | `_undo` (app.py:497)           | action_type, n_cells_reverted                    |
| `clear`            | `on_clear` callback            | n_cells_cleared                                  |
| `save_svg`         | `_save_svg` (app.py:515)       | path, n_cells, n_colors                          |
| `session_end`      | atexit                         | duration_s, total_strokes, total_cells           |
| `layer_snapshot`   | atexit, **before** session_end | one row per distinct color + one `composite` row; payload = `{color, cells: [[col,row],...]}` for color rows and `{cells: {"#hex": [[col,row],...], ...}}` for the composite |

The hook points are all single-line additions (`self.telemetry.log("stroke_end", {...})`). Total Tk-side surface ≤ 30 added lines.

**Throttling:** `stroke_point` fires on every mouse-motion event during a drag — at full speed that's hundreds per second. Throttle in `Telemetry.log()` by event_type: keep at most one `stroke_point` per 50ms per active stroke.

**Layer snapshot at close:** The atexit hook receives a handle to `doc.cell_fills`. Group the dict by color → one event per color with `cells: [[col,row],...]`; then emit a final `composite` event with the full mapping. This runs *before* `session_end` so a crashed render still leaves the rest of the spool intact. Snapshot is data-only — SVG/PNG generation happens during sync (Phase 3), keeping the Tk-side surface minimal and avoiding any close-time latency.

## Phase 2 — Neon schema

**Schema name:** `gridfill` (new). Created via `mcp__ragarmy-neon__query` with explicit DDL.

```sql
CREATE SCHEMA gridfill;

CREATE TABLE gridfill.sessions (
  session_id    text PRIMARY KEY,
  started_at    timestamptz NOT NULL,
  ended_at      timestamptz,
  duration_s    integer,
  palette_hash  text,
  total_strokes integer DEFAULT 0,
  total_cells   integer DEFAULT 0,
  n_colors      integer DEFAULT 0,
  app_version   text
);

CREATE TABLE gridfill.events (
  id          bigserial PRIMARY KEY,
  session_id  text NOT NULL REFERENCES gridfill.sessions(session_id),
  ts          timestamptz NOT NULL,
  event_type  text NOT NULL,
  payload     jsonb NOT NULL
);
CREATE INDEX ON gridfill.events (session_id, ts);
CREATE INDEX ON gridfill.events (event_type);

CREATE TABLE gridfill.cell_strikes (
  session_id  text NOT NULL REFERENCES gridfill.sessions(session_id),
  col         smallint NOT NULL,    -- 0..191
  row         smallint NOT NULL,    -- 0..107
  color       text NOT NULL,
  tool        text NOT NULL,
  ts          timestamptz NOT NULL
);
CREATE INDEX ON gridfill.cell_strikes (session_id);
CREATE INDEX ON gridfill.cell_strikes (col, row);

-- Vector dim TBD by feature builder; start at 32 (small, deterministic features, no embedding model needed).
CREATE TABLE gridfill.session_vectors (
  session_id  text PRIMARY KEY REFERENCES gridfill.sessions(session_id),
  features    jsonb NOT NULL,        -- human-readable feature breakdown
  embedding   vector(32) NOT NULL
);
CREATE INDEX ON gridfill.session_vectors USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

CREATE TABLE gridfill.layers (
  id          bigserial PRIMARY KEY,
  session_id  text NOT NULL REFERENCES gridfill.sessions(session_id),
  kind        text NOT NULL,            -- 'color' | 'composite'
  color       text,                     -- hex for kind='color', NULL for composite
  n_cells     integer NOT NULL,
  cells_json  jsonb NOT NULL,           -- source of truth: [[col,row],...] for color; {"#hex":[[col,row],...]} for composite
  svg         text NOT NULL,            -- rendered SVG (text)
  png         bytea NOT NULL,           -- rendered PNG (1920x1080)
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, kind, color)
);
CREATE INDEX ON gridfill.layers (session_id);
CREATE INDEX ON gridfill.layers (color);

-- ---- Cell-level vector spaces ----

-- Interpretation A: one learned 64-dim embedding per cell on the lattice.
-- 20,736 rows total (192 × 108). Retrained on every sync from the full event corpus.
CREATE TABLE gridfill.cell_embeddings (
  col          smallint NOT NULL,         -- 0..191
  row          smallint NOT NULL,         -- 0..107
  n_strikes    integer NOT NULL,          -- total times painted across all sessions
  trained_at   timestamptz NOT NULL,
  embedding    vector(64) NOT NULL,
  PRIMARY KEY (col, row)
);
CREATE INDEX ON gridfill.cell_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);

-- Pooled engram per drawing (composite layer): mean of its painted cells' embeddings.
-- Position-aware (because cell identity is in the embedding) but behaviorally weighted.
CREATE TABLE gridfill.drawing_engrams (
  layer_id    bigint PRIMARY KEY REFERENCES gridfill.layers(id),
  pool_method text NOT NULL,              -- 'mean' | 'sum' | 'weighted'
  n_cells     integer NOT NULL,
  embedding   vector(64) NOT NULL
);
CREATE INDEX ON gridfill.drawing_engrams USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);

-- Interpretation B: pixel-exact occupancy vector per drawing, PCA-reduced to 1024
-- because pgvector's hard cap is 16,000 dims and 20,736 is over it. The full
-- 20,736-dim sparse vector is also kept as a JSONB sparse map for lossless lookup.
CREATE TABLE gridfill.drawing_occupancy (
  layer_id     bigint PRIMARY KEY REFERENCES gridfill.layers(id),
  cells_sparse jsonb NOT NULL,            -- {"col,row": "#hex", ...} — lossless source of truth
  embedding    vector(1024) NOT NULL      -- PCA(20736 → 1024) on color-channel-flattened occupancy
);
CREATE INDEX ON gridfill.drawing_occupancy USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
```

**Storage sanity:** A full composite PNG at 1920×1080 with PNG-8 palette compression is typically <200 KB; per-color layers compress harder (mostly transparent). Even 100 sessions × 20 colors stays well under a gigabyte. If this grows uncomfortably, the PNG column can be moved to `bytea` external storage or dropped (JSON + SVG are sufficient for reconstruction).

Hardware note (per CLAUDE.md): no GPU. Features here are **deterministic statistics**, not learned embeddings — no `sentence-transformers`, no GPU question. If we ever switch to learned embeddings, force `device="cpu"`.

## Phase 3 — Sync (`sync_to_neon.py`)

Standalone script in `toolbox/gridfill/admin/sync_to_neon.py`:

1. Scan `~/.gridfill/events/*.jsonl`.
2. For each file not already in `gridfill.sessions`, open a transaction:
   - Parse `session_start` and `session_end` → `sessions` row.
   - Bulk-insert all events → `events`.
   - Project stroke events → `cell_strikes` (one row per cell painted, derived from `stroke_end` payloads).
   - Compute feature vector (Phase 4) → `session_vectors`.
   - Render layer artifacts (Phase 4a) → `layers` (one row per color + one composite).
3. After all per-session inserts, run **Phase 4b** (retrain cell embeddings on the full corpus) and **Phase 4c** (compute engrams + occupancy vectors for any new composite layers). These are corpus-level steps, not per-session.
4. Move processed JSONL to `~/.gridfill/events/processed/`.

Idempotent — re-running is safe (session_id is PK).

Uses `psycopg` with the NEON connection string from environment (same pattern as other toolbox scripts; verify with one `mcp__ragarmy-neon__list_schemas` call before first run).

## Phase 4 — Session feature vector (32-dim, deterministic)

Computed from a session's events. No model, no GPU.

```
[0:11]   tool-usage distribution    — fraction of strokes per tool (pad to 11 slots, current tools: cell/free/sheet/erase)
[11:21]  color-family distribution  — strokes binned by hue family (10 bins from HSV hue)
[21:25]  stroke-length quartiles    — cells-per-stroke at q25/q50/q75/q95
[25:28]  spatial stats              — mean col, mean row, std radius (centered at canvas center)
[28:30]  tempo                      — cells-per-minute, strokes-per-minute (log-scaled)
[30]     undo ratio                 — undos / strokes
[31]     palette breadth            — unique colors / log(strokes+1)
```

L2-normalized. Stored alongside a JSONB `features` breakdown so the dashboard can show "session 17 was 60% erase tool, blue-heavy, slow tempo" without re-decoding the vector.

UMAP projection runs server-side on demand (umap-learn, CPU) — cached per (n_sessions) key.

## Phase 4a — Layer rendering (`admin/layer_render.py`)

Called during sync, after `cell_strikes` insert.

For each `layer_snapshot` event in the spool:

- **Color layer** (`kind='color'`): one SVG `<rect>` per cell in that color against a transparent 1920×1080 viewport. Re-uses `renderer.py`'s `CELL_SIZE = 10` and grid geometry. PNG rasterized via `cairosvg` (CPU only).
- **Composite layer** (`kind='composite'`): all cells, true colors, against the charcoal background that matches the Tk canvas. Mirrors what `SvgRenderer` already produces — call `SvgRenderer().render(reconstructed_doc, grid_color)` directly, then rasterize.

JSON column is written verbatim from the event payload. Idempotent: `UNIQUE (session_id, kind, color)` makes re-runs a no-op via `ON CONFLICT DO NOTHING`.

No GPU: `cairosvg` is pure-CPU. No new model dependencies.

## Phase 4b — Cell embedding training (`admin/cell_embeddings.py`)

The cell-as-token vector space. Trained on the full corpus every sync.

**Token vocabulary.** Each cell is identified by an integer `cell_id = col * 108 + row`. Vocabulary size = 20,736 (fixed).

**Co-occurrence definitions, all four contribute training pairs:**

| Source                | Window definition                                                  | Weight |
| --------------------- | ------------------------------------------------------------------ | :----: |
| Same session          | All cells painted in the same session, regardless of order/time    |  0.25  |
| Same stroke           | Cells touched within one `stroke_start`→`stroke_end` block         |  1.00  |
| Temporal ±N sec       | Cells painted within ±5 seconds of each other (sliding window)     |  0.50  |
| Spatial + temporal    | Same as above but weighted by 1 / (1 + spatial_distance_cells)     |  0.75  |

The four streams are concatenated into one training corpus (each pair weighted via `gensim`'s `sample_int` or by repeating high-weight pairs). One gensim `Word2Vec` model (skip-gram, window=5, vector_size=64, min_count=2, workers=4, seed=42) trains in seconds on this corpus size.

**Output.** A 20,736 × 64 matrix. Written to `gridfill.cell_embeddings` via `INSERT ... ON CONFLICT (col, row) DO UPDATE`. `trained_at` is bumped on every retrain. Cells never painted (n_strikes = 0) are skipped — they have no signal.

**Stability.** Fixed seed + deterministic gensim build = reproducible embeddings across syncs. Drift between training runs is expected as the corpus grows; the dashboard can show "embeddings last trained at <ts>, N painted cells in corpus."

**Library:** `gensim` (CPU, no torch, no GPU question).

## Phase 4c — Drawing engrams + occupancy (`admin/drawing_vectors.py`)

For each new composite layer (post-sync), compute two vectors:

**Engram (Interpretation A, pooled cell embedding):**
1. Look up the embedding for each painted cell from `gridfill.cell_embeddings`.
2. Pool: mean of the cell embeddings (default). Optional weighted pool by strike-recency or by tool.
3. L2-normalize. Insert into `drawing_engrams` (PK = layer_id; `ON CONFLICT DO UPDATE` for retrains).
4. Engrams must be recomputed whenever cell embeddings are retrained — so this step always runs after Phase 4b. Idempotent.

**Occupancy (Interpretation B, pixel-exact):**
1. Build a 20,736-dim vector where slot `cell_id` = a numeric encoding of the color at that cell (HSV-flattened to one float, e.g., `hue * 0.6 + sat * 0.3 + val * 0.1`), or 0 if unpainted. This gives color-aware exact-match where two drawings using similar colors at the same cells score close.
2. Store the full sparse representation as `cells_sparse` JSONB (lossless, for exact diff).
3. PCA-reduce to 1024 dims using a corpus-fitted PCA (fit on all composite drawings, refit every sync). The principal components themselves are stored in a small companion table `gridfill.pca_model` so the dashboard can project a query drawing into the same space.
4. Insert into `drawing_occupancy`.

**Why both pooled-cell and occupancy?** Pooled-cell engrams give *behavioral* similarity ("drawings that activate similar cell-clusters"); occupancy gives *literal* similarity ("drawings that paint the same cells"). They answer different research questions and you wanted both.

**Library:** `numpy` for the occupancy build, `scikit-learn` `IncrementalPCA` for the dim reduction (CPU, fits the corpus size easily). No torch.

## Phase 5 — FastAPI dashboard (`admin/server.py`)

**Stack:** FastAPI + Uvicorn, vanilla HTML/CSS/JS, no build step. Styled with `configapa_mono.css` (copied or symlinked into `admin/static/`).

**Source for CSS:** `/home/dft/Desktop/Sean Workspace/.claude/agents/visual-design-engineer/assets/configs/configapa_mono.css`

**Routes:**

| Route                    | Returns                                                                    |
| ------------------------ | -------------------------------------------------------------------------- |
| `GET /`                  | Heatmap page — 192×108 SVG, color-by-prevalence, filters (session, tool, color, date range) |
| `GET /api/heatmap`       | JSON `[{col,row,count,dominant_color}]`, respects filter query params       |
| `GET /sessions`          | Session table — id, date, duration, strokes, cells, top color              |
| `GET /sessions/{id}`     | Single-session drilldown — feature breakdown, mini-heatmap, timeline strip |
| `GET /vectors`           | 2D scatter (UMAP projection), points colored by dominant tool, hoverable   |
| `GET /api/vectors`       | JSON `[{session_id, x, y, top_tool, top_color, n_strokes}]`                |
| `GET /layers`            | Layer gallery — sessions across rows, per-color thumbs + composite per row |
| `GET /api/layer/{id}.svg`| Inline SVG for a layer (rendered from `cells_json`, fast)                  |
| `GET /api/layer/{id}.png`| PNG bytes (served from `layers.png` column, `Content-Type: image/png`)     |
| `GET /api/layer/{id}.json`| Raw `cells_json` for programmatic use                                     |
| `GET /cells`             | Cell-embedding map — 2D UMAP projection of all 20,736 cell vectors, hoverable to see which cell is which lattice position |
| `GET /api/cells/similar` | KNN over `cell_embeddings` — query: `?col=X&row=Y&k=20` → returns nearest cells by behavioral similarity |
| `GET /engrams`           | Drawing engram gallery — UMAP of all composite engrams; click for "drawings near this one" |
| `GET /api/engrams/similar`| KNN over `drawing_engrams` — query: `?layer_id=X&k=10` → behavioral nearest drawings  |
| `GET /api/occupancy/similar`| KNN over `drawing_occupancy` — query: `?layer_id=X&k=10` → pixel-exact nearest drawings |

**Heatmap rendering:** SVG `<rect>` grid, 192×108 = 20,736 rects. Fine for an SVG of this size; one `<g>` per row to keep parse time down. Client renders from the JSON response.

**Scatter:** Plotly via CDN (single `<script>` tag) — keeps the page no-build but gives zoom/hover for free.

**Theme:** Charcoal background to match the Tk app's aesthetic; APA mono CSS for tabular sections (sessions list, feature breakdown).

## Phase 6 — Launch ergonomics

Add a `gridfill-admin` shell wrapper in `toolbox/gridfill/admin/`:

```bash
#!/usr/bin/env bash
cd "$(dirname "$0")"
python sync_to_neon.py          # one-shot ingest of any new spools
exec uvicorn server:app --host 127.0.0.1 --port 8765
```

Opens at `http://127.0.0.1:8765`. No auth. The Tk app remains unchanged in usage; telemetry is silent.

## Files

**New:**
- `toolbox/gridfill/telemetry.py` — JSONL writer + throttle
- `toolbox/gridfill/admin/sync_to_neon.py` — spool → Neon
- `toolbox/gridfill/admin/feature_vector.py` — 32-dim feature builder
- `toolbox/gridfill/admin/layer_render.py` — per-color + composite SVG/PNG rendering
- `toolbox/gridfill/admin/cell_embeddings.py` — gensim Word2Vec training over 4 co-occurrence streams
- `toolbox/gridfill/admin/drawing_vectors.py` — pooled engram + PCA occupancy per drawing
- `toolbox/gridfill/admin/server.py` — FastAPI app
- `toolbox/gridfill/admin/static/configapa_mono.css` — copy from agent assets
- `toolbox/gridfill/admin/static/dashboard.js` — heatmap + scatter rendering
- `toolbox/gridfill/admin/templates/index.html` — heatmap page
- `toolbox/gridfill/admin/templates/sessions.html` — session list
- `toolbox/gridfill/admin/templates/session_detail.html` — drilldown
- `toolbox/gridfill/admin/templates/vectors.html` — scatter
- `toolbox/gridfill/admin/templates/layers.html` — per-session layer gallery
- `toolbox/gridfill/admin/templates/cells.html` — cell-embedding UMAP + KNN explorer
- `toolbox/gridfill/admin/templates/engrams.html` — drawing engram gallery + nearest-neighbors view
- `toolbox/gridfill/admin/gridfill-admin` — launch script

**Modified (Tk side, ~30 lines total):**
- `toolbox/gridfill/app.py` — instantiate `Telemetry` in `__init__`, add `self.telemetry.log(...)` calls at the eleven hook points listed in Phase 1. Re-uses existing handlers: `_on_mode_change` (211), `_set_color` (199), `_on_brush_change` (227), `_on_press` (247), `_on_drag` (256), `_on_release` (265), `_undo` (497), `_save_svg` (515).

**Untouched:** `state.py`, `renderer.py`, `tools.py`, `ui.py`, `copic_palette.py`. The existing in-app `AdminPanel` (`ui.py:862`) stays — it's a useful at-a-glance view during a session and complements the web panel.

## Verification

1. **Tk hooks fire:** Launch `grid_fill.py`, paint a few strokes, switch tools, undo, save. Tail `~/.gridfill/events/<sid>.jsonl` and confirm all event types appear with plausible payloads.
2. **Throttle holds:** Drag a long stroke and confirm `stroke_point` rows are spaced ≥50ms apart.
3. **Sync is idempotent:** Run `sync_to_neon.py` twice in a row; row counts in `gridfill.events` should not double. Verify via `mcp__ragarmy-neon__query` row counts.
4. **Heatmap renders:** Open `http://127.0.0.1:8765/`, see the lattice with the painted cells lit. Apply a session filter and confirm the heatmap re-renders.
5. **Vector space populates:** After 3+ sessions exist, `/vectors` should show 3+ points. Sessions with similar tool/color profiles should land near each other.
6. **Cross-check counts:** `total_cells` in `gridfill.sessions` should equal `len(doc.cell_fills)` reported by the in-app `AdminPanel` at session end.
7. **Style sanity:** Confirm `configapa_mono.css` is loaded (Network tab) and tabular elements render in the mono APA aesthetic.
8. **Layer snapshot fires at close:** Paint cells in ≥2 colors, quit the Tk app cleanly. Tail the JSONL — expect one `layer_snapshot` event per color plus one `composite`, all *before* `session_end`.
9. **Layers materialize in Neon:** After sync, query `SELECT kind, color, n_cells FROM gridfill.layers WHERE session_id = $1` and confirm one row per color + one `composite` row whose `n_cells` equals the sum of color-layer `n_cells`.
10. **Layer rendering is faithful:** Open `/layers` for that session. The composite layer should match the final canvas. Each color layer should show only that color's cells against transparent background. PNG and SVG versions should be visually identical.
11. **Layer snapshot survives a Tk crash mid-session:** Kill the process with SIGKILL after a few strokes. Even without `session_end`, sync should still ingest the events. Layers won't exist (no snapshot fired), but the session row should be marked `ended_at = NULL` so the dashboard can flag it.
12. **Cell embeddings populate after first sync with ≥1 session:** `SELECT count(*) FROM gridfill.cell_embeddings` should equal the number of distinct cells ever painted (not 20,736 — never-painted cells are skipped). `trained_at` should match the sync time.
13. **Cell embedding sanity check:** Pick a cell you know you paint in "houses" (e.g., a roof apex location). Run `/api/cells/similar?col=X&row=Y&k=20`. Top results should be other cells that participate in your house drawings — not random cells across the canvas. If they're random, the corpus is too small or the co-occurrence weights need tuning.
14. **Engrams populate for every composite layer:** `SELECT count(*) FROM gridfill.drawing_engrams` should equal `SELECT count(*) FROM gridfill.layers WHERE kind = 'composite'`. After a retrain (Phase 4b), all engram rows should have updated values.
15. **Engram KNN returns sensible neighbors:** Pick a session where you drew a house. `/api/engrams/similar?layer_id=X&k=5` should surface other sessions where you drew houses, ahead of sessions where you drew unrelated subjects. This is the headline research validation; if it doesn't work, the embedding pipeline needs investigation before the dashboard is useful.
16. **Occupancy KNN returns near-duplicates:** Take a composite, save it, paint a near-identical one in a new session, sync. `/api/occupancy/similar?layer_id=<new>&k=3` should return the original as the nearest neighbor with a small cosine distance.
17. **PCA model persists and is reused:** `gridfill.pca_model` should hold one row of fitted components. Querying a new drawing through `/api/occupancy/similar` should project it through the *same* PCA used during training, not a fresh fit.

## Out of scope (not in this build)

- Live websocket updates (sync is batch on quit/manual).
- Multi-machine aggregation.
- Authentication.
- Replaying a session as an animation (could come later; data model supports it).
- Learned (transformer) embeddings of stroke sequences — deferred; if added, force `device="cpu"`.
- CNN perceptual embeddings of the rendered PNG (MobileNet etc.) — deferred until cell-embedding engrams prove insufficient for shape-class retrieval. CPU only if added.
- Engram per *connected component* within a drawing (the "house + car in one canvas" case) — current plan signatures the whole composite. Component-level engrams are a clean follow-up: same `drawing_engrams` shape, just one row per component instead of per composite.
