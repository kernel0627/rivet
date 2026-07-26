from __future__ import annotations

import difflib
from dataclasses import dataclass

from rivet.workspace.boundary import ResolvedPath, WorkspaceBoundary
from rivet.workspace.transaction import (
    AtomicWriteResult,
    atomic_write_bytes,
    file_sha256,
)


class PatchConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class TextEdit:
    target: ResolvedPath
    old_text: str
    new_text: str
    expected_sha256: str | None = None
    replace_all: bool = False
    create_if_missing: bool = False


@dataclass(frozen=True)
class PatchResult:
    writes: tuple[AtomicWriteResult, ...]
    unified_diff: str


class AtomicPatchApplier:
    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        max_file_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.boundary = boundary
        self.max_file_bytes = max_file_bytes

    def apply(self, edits: tuple[TextEdit, ...]) -> PatchResult:
        if not edits:
            raise PatchConflict("patch must contain at least one edit")
        relative_paths = [edit.target.relative_path for edit in edits]
        if len(relative_paths) != len(set(relative_paths)):
            raise PatchConflict("a patch cannot edit the same path more than once")

        prepared: list[tuple[TextEdit, str, str, str | None, int | None]] = []
        diffs: list[str] = []
        for edit in edits:
            target = self.boundary.revalidate(
                edit.target,
                require_unchanged=True,
            )
            if target.existed:
                if target.path.stat().st_size > self.max_file_bytes:
                    raise PatchConflict(
                        f"patch target exceeds {self.max_file_bytes} bytes: {target.relative_path}"
                    )
                raw = target.path.read_bytes()
                try:
                    before = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise PatchConflict(
                        f"patch target is not UTF-8 text: {target.relative_path}"
                    ) from exc
                before_sha256 = file_sha256(target.path)
                mode = target.path.stat().st_mode & 0o7777
            else:
                if not edit.create_if_missing:
                    raise PatchConflict(f"patch target does not exist: {target.relative_path}")
                before = ""
                before_sha256 = None
                mode = None
            if edit.expected_sha256 is not None and edit.expected_sha256 != before_sha256:
                raise PatchConflict(f"expected hash does not match: {target.relative_path}")

            if edit.create_if_missing and not target.existed and edit.old_text == "":
                after = edit.new_text
            else:
                occurrences = before.count(edit.old_text)
                if occurrences == 0:
                    raise PatchConflict(f"old_text was not found: {target.relative_path}")
                if occurrences > 1 and not edit.replace_all:
                    raise PatchConflict(
                        f"old_text is ambiguous ({occurrences} matches): {target.relative_path}"
                    )
                after = before.replace(
                    edit.old_text,
                    edit.new_text,
                    -1 if edit.replace_all else 1,
                )
            prepared.append((edit, before, after, before_sha256, mode))
            diffs.extend(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=f"a/{target.relative_path}",
                    tofile=f"b/{target.relative_path}",
                )
            )

        writes: list[AtomicWriteResult] = []
        for edit, _before, after, before_sha256, mode in prepared:
            writes.append(
                atomic_write_bytes(
                    self.boundary,
                    edit.target,
                    after.encode("utf-8"),
                    expected_sha256=before_sha256,
                    mode=mode,
                )
            )
        return PatchResult(
            writes=tuple(writes),
            unified_diff="".join(diffs),
        )
