from __future__ import annotations

import json
import unittest

from pydantic import Field

from rivet.tools.catalog import DuplicateToolError, ToolCatalog
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
from rivet.tools.results import ErrorKind, TextBlock, ToolResult


class ExampleArguments(ToolArguments):
    text: str
    count: int = Field(default=1, ge=1, le=5)


class ExampleTool:
    spec = ToolSpec(
        name="example",
        version="1.2.0",
        description="Return example text.",
        input_model=ExampleArguments,
        output_types=(TextBlock,),
        effect=EffectClass.READ,
        permission=PermissionClass.SAFE_READ,
        default_timeout=2.0,
        idempotent=True,
        parallel_safe=True,
    )

    def prepare(
        self,
        arguments: ExampleArguments,
        context: ToolPrepareContext,
    ) -> ToolPreparation:
        return ToolPreparation(
            normalized_arguments=arguments.model_dump(mode="json"),
            resolved_targets=(context.workspace.resolve("."),),
        )

    def execute(
        self,
        prepared: PreparedTool,
        context: ToolExecutionContext,
    ) -> ToolResult:
        return ToolResult.success(TextBlock(str(prepared.normalized_arguments["text"])))


class ToolContractTests(unittest.TestCase):
    def test_pydantic_arguments_export_strict_json_schema(self) -> None:
        schema = ExampleTool.spec.input_schema

        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["additionalProperties"], False)
        self.assertEqual(schema["required"], ["text"])
        self.assertEqual(schema["properties"]["count"]["default"], 1)

    def test_model_schema_contains_function_contract(self) -> None:
        schema = ExampleTool.spec.to_model_schema()

        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "example")
        self.assertEqual(
            schema["function"]["parameters"],
            ExampleTool.spec.input_schema,
        )

    def test_catalog_preserves_registration_order_and_hides_internal_tools(self) -> None:
        visible = ExampleTool()
        hidden = ExampleTool()
        hidden.spec = ToolSpec(
            **{
                **hidden.spec.__dict__,
                "name": "internal_example",
                "model_visible": False,
            }
        )
        catalog = ToolCatalog([visible, hidden])

        self.assertEqual([spec.name for spec in catalog.specs()], ["example", "internal_example"])
        self.assertEqual(
            [item.name for item in catalog.model_schemas()],
            ["example"],
        )
        self.assertEqual(
            [item["function"]["name"] for item in catalog.model_schema_payloads()],
            ["example"],
        )

    def test_catalog_rejects_duplicate_name(self) -> None:
        catalog = ToolCatalog([ExampleTool()])

        with self.assertRaises(DuplicateToolError):
            catalog.register(ExampleTool())

    def test_structured_result_serializes_content_and_error(self) -> None:
        success = ToolResult.success(TextBlock("hello"))
        failure = ToolResult.error(
            ErrorKind.TOOL_EXECUTION_ERROR,
            "failed",
            retryable=True,
        )

        self.assertEqual(
            json.loads(success.to_model_text())["content"][0],
            {"kind": "text", "text": "hello"},
        )
        self.assertEqual(
            failure.to_model_payload()["error"],
            {
                "kind": "tool_execution_error",
                "message": "failed",
                "retryable": True,
            },
        )

    def test_tool_spec_exposes_required_policy_fields(self) -> None:
        spec = ExampleTool.spec

        self.assertEqual(spec.effect, EffectClass.READ)
        self.assertEqual(spec.permission, PermissionClass.SAFE_READ)
        self.assertEqual(spec.default_timeout, 2.0)
        self.assertTrue(spec.idempotent)
        self.assertTrue(spec.parallel_safe)


if __name__ == "__main__":
    unittest.main()
