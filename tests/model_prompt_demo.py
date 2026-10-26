#!/usr/bin/env python3
"""Paid, explicitly invoked software-only model demonstrations. Never run by pytest.

Requires OPENAI_API_KEY. No physical transport or arbitrary server option.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'gpt-4.1-mini-2025-04-14'
MAX_ROUNDS = 20
MAX_OUTPUT = 1024
MAX_BODY_BYTES = 120000
MAX_RESERVED_USD = 1.0
# Verified official standard rates, October 25, 2026. Cached-input discounts
# are deliberately ignored. Reverify before changing the pinned model/rates.
INPUT_USD_PER_MILLION = .40
OUTPUT_USD_PER_MILLION = 1.60
INSTRUCTIONS = '''You control only a SOFTWARE native firmware test fixture. Resolve its current generic user wait using the provided tools and verify the operation result. You choose the tool sequence. Read capability and status evidence; never invent identity, generation, state version, prompt tokens or completion. Fresh protocol inspection is required before binding/responding; bind once per native boot. Each operation needs a unique request_id, current printer identity/connection/state version and a future epoch-time deadline (current time plus 60 seconds is suitable). Reuse an identical request only when checking an uncertain duplicate; do not mutate its content. Poll running receipts with operation_get. Status reads send no printer commands. Respect refusals and unsupported actions. Do not claim physical printing or hardware recovery. When finished, summarize only verified results. No actual printer is connected.'''


def function_tools(mcp_tools):
    mapping, result = {}, []
    for tool in mcp_tools:
        name = tool.name.replace('.', '_')
        if name in mapping:
            raise ValueError('tool name collision')
        mapping[name] = tool.name
        result.append({'type': 'function', 'name': name, 'description': tool.description,
                       'parameters': tool.input_schema, 'strict': True})
    return mapping, result


def reservation(payload):
    size = len(json.dumps(payload, ensure_ascii=True).encode())
    if size > MAX_BODY_BYTES:
        raise ValueError('request byte budget exceeded')
    # ASCII byte length conservatively bounds text tokens; add protocol framing
    # allowance. Output is API-capped. This is a local ceiling, not a bill.
    return ((size + 8192) * INPUT_USD_PER_MILLION + MAX_OUTPUT * OUTPUT_USD_PER_MILLION) / 1e6


def strict_arguments(text):
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ValueError('duplicate argument key')
            output[key] = value
        return output
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite argument')))


async def demo(evidence, scenario="prompt"):
    if scenario not in ("prompt", "job", "combined", "hot"):
        raise ValueError("unknown scenario")
    if not os.environ.get('OPENAI_API_KEY'):
        raise RuntimeError('OPENAI_API_KEY is required; no request was made')
    if evidence.exists():
        raise RuntimeError('Use a new evidence path; existing runs are never overwritten')
    evidence.parent.mkdir(parents=True, exist_ok=True)
    record = {'scenario': scenario, 'model': MODEL, 'physical_printer': False, 'status': 'running', 'rounds': [],
              'reserved_cost_upper_estimate_usd': 0., 'reported_usage_cost_estimate_usd': 0.,
              'originating_thread': os.environ.get('CODEX_THREAD_ID'),
              'originating_session_title': None}
    def save():
        evidence.write_text(json.dumps(record, indent=2) + '\n')
    save()
    try:
        with tempfile.TemporaryDirectory(prefix='model-printer-demo-') as directory:
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / ('tests/native_prompt_console.py' if scenario == 'prompt' else 'tests/octoprint_virtual_smoke.py')), '--mcp', '--journal', str(Path(directory)/'operations.sqlite')] + (['--model-fixture'] if scenario != 'prompt' else []) + (['--scenario', 'unified_prompt'] if scenario == 'combined' else (['--scenario','hot_console'] if scenario=='hot' else [])),
                cwd=str(ROOT))
            async with stdio_client(params) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing, read_timeout_seconds=15.) as client:
                    await client.initialize()
                    mapping, tools = function_tools((await client.list_tools()).tools)
                    from model_job_scenario import JOB_INSTRUCTIONS, COMBINED_INSTRUCTIONS, evaluate_job, evaluate_combined
                    from model_hot_scenario import HOT_INSTRUCTIONS,evaluate_hot
                    observed = []
                    if scenario != 'prompt':
                        record['job_observations'] = observed
                    instructions = HOT_INSTRUCTIONS if scenario=='hot' else (INSTRUCTIONS if scenario == 'prompt' else (COMBINED_INSTRUCTIONS if scenario == 'combined' else JOB_INSTRUCTIONS))
                    messages = [{'role': 'user', 'content': 'Resolve the current native test-fixture user wait and verify the result.' if scenario == 'prompt' else 'Stage and start the provided virtual job, pause it mid-stream, verify it stays paused, resume it and verify completion.'}]
                    async with AsyncOpenAI(base_url='https://api.openai.com/v1', max_retries=0, timeout=30.) as model:
                        for index in range(MAX_ROUNDS if scenario == 'prompt' else (50 if scenario == 'combined' else 40)):
                            payload = {'model': MODEL, 'input': messages, 'tools': tools,
                                'instructions': instructions + f'\nCurrent epoch time: {time.time():.3f}',
                                'max_output_tokens': MAX_OUTPUT, 'parallel_tool_calls': False, 'store': False, 'service_tier': 'default'}
                            reserve = reservation(payload)
                            if record['reserved_cost_upper_estimate_usd'] + reserve > MAX_RESERVED_USD:
                                raise RuntimeError('local model cost ceiling reached')
                            record['reserved_cost_upper_estimate_usd'] += reserve
                            row = {'index': index, 'status': 'request_started', 'tool_calls': []}
                            record['rounds'].append(row)
                            save() # account for uncertain/billed requests before invocation
                            response = await model.responses.create(**payload)
                            usage = response.usage
                            row['usage'] = usage.model_dump() if usage else None
                            if usage:
                                record['reported_usage_cost_estimate_usd'] += (usage.input_tokens * INPUT_USD_PER_MILLION + usage.output_tokens * OUTPUT_USD_PER_MILLION)/1e6
                            row['status'] = response.status
                            row['text'] = response.output_text
                            output = [item.model_dump(exclude_none=True) for item in response.output]
                            messages.extend(output)
                            calls = [item for item in response.output if item.type == 'function_call']
                            for call in calls:
                                try:
                                    if call.name not in mapping:
                                        raise ValueError('unknown tool')
                                    arguments = strict_arguments(call.arguments)
                                    result = await client.call_tool(mapping[call.name], arguments)
                                    body = result.structured_content
                                    if body is None:
                                        body = {'error': 'No structured tool result', 'is_error': result.is_error}
                                except (ValueError, TypeError) as exc:
                                    arguments = None
                                    body = {'error': str(exc)[:256]}
                                if scenario != 'prompt':
                                    observation = (await client.call_tool('job.inspect', {})).structured_content
                                    observed.append({'at': time.monotonic(), 'status': observation})
                                row['tool_calls'].append({'name': call.name, 'arguments': arguments, 'result': body})
                                messages.append({'type': 'function_call_output', 'call_id': call.call_id,
                                                 'output': json.dumps(body, allow_nan=False)})
                            save()
                            if not calls:
                                if scenario != 'prompt':
                                    observation = (await client.call_tool('job.inspect', {})).structured_content
                                    if observation.get('job_state') in ('running', 'completing'):
                                        # A conversational turn ending does not finish the job.
                                        # Supply actual read-only feedback, without scripting
                                        # any mutation or extending the shared cost/round caps.
                                        row['continuation_observation'] = observation
                                        messages.append({'role': 'user', 'content':
                                            'The objective remains incomplete. Continue observing until completion. Current independent observation: ' + json.dumps(observation)})
                                        save()
                                        await asyncio.sleep(1)
                                        continue
                                break
                    # Independent read-only evaluation; no scripted mutation.
                    status = (await client.call_tool('printer.status', {})).structured_content
                    events = (await client.call_tool('printer.events', {'after': 0, 'limit': 100})).structured_content
                    record['final_status'], record['operation_events'] = status, events
                    completed = [event for event in events['events'] if event['status'] == 'completed'
                                 and event['detail'].get('resolution') == 'firmware_user_wait_cleared']
                    snapshot = status.get('prompt_snapshot') or {}
                    passed = len(completed) == 1 and snapshot.get('active') is False and snapshot.get('waiting') is False
                    if scenario != 'prompt':
                        record['job_observations'] = observed
                        if scenario=='hot':
                            record['final_pause_status']=(await client.call_tool('job.pause_status',{})).structured_content
                            passed=evaluate_hot(status,events,observed,record['final_pause_status'])
                        else:passed = (evaluate_combined if scenario == 'combined' else evaluate_job)(status, events, observed)
                    record['status'] = 'passed' if passed else 'incomplete'
                    save()
                    if not passed:
                        raise RuntimeError('Model did not meet the selected software objective')
    except Exception as exc:
        if record['status'] == 'running': record['status'] = 'error'
        # Do not persist request headers, credentials or raw API error bodies.
        record['error_type'] = type(exc).__name__
        save()
        raise
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=['prompt', 'job', 'combined', 'hot'], default='prompt')
    parser.add_argument('--evidence', type=Path, required=True)
    options = parser.parse_args()
    try:
        result = asyncio.run(demo(options.evidence, options.scenario))
    except Exception as exc:
        print(f'Demonstration stopped: {type(exc).__name__}; inspect the bounded evidence record.', file=sys.stderr)
        sys.exit(1)
    print(json.dumps({key: result[key] for key in ('status', 'model', 'reported_usage_cost_estimate_usd', 'reserved_cost_upper_estimate_usd')}))
