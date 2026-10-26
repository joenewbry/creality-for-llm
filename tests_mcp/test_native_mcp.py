"""Official MCP client against the actual native-console stdio process."""
import asyncio
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def test_official_client_discovers_and_executes_native_prompt_tools(tmp_path):
    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'tests/native_prompt_console.py'), '--mcp', '--journal', str(tmp_path / 'mcp.sqlite')], cwd=str(ROOT))
        async with stdio_client(params) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing, read_timeout_seconds=15.) as client:
                initialized = await client.initialize()
                assert initialized.capabilities.tools is not None
                tools = await client.list_tools()
                assert {tool.name for tool in tools.tools} == {'prompt.inspect', 'prompt.bind', 'prompt.respond',
                    'printer.describe', 'printer.status', 'operation.get', 'printer.events', 'printer.observations'}
                description = await client.call_tool('printer.describe', {})
                assert not description.is_error
                assert description.structured_content['physical_printer'] is False
                for operation, arguments in [('prompt.inspect', {}), ('prompt.bind', {}),
                                             ('prompt.respond', {'prompt': 1, 'choice': 0})]:
                    state = (await client.call_tool('printer.status', {})).structured_content
                    request = dict(request_id=operation, printer_id=state['printer_id'],
                        connection_generation=state['connection_generation'], expected_state_version=state['state_version'],
                        deadline=time.time()+10, arguments=arguments)
                    reply = await client.call_tool(operation, request)
                    assert not reply.is_error
                    result = reply.structured_content
                    end = time.monotonic()+5
                    while result['status'] == 'running' and time.monotonic() < end:
                        await asyncio.sleep(.005)
                        result = (await client.call_tool('operation.get', {'operation_id': result['operation_id']})).structured_content
                    assert result['status'] == 'completed'
                    duplicate = (await client.call_tool(operation, request)).structured_content
                    assert duplicate == result
                assert result['result']['resolution'] == 'firmware_user_wait_cleared'
                assert result['result']['physical_recovery_qualified'] is False
                rejected = await client.call_tool('printer.raw', {'command': 'M108'})
                assert rejected.is_error and rejected.structured_content['error']['code'] == 'invalid_request'
                invalid = await client.call_tool('prompt.respond', {**request, 'request_id': 'invalid-choice',
                    'arguments': {'prompt': 1, 'choice': 1}})
                assert invalid.is_error
                events = (await client.call_tool('printer.events', {'after': 0, 'limit': 100})).structured_content
                assert [event['status'] for event in events['events']] == ['accepted', 'running', 'completed'] * 3
    asyncio.run(scenario())
