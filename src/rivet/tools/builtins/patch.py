from __future__ import annotations

from dataclasses import replace

from pydantic import Field

from rivet.tools.contracts import (
    EffectClass,
    PermissionClass,
    PreparedTool,
    ToolArguments,
    ToolExecutionContext,
    ToolPreparation,
    ToolPrepareContext,
    ToolSpec,
)
from rivet.tools.results import (
    DiffBlock,
    ErrorKind,
    SideEffectState,
    ToolResult,
)
from rivet.workspace.patch import AtomicPatchApplier, PatchConflict, TextEdit


class TextEditArguments(ToolArguments):
    path: str = Field(min_length=1, max_length=4_096)
    old_text: str = Field(max_length=1_000_000)
    new_text: str = Field(max_length=1_000_000)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    replace_all: bool = False
    create_if_missing: bool = False


class ApplyPatchArguments(ToolArguments):
    edits: list[TextEditArguments] = Field(min_length=1, max_length=100)


class ApplyPatchTool:
    spec = ToolSpec(
        name="apply_patch",
        version="1.0.0",
        description="Apply exact text replacements using checkpointed atomic file writes.",
        input_model=ApplyPatchArguments,
        output_types=(DiffBlock,),
        effect=EffectClass.WRITE,
        permission=PermissionClass.WORKSPACE_WRITE,
        default_timeout=30.0,
        idempotent=False,
        parallel_safe=False,
    )

    def __init__(self, *, checkpoint_required: bool = True) -> None:
        self.checkpoint_required = checkpoint_required
        if not checkpoint_required:
            self.spec = replace(
                type(self).spec,
                description=(
                    "Apply exact text replacements using atomic file writes "
                    "without a recovery snapshot."
                ),
            )

    def prepare(
        self,
        arguments: ApplyPatchArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        targets = []
        normalized_edits = []
        seen: set[str] = set()
        for edit in arguments.edits:
            target = context.workspace.resolve(
                edit.path,
                must_exist=not edit.create_if_missing,
                for_write=True,
                allow_final_symlink=False,
            )
            if target.relative_path in seen:
                raise ValueError(f"duplicate patch target: {target.relative_path}")
            seen.add(target.relative_path)
            targets.append(target)
            normalized = edit.model_dump(mode="json")
            normalized["path"] = target.relative_path
            normalized_edits.append(normalized)
        return ToolPreparation(
            normalized_arguments={"edits": normalized_edits},
            resolved_targets=tuple(targets),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        if self.checkpoint_required and context.checkpoint is None:
            return ToolResult.error(
                ErrorKind.CHECKPOINT_ERROR,
                "apply_patch requires a valid checkpoint",
            )
        arguments = ApplyPatchArguments.model_validate(prepared.normalized_arguments)
        edits = tuple(
            TextEdit(
                target=target,
                old_text=edit.old_text,
                new_text=edit.new_text,
                expected_sha256=edit.expected_sha256,
                replace_all=edit.replace_all,
                create_if_missing=edit.create_if_missing,
            )
            for target, edit in zip(
                prepared.resolved_targets,
                arguments.edits,
                strict=True,
            )
        )
        try:
            result = AtomicPatchApplier(context.workspace).apply(edits)
        except PatchConflict as exc:
            return ToolResult.error(
                ErrorKind.WORKSPACE_CHANGED,
                str(exc),
            )
        return ToolResult.success(
            DiffBlock(
                result.unified_diff,
                paths=tuple(write.path for write in result.writes),
            ),
            side_effect_state=SideEffectState.APPLIED,
            metadata={
                "writes": [
                    {
                        "path": write.path,
                        "before_sha256": write.before_sha256,
                        "after_sha256": write.after_sha256,
                        "bytes_written": write.bytes_written,
                    }
                    for write in result.writes
                ]
            },
        )
