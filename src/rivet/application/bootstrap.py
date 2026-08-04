from __future__ import annotations

import inspect
import shutil
import sys
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rivet.application.service import ApplicationService
from rivet.code_intelligence import PythonAstAnalyzer
from rivet.code_intelligence.indexer import WorkspaceIndexer
from rivet.code_intelligence.lsp import LspManager, discover_python_server
from rivet.code_intelligence.retrieval import (
    HashEmbeddingModel,
    HybridRetriever,
    InMemoryDenseIndex,
    LexicalReranker,
    QdrantChunkIndex,
    SqliteSparseIndex,
)
from rivet.configuration import RivetConfig, load_config
from rivet.context import DefaultContextEngine
from rivet.domain import RunBudget
from rivet.model.factory import build_model_gateway
from rivet.observability import EventStream, JsonlEventSink
from rivet.reviewer import ModelReviewer
from rivet.runtime import RuntimeEngine, RuntimeSettings
from rivet.state.artifacts import ContentAddressedArtifactStore
from rivet.state.layout import StateLayout
from rivet.state.sqlite import SQLiteStateStore
from rivet.tools.builtins import (
    ApplyPatchTool,
    GitDiffTool,
    GitStatusTool,
    ListFilesTool,
    ReadFileTool,
    RunCommandTool,
    RunTestsTool,
    SearchTextTool,
)
from rivet.tools.builtins.code_intelligence import code_intelligence_tools
from rivet.tools.catalog import ToolCatalog
from rivet.tools.executor import ToolExecutor
from rivet.verification import (
    DefaultVerifier,
    VerificationCommand,
    VerificationPlan,
)
from rivet.workspace.boundary import WorkspaceBoundary
from rivet.workspace.checkpoint import FileCheckpointService
from rivet.workspace.command import ProcessRunner
from rivet.workspace.permissions import ConfigPermissionBroker


@dataclass
class ApplicationHarness:
    service: ApplicationService
    config: RivetConfig
    layout: StateLayout
    event_stream: EventStream
    _resources: list[Any] = field(default_factory=list, repr=False)

    async def close(self) -> None:
        for resource in reversed(self._resources):
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            result = close()
            if inspect.isawaitable(result):
                await result

    async def __aenter__(self) -> ApplicationHarness:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


