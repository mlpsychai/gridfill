# gridfill scribe

## 2026-05-23 — Session: HF readiness audit → GitHub + Fly deploy

### Opening question

User asked: is gridfill ready to push to Hugging Face? Audit revealed a fundamental mismatch.

- Two `gridfill` directories existed: `~/Desktop/Sean Workspace/gridfill/` (HF Space scaffold, `sdk: static`, 4 files of boilerplate) and `~/Desktop/Sean Workspace/toolbox/gridfill/` (the actual Tkinter desktop app — `app.py`, `ui.py`, `tools.py`, `renderer.py`, `state.py`, `telemetry.py`, palettes, `admin/`)
- HF Static Spaces serve HTML/CSS/JS; the real app uses Tk (verified by grep — zero `gradio`/`streamlit`/`flask`/`fastapi` imports anywhere)
- Push-as-static was never going to render. Static Space `index.html` was still the HF placeholder ("Welcome to your static Space!"), `style.css` the matching template.

### User decision

Pick: **Docker on GitHub + deploy elsewhere.** Skip HF entirely. Plan agreed across nine steps: dependency manifest, Dockerfile + start.sh, ignore files, README + LICENSE, admin/ placement, GitHub repo, GHCR CI, pick host, retire HF Space.

### Step-by-step execution

- **Step 1 — Dependency manifest.** Grep of all imports across `app.py`, `ui.py`, `tools.py`, `renderer.py`, `state.py`, `telemetry.py`, `copic_palette.py`, `curated_palettes.py`, `grid_fill.py`: every import is stdlib or local. Zero third-party Python deps. `requirements.txt` written as a comment-only manifest documenting this so future readers don't think it was forgotten. The `.venv/` deps (cairosvg, gensim, numba, umap, pynndescent, smart_open) are admin/-only.

- **Step 2 — Containerize.** Wrote `Dockerfile` and `start.sh`. Base: **debian:bookworm-slim**, not python:3.11-slim. Reason: `python:slim`'s Python is built outside apt, and `python3-tk` from apt installs `_tkinter` for the system Python at `/usr/bin/python3`, not for `/usr/local/bin/python3.x`. Debian's `python3` + `python3-tk` from the same apt source imports cleanly. Stack inside the container: tini → Xvfb (`:1`, 1920×1200×24) → fluxbox → x11vnc → websockify+noVNC on 6080 → `python3 grid_fill.py`. Local smoke test on host docker: all five app processes alive in the container, `curl http://localhost:6080/vnc.html` returned 200.

- **Step 3 — Ignore files.** `.gitignore` excludes `admin/`, `.venv/`, `__pycache__/`, `*.pyc`, `assets/`, `saved_svgs/`, `jpeg/`, `scr/`, `.claude/`. `.dockerignore` not needed since the Dockerfile uses explicit `COPY` of named files, not `COPY . .`.

- **Step 4 — README + LICENSE.** README covers local Python run, Docker run with the noVNC URL, full feature/architecture tables (sourced from `docs/feature_inventory.md`), telemetry disclosure (JSONL spool to `~/.gridfill/events/<session_id>.jsonl`, local-only), pointer to the feature inventory. LICENSE is MIT under "Sean Mapoles" (user-confirmed; first draft was `mlpsychai` but the GitHub profile bio gave the real name).

- **Step 5 — admin/ placement.** Decision: `.gitignore` excludes; `admin/` stays local-only. Carries Neon sync paths and the telemetry server, not safe to ship publicly. Move-out-of-directory alternative was offered but `.gitignore` chosen for reversibility.

- **Step 6 — GitHub repo.** `gh` CLI installed via apt (Ubuntu universe ships 2.45.0). User authed as `mlpsychai` via the device-code flow. Repo created public via `gh repo create mlpsychai/gridfill --public --source=. --remote=origin --push`. Remote switched from the gh-default HTTPS to SSH (`git@github.com:mlpsychai/gridfill.git`) to match the existing `researchrag` pattern.

