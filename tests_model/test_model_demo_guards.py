"""Offline guard tests only; never call a model or load credentials."""
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from model_prompt_demo import function_tools, reservation, strict_arguments, MAX_BODY_BYTES


def test_tool_names_map_without_losing_original_names_or_schemas():
    schema = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
    mapping, tools = function_tools([SimpleNamespace(name='prompt.inspect', description='inspect', input_schema=schema)])
    assert mapping == {'prompt_inspect': 'prompt.inspect'}
    assert tools[0]['strict'] is True and tools[0]['parameters'] == schema
    with pytest.raises(ValueError):
        function_tools([SimpleNamespace(name=n, description='', input_schema=schema) for n in ('a.b', 'a_b')])


def test_budget_guard_bounds_request_size_before_invocation():
    assert 0 < reservation({'input': 'hello'}) < 1
    with pytest.raises(ValueError):
        reservation({'input': 'x' * MAX_BODY_BYTES})


@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_model_argument_parser_rejects_ambiguous_values(text):
    with pytest.raises(ValueError): strict_arguments(text)
