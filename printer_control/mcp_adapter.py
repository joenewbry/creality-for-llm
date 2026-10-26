"""Optional official-SDK MCP stdio adapter for the existing journaled router."""
import json
import anyio
from jsonschema import Draft202012Validator
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server


def make_server(router):
    definitions = {definition['name']: definition for definition in router.definitions()}
    validators = {name: Draft202012Validator(definition['inputSchema']) for name, definition in definitions.items()}

    async def list_tools(context, params):
        return types.ListToolsResult(tools=[types.Tool(**definition) for definition in definitions.values()])

    async def call_tool(context, params):
        try:
            if params.name not in definitions:
                raise ValueError('unknown_tool')
            arguments = params.arguments or {}
            if not validators[params.name].is_valid(arguments):
                raise ValueError('invalid_tool_arguments')
            result = await anyio.to_thread.run_sync(router.call, params.name, arguments)
            return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result, allow_nan=False))],
                                        structuredContent=result)
        except (ValueError, TypeError, RecursionError) as exc:
            result = {'error': {'code': 'invalid_request', 'message': str(exc)[:256]}}
        except Exception:
            result = {'error': {'code': 'internal_error', 'message': 'Operation unavailable; inspect its durable receipt before retrying.'}}
        return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result))],
                                    structuredContent=result, isError=True)

    return Server('creality-llm-software', version='0.1.0',
                  instructions='Experimental software backends only. Inspect capability scope. Submit explicit idempotent requests, poll receipts, and never infer physical recovery from protocol acceptance.',
                  on_list_tools=list_tools, on_call_tool=call_tool,
                  get_tool_input_schema=lambda name: definitions.get(name, {}).get('inputSchema'))


async def serve_mcp(router):
    server = make_server(router)
    async with stdio_server() as (incoming, outgoing):
        await server.run(incoming, outgoing, server.create_initialization_options())