def build_application(
    workspace: Path,
    *,
    overrides: dict[str, Any] | None = None,
    model_gateway: Any | None = None,
    state_root: Path | None = None,
    model_visible_tools: Collection[str] | None = None,
) -> ApplicationHarness:
    root = workspace.expanduser().resolve()
    loaded = load_config(root, overrides=overrides)
    config = loaded.config
    configured_state = state_root or config.state.root
    layout = StateLayout.for_workspace(root, state_root=configured_state).create()
    boundary = WorkspaceBoundary(root)
    state = SQLiteStateStore(layout.database_path)
    artifacts = ContentAddressedArtifactStore(
        layout.artifacts_root,
        max_bytes=config.runtime.max_artifact_bytes,
    )
    artifacts.initialize()
    process_runner = ProcessRunner(boundary)
    checkpoint_service = FileCheckpointService(layout.workspace_state_root / "checkpoints")
    permission_broker = ConfigPermissionBroker(config.permissions)
    catalog = ToolCatalog(
        [
            ListFilesTool(),
            ReadFileTool(),
            SearchTextTool(),
            GitStatusTool(),
            GitDiffTool(),
            ApplyPatchTool(),
            RunCommandTool(),
            RunTestsTool(),
            *code_intelligence_tools(),
        ],
        model_visible_names=model_visible_tools,
    )
    executor = ToolExecutor(
        catalog,
        boundary,
        permission_broker=permission_broker,
        checkpoint_service=checkpoint_service,
    )

    resources: list[Any] = [state]
    services: dict[str, Any] = {
        "process_runner": process_runner,
        "python_ast_analyzer": PythonAstAnalyzer(),
    }
    indexer = None
    if config.retrieval.enabled:
        sparse_index = SqliteSparseIndex(layout.indexes_root / "sparse.sqlite3")
        resources.append(sparse_index)
        dense_index = None
        if config.retrieval.dense:
            embedding_model = HashEmbeddingModel()
            if config.retrieval.qdrant_url:
                collection_name = (
                    f"{config.retrieval.collection_prefix}_{_workspace_key(root)[:16]}"
                )
                dense_index = QdrantChunkIndex(
                    collection_name,
                    embedding_model,
                    client_options={"url": config.retrieval.qdrant_url},
                )
                resources.append(dense_index)
            else:
                dense_index = InMemoryDenseIndex(embedding_model)
        indexer = WorkspaceIndexer(
            workspace_root=root,
            workspace_id=_workspace_key(root),
            sparse_index=sparse_index,
            additional_indexes=(dense_index,) if dense_index is not None else (),
        )
        indexer.refresh()
        retriever = HybridRetriever(
            sparse=sparse_index if config.retrieval.sparse else None,
            dense=dense_index,
            reranker=LexicalReranker() if config.retrieval.reranker else None,
            candidate_limit=max(
                config.retrieval.top_k_sparse,
                config.retrieval.top_k_dense,
            ),
        )
        services["retriever"] = retriever
        services["workspace_indexer"] = indexer

    python_server = discover_python_server()
    if python_server is not None:
        lsp_manager = LspManager(root, (python_server,))
        resources.append(lsp_manager)
        services["lsp_manager"] = lsp_manager

    gateway = model_gateway or build_model_gateway(config)
    if model_gateway is None:
        resources.append(gateway)
    event_stream = EventStream()
    trace_sink = JsonlEventSink(layout.logs_root / "events.jsonl")
    event_stream.subscribe(trace_sink)
    verifier = DefaultVerifier(process_runner)
    reviewer = ModelReviewer(gateway, model=config.model.model) if config.reviewer.enabled else None
    runtime = RuntimeEngine(
        state_store=state,
        context_engine=DefaultContextEngine(),
        model_gateway=gateway,
        tool_catalog=catalog,
        tool_executor=executor,
        artifact_store=artifacts,
        verifier=verifier,
        reviewer=reviewer,
        verification_plan_factory=lambda run, paths, events: _verification_plan(
            root,
            paths,
            config.runtime.max_command_time_seconds,
        ),
        event_publisher=event_stream,
        settings=RuntimeSettings(
            provider_name=config.model.provider,
            model_name=config.model.model or "unconfigured",
            lease_ttl_seconds=config.runtime.run_lease_seconds,
            context_input_tokens_per_call=config.context.max_input_tokens,
            output_tokens_per_call=config.context.reserve_output_tokens,
            stream_model=config.model.stream,
            model_max_retries=config.model.max_retries,
            reviewer_blocking_severities=config.reviewer.blocking_severities,
            max_consecutive_identical_actions=2,
            tool_services=services,
        ),
    )
    budget = RunBudget(
        max_turns=config.runtime.max_turns,
        max_model_calls=config.runtime.max_model_calls,
        max_reviewer_calls=config.reviewer.max_calls,
        max_tool_executions=config.runtime.max_tool_executions,
        max_wall_time_seconds=config.runtime.max_wall_time_seconds,
        max_command_time_seconds=config.runtime.max_command_time_seconds,
        max_artifact_bytes=config.runtime.max_artifact_bytes,
    )
    snapshot = config.model_dump(mode="json")
    service = ApplicationService(
        workspace_root=root,
        boundary=boundary,
        runtime=runtime,
        state=state,
        config_snapshot=snapshot,
        default_budget=budget,
        checkpoint_service=checkpoint_service,
        event_publisher=event_stream,
        index_refresher=indexer,
    )
    return ApplicationHarness(
        service=service,
        config=config,
        layout=layout,
        event_stream=event_stream,
        _resources=resources,
    )


def _verification_plan(
    workspace: Path,
    changed_paths: tuple[str, ...],
    timeout_seconds: float,
) -> VerificationPlan:
    commands: list[VerificationCommand] = []
    if (workspace / "tests").is_dir() or (workspace / "pyproject.toml").is_file():
        commands.append(
            VerificationCommand(
                name="python_tests",
                argv=(sys.executable, "-m", "pytest", "-q"),
                timeout_seconds=timeout_seconds,
            )
        )
    ruff = shutil.which("ruff")
    if ruff and (workspace / "pyproject.toml").is_file():
        commands.append(
            VerificationCommand(
                name="ruff",
                argv=(ruff, "check", "."),
                timeout_seconds=timeout_seconds,
            )
        )
    if not commands:
        commands.append(
            VerificationCommand(
                name="python_syntax",
                argv=(
                    sys.executable,
                    "-c",
                    (
                        "import ast,pathlib;"
                        "[ast.parse(p.read_text(encoding='utf-8')) "
                        "for p in pathlib.Path('.').rglob('*.py') "
                        "if not any(x in p.parts for x in "
                        "('.git','.venv','__pycache__'))]"
                    ),
                ),
                timeout_seconds=timeout_seconds,
            )
        )
    return VerificationPlan(
        commands=tuple(commands),
        allowed_changed_paths=changed_paths,
        require_diff=True,
    )


def _workspace_key(root: Path) -> str:
    from rivet.domain.common import workspace_id_for

    return workspace_id_for(root)
