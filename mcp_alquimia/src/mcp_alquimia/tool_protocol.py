"""MCP plumbing shared by every tool: annotations, invocation and argument schemas."""

import subprocess

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, ValidationError

from .response_contract import to_json
from .subprocess_client import EngineProcessError
from .tool_schema import compact_schema, split_docstring

# Safe and harmless. Does not modify any system state (e.g., fetching lists, reading results).
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,  # Without manual confirmation
    openWorldHint=False,  # Confined to the local sandbox.
)
# Modifies system state (e.g., initializing engines, advancing time steps).
MUTATING = ToolAnnotations(
    readOnlyHint=False,  # With manual confirmation
    destructiveHint=False,  # Changes state, but does not destroy data
    idempotentHint=False,
    openWorldHint=False,  # Confined to the local sandbox.
)
# Changes state, but repeating the call has no further effect (e.g., close).
IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False,  # With manual confirmation
    destructiveHint=False,  # Changes state, but does not destroy data
    idempotentHint=True,
    openWorldHint=False,  # Confined to the local sandbox.
)


def describe_validation_error(error: ValidationError) -> str:
    """Summarize each failing field as `location: message` for the client."""
    details = "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}"
        for item in error.errors()
    )
    return f"Invalid {error.title}: {details}"


async def invoke(function, *args) -> str:
    """Run an operation off the MCP event loop; returns its result as compact JSON.

    Raises:
        ToolError: The operation rejected its input or the engine failed; the
            message is returned to the client.
    """
    try:
        # The default cancellation shielding keeps ownership until the native
        # call returns or times out, allowing lifespan cleanup to reap it.
        return to_json(await anyio.to_thread.run_sync(function, *args))
    except ValidationError as error:
        raise ToolError(describe_validation_error(error)) from error
    except (
        EngineProcessError,
        OSError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as error:
        raise ToolError(str(error)) from error


def prepare_tool_schemas(server: MCPServer) -> None:
    """Make every registered tool strict and describe it to the model from its docstring.

    The SDK's argument models silently drop unknown keys, so a misspelled or
    retired argument (e.g. a top-level dt) would run with defaults. A **kwargs
    parameter cannot catch them either: the SDK turns it into a required field.
    The SDK also sends the whole docstring as the description; only its summary
    is sent here, and each Args entry describes its parameter.

    Raises:
        ValueError: A tool's Args entries do not name exactly its parameters.
    """
    for tool in server._tool_manager.list_tools():
        summary, argument_text = split_docstring(tool.description)
        arguments = tool.fn_metadata.arg_model

        # Create a strict subclass of the original arguments model.
        # Copy the existing config but force `extra="forbid"` to reject unknown parameters.
        strict_arguments = type(
            arguments.__name__,
            (arguments,),
            {"model_config": ConfigDict(**arguments.model_config, extra="forbid")},
        )

        # Update the internal validation model.
        # This ensures the Python server throws an error if extra arguments are received.
        tool.fn_metadata.arg_model = strict_arguments

        # Update the external JSON schema exposed to the LLM.
        # This sets `additionalProperties: false` so the LLM knows it cannot hallucinate arguments.
        schema = strict_arguments.model_json_schema(by_alias=True)
        properties = schema.get("properties", {})
        # The docstring aligns with the JSON schema
        if set(argument_text) != set(properties):
            raise ValueError(
                f"Tool {tool.name}: docstring Args {sorted(argument_text)} must name "
                f"exactly its parameters {sorted(properties)}"
            )
        for name, text in argument_text.items():
            properties[name]["description"] = text
        tool.description = summary
        tool.parameters = compact_schema(schema)
