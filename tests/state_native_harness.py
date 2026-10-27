"""Build only the hash-pinned maintained-source fixture; not a physical printer."""
import json
from pathlib import Path
import sys
from types import ModuleType
from scripts.prepare_state_fixture import verify,checked


def build_state_native_harness(output):
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((root/'state-fixture-source.json').read_text())
    source=verify(root/'state-firmware',manifest)
    helper=source/'buildroot/tests/llm_prompt_native.py'
    records={row['path']:row for row in manifest['files']}
    def code(path):
        data=checked(path.read_bytes(),records[path.relative_to(source).as_posix()])
        return compile(data,str(path),'exec')  # Do not execute unverified .pyc caches.
    module=ModuleType('llm_prompt_native');module.__file__=str(helper)
    previous=sys.modules.get('llm_prompt_native')
    try:
        sys.modules['llm_prompt_native']=module
        exec(code(helper),module.__dict__)
        script=source/'buildroot/tests/llm_machine_state_native.py'
        state={'__file__':str(script),'__name__':'state_fixture_builder'}
        exec(code(script),state)
        output=Path(output);output.mkdir()
        state['qualify'](output)
    finally:
        if previous is None:sys.modules.pop('llm_prompt_native',None)
        else:sys.modules['llm_prompt_native']=previous
    verify(source,manifest)  # Native execution may not alter pinned input bytes.
    return output/'wire/state-actions'