- **Step 7 — GHCR CI.** Workflow `.github/workflows/docker.yml` builds on push to main + manual dispatch, pushes to `ghcr.io/mlpsychai/gridfill` with tags `latest` / `main` / `sha-<short>`. First run green in 1m49s. Package shipped public on first publish (ghcr inherited the repo's public visibility — no manual flip needed). User flagged the Node 20 deprecation warning; bumped all five actions to Node-24-supporting majors: `checkout` v4→v6, `setup-buildx-action` v3→v4, `login-action` v3→v4, `metadata-action` v5→v6, `build-push-action` v6→v7. Cleared the warning in the next run (18s, full cache hit).

- **Step 8 — Fly.io deploy.** Picked `lax` after `phx` was deprecated mid-deploy. `shared-cpu-1x:512MB`, `auto_stop_machines = "stop"`, `min_machines_running = 0`. Trial-org card requirement gated the first deploy attempt. Then the bug-fix arc:
  - First successful deploy: "no listener on 0.0.0.0:6080" warning despite all processes alive. Diagnosed via `fly ssh console` + `/proc/net/tcp{,6}`: websockify bound IPv4-only; Fly's fly-proxy connects via IPv6.
  - Attempt 1: `--listen-host=::` flag. Died as a zombie. The Debian-packaged websockify (~0.10) doesn't have that flag.
  - Attempt 2: positional `[::]:6080` source spec. Worked. Linux dual-stack picks up IPv4 too.
  - Live at `https://gridfill.fly.dev`. Curl probe: `/vnc.html` HTTP 200, cold-start ~21s, warm <200ms.
  - User flagged the bare URL serving a directory listing of noVNC files. Added `index.html` redirect (meta-refresh + JS `location.replace` + visible fallback link) installed into `/usr/share/novnc/index.html`. `/` now lands on the canvas with autoconnect armed.
  - User reported cursor-drag latency. Tuned x11vnc: `-defer 1 -wait 5 -nonap` (was 40ms batch / 30ms poll / nap-enabled defaults). Marked good-enough for the demo's purpose.

- **Step 9 — Retire HF Space.** Deleted `smapoles/gridfill` via `hf repo delete smapoles/gridfill --type space`. Local clone at `~/Desktop/Sean Workspace/gridfill/` removed. Verified: HF URL now returns HTTP 401 (gone), workspace root has no `gridfill` directory outside `toolbox/`.

### Final state

- **GitHub:** [`mlpsychai/gridfill`](https://github.com/mlpsychai/gridfill), public, default branch `main`, SSH remote
- **Image:** `ghcr.io/mlpsychai/gridfill:latest`, public, multi-tag (`latest` / `main` / `sha-<short>`)
- **Demo:** [`https://gridfill.fly.dev`](https://gridfill.fly.dev) — bare URL auto-redirects to `vnc.html?autoconnect=true&resize=scale`
- **License:** MIT, Sean Mapoles
- **HF Space:** gone

### Lessons worth carrying forward

- `python:3.11-slim` + `apt python3-tk` does NOT bind tkinter into the image's Python. Use `debian:bookworm-slim` + `python3` + `python3-tk` for Tk apps that need to stay in pure Debian apt.
- Fly.io's internal `fly-proxy` is **IPv6-only**. Any service must bind IPv6 (Linux dual-stack via `[::]` covers both). The deploy will report "no listener" even when the service is alive on IPv4.
- Debian-packaged websockify (~0.10) is older than upstream — `--listen-host` isn't there. Use the portable positional `[host:]port` form.
- x11vnc defaults (`-defer 40 -wait 30` + nap mode) are tuned for desktop kiosks where motion latency doesn't matter. Drawing apps need `-defer 1 -wait 5 -nonap`; trades CPU for responsiveness.
- VNC over public internet from AZ → LAX has a ~30ms RTT floor. No tuning fixes this. Native browser canvas (JS) is the only path to <10ms feel.

### Open items / future work

- Cursor latency is "good enough" but not native-feeling. If the demo needs to feel snappier later: bump to `performance-1x` ($8/mo, dedicated 1 vCPU) first; if still not enough, commit to a vanilla JS Canvas rewrite (days of work, no server in the loop).
- `auto_stop_machines = "stop"` gives ~20s cold start. Could switch to `"suspend"` for ~500ms wake at the cost of higher idle billing — defer until the demo gets real traffic.
- The `admin/` subtree (Neon sync, embeddings, telemetry server, test suite) is local-only and excluded from both git and the container. If the embedding/visualization work ever ships publicly, it gets its own image and repo, not bolted onto gridfill.
