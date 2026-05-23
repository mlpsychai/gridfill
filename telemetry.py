"""Telemetry — JSONL event spool for Grid Filler sessions.

Self-contained module. Import `Telemetry`, instantiate once in the app's
__init__, call `.log(event_type, payload)` at the hook points listed in
docs/admin_panel_plan.md. The instance registers its own atexit hook to
emit `session_end` (and `layer_snapshot` rows if a doc reference was given).

Design constraints:
  - Single-process, single-thread Tk app — no locking needed.
  - Durability over throughput — flush every line; small files anyway.
  - Never raise into the host app. Telemetry failure is silent.
  - Stroke-point events are throttled per active stroke (see THROTTLE_MS).

Spool location: ~/.gridfill/events/<session_id>.jsonl
"""

from __future__ import annotations

import atexit
import json
import os
import secrets
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

APP_VERSION = "0.1.0"
SPOOL_DIR = Path.home() / ".gridfill" / "events"
THROTTLE_MS = 50  # max one stroke_point per active stroke per this many ms


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


class Telemetry:
    """JSONL writer with per-stroke throttle and atexit lifecycle."""

    def __init__(
        self,
        doc_ref: Any = None,
        *,
        palette_hash: str | None = None,
        screen_dims: tuple[int, int] | None = None,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.session_id = _make_session_id()
        self.doc_ref = doc_ref  # late-bound; layer_snapshot reads cell_fills here

        self._started_at = time.monotonic()
        self._fp = None
        self._strokes_logged = 0
        self._last_stroke_point_ms = 0.0
        self._closed = False

        if not self.enabled:
            return

        try:
            SPOOL_DIR.mkdir(parents=True, exist_ok=True)
            path = SPOOL_DIR / f"{self.session_id}.jsonl"
            self._fp = path.open("a", encoding="utf-8")
        except Exception:
            self.enabled = False
            return

        self._emit("session_start", {
            "session_id": self.session_id,
            "app_version": APP_VERSION,
            "palette_hash": palette_hash,
            "screen_w": (screen_dims or (None, None))[0],
            "screen_h": (screen_dims or (None, None))[1],
            "host": socket.gethostname(),
            "user": os.environ.get("USER") or os.environ.get("USERNAME"),
            "python": sys.version.split()[0],
        })

        atexit.register(self._on_exit)

    # ---------- Public API ----------

    def log(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        """Append one event to the spool. Never raises."""
        if not self.enabled or self._closed:
            return

        if event_type == "stroke_point":
            now_ms = time.monotonic() * 1000.0
            if now_ms - self._last_stroke_point_ms < THROTTLE_MS:
                return
            self._last_stroke_point_ms = now_ms

        if event_type == "stroke_start":
            # Reset throttle window for the new stroke.
            self._last_stroke_point_ms = 0.0

        if event_type == "stroke_end":
            self._strokes_logged += 1

        self._emit(event_type, dict(payload or {}))

    def set_doc_ref(self, doc_ref: Any) -> None:
        """Late-bind the Document so layer_snapshot can read cell_fills."""
        self.doc_ref = doc_ref

    def close(self) -> None:
        """Idempotent. Emits layer_snapshot + session_end and closes the file."""
        if self._closed or not self.enabled:
            return
        self._closed = True

        try:
            self._emit_layer_snapshot()
        except Exception:
            pass

        try:
            duration = int(time.monotonic() - self._started_at)
            total_cells = 0
            n_colors = 0
            if self.doc_ref is not None:
                cell_fills = getattr(self.doc_ref, "cell_fills", None) or {}
                total_cells = len(cell_fills)
                n_colors = len(set(cell_fills.values()))
            self._emit("session_end", {
                "duration_s": duration,
                "total_strokes": self._strokes_logged,
                "total_cells": total_cells,
                "n_colors": n_colors,
            })
        except Exception:
            pass

        try:
            if self._fp is not None:
                self._fp.close()
        except Exception:
            pass

    # ---------- Internals ----------

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._fp is None:
            return
        row = {
            "session_id": self.session_id,
            "ts": _now_iso(),
            "event_type": event_type,
            "payload": payload,
        }
        try:
            self._fp.write(json.dumps(row, separators=(",", ":")) + "\n")
            self._fp.flush()
        except Exception:
            pass

    def _emit_layer_snapshot(self) -> None:
        """One event per distinct color + one composite. Pre-session_end."""
        if self.doc_ref is None:
            return
        cell_fills = getattr(self.doc_ref, "cell_fills", None)
        if not cell_fills:
            return

        by_color: dict[str, list[list[int]]] = {}
        for (col, row), color in cell_fills.items():
            by_color.setdefault(color, []).append([int(col), int(row)])

        for color, cells in by_color.items():
            self._emit("layer_snapshot", {
                "kind": "color",
                "color": color,
                "n_cells": len(cells),
                "cells": cells,
            })

        composite = {color: cells for color, cells in by_color.items()}
        self._emit("layer_snapshot", {
            "kind": "composite",
            "n_cells": sum(len(c) for c in by_color.values()),
            "n_colors": len(by_color),
            "cells": composite,
        })

    def _on_exit(self) -> None:
        self.close()
