"""Document state, primitives, and undo/redo for gridfill.

All coordinates here are in SVG space (1920x1080), never screen pixels.
The Document is the single source of truth; renderers read from it,
tools mutate it through Actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


# ---------- Primitives ----------

@dataclass(frozen=True)
class BrushPattern:
    label: str
    dash: int
    gap: int

    @property
    def is_solid(self) -> bool:
        return self.dash <= 0 or self.gap <= 0


SOLID = BrushPattern("Solid", 0, 0)


@dataclass
class CellFill:
    col: int
    row: int
    color: str


@dataclass
class Stroke:
    """Free-draw stroke primitive.

    Legacy: retained for safe replay of any pre-existing strokes (and
    EraseAction / ClearAction snapshots that reference them). No live
    tool produces Strokes anymore — free-draw now paints into
    Document.cell_fills like cell-fill does.
    """
    color: str
    size: tuple[int, int]
    points: list[tuple[float, float]]
    pattern: BrushPattern = SOLID


# ---------- Document ----------

@dataclass
class Document:
    """Holds all drawable state. Mutated only through Actions."""

    cell_fills: dict[tuple[int, int], str] = field(default_factory=dict)
    strokes: list[Stroke] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.cell_fills or self.strokes)

    def clear(self) -> None:
        self.cell_fills.clear()
        self.strokes.clear()


# ---------- Actions ----------

class Action(Protocol):
    """Reversible mutation against a Document."""

    def apply(self, doc: Document) -> None: ...
    def revert(self, doc: Document) -> None: ...


@dataclass
class SetCellAction:
    col: int
    row: int
    new_color: str
    prev_color: str | None = None  # captured on apply

    def apply(self, doc: Document) -> None:
        self.prev_color = doc.cell_fills.get((self.col, self.row))
        doc.cell_fills[(self.col, self.row)] = self.new_color

    def revert(self, doc: Document) -> None:
        if self.prev_color is None:
            doc.cell_fills.pop((self.col, self.row), None)
        else:
            doc.cell_fills[(self.col, self.row)] = self.prev_color


@dataclass
class BatchCellAction:
    """Drag-fill produces many cell mutations; collapse to one undo step."""

    changes: list[SetCellAction] = field(default_factory=list)

    def add(self, change: SetCellAction) -> None:
        self.changes.append(change)

    def apply(self, doc: Document) -> None:
        # Apply order matters for prev_color capture; redo replays in order.
        for ch in self.changes:
            ch.apply(doc)

    def revert(self, doc: Document) -> None:
        for ch in reversed(self.changes):
            ch.revert(doc)


@dataclass
class AddStrokeAction:
    stroke: Stroke

    def apply(self, doc: Document) -> None:
        doc.strokes.append(self.stroke)

    def revert(self, doc: Document) -> None:
        # Identity-based removal; safe even after redo cycles.
        doc.strokes.remove(self.stroke)


@dataclass
class EraseAction:
    """Records what was removed so revert can reinstate it exactly."""

    removed_cells: dict[tuple[int, int], str] = field(default_factory=dict)
    removed_strokes: list[Stroke] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.removed_cells or self.removed_strokes)

    def apply(self, doc: Document) -> None:
        # On initial application the removals already happened in-place
        # (the erase tool snapshots as it goes). Redo path re-removes.
        for key in self.removed_cells:
            doc.cell_fills.pop(key, None)
        for st in self.removed_strokes:
            if st in doc.strokes:
                doc.strokes.remove(st)

    def revert(self, doc: Document) -> None:
        for key, color in self.removed_cells.items():
            doc.cell_fills[key] = color
        doc.strokes.extend(self.removed_strokes)


@dataclass
class ClearAction:
    """Wipe everything; revert restores the snapshot."""

    snapshot_cells: dict[tuple[int, int], str] = field(default_factory=dict)
    snapshot_strokes: list[Stroke] = field(default_factory=list)

    def apply(self, doc: Document) -> None:
        self.snapshot_cells = dict(doc.cell_fills)
        self.snapshot_strokes = list(doc.strokes)
        doc.clear()

    def revert(self, doc: Document) -> None:
        doc.cell_fills.update(self.snapshot_cells)
        doc.strokes.extend(self.snapshot_strokes)


# ---------- Undo stack ----------

class UndoStack:
    """Two-stack undo/redo. New actions clear the redo stack."""

    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self._undo: list[Action] = []
        self._redo: list[Action] = []

    def do(self, action: Action) -> None:
        action.apply(self.doc)
        self._undo.append(action)
        self._redo.clear()

    def record(self, action: Action) -> None:
        """Register an already-applied action (e.g., live drag) without re-applying."""
        self._undo.append(action)
        self._redo.clear()

    def undo(self) -> Action | None:
        if not self._undo:
            return None
        action = self._undo.pop()
        action.revert(self.doc)
        self._redo.append(action)
        return action

    def redo(self) -> Action | None:
        if not self._redo:
            return None
        action = self._redo.pop()
        action.apply(self.doc)
        self._undo.append(action)
        return action

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
