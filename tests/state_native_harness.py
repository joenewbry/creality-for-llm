"""Build only the hash-pinned maintained-source fixture; not a physical printer."""
import json
from pathlib import Path
import shutil
import subprocess
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
    # Link the actual getter too, rather than leaving qualify()'s serializer
    # sample substitution in the combined executable. Its hardware stubs live
    # in a separate namespace from the existing prompt harness stubs.
    sampler=output/'native_sampler.cpp'
    sampler.write_text('''#include <cassert>
#include <cstdint>
#include "getter/feature/host_machine_state.h"
namespace StateHardware {
#include "getter/feature/host_machine_state.cpp"
int critical=0;Marlin marlin;Motion motion;Planner planner;Stepper stepper;
Gcode gcode;Parser parser;Thermal thermalManager;Fan fans[1];
}
HostMachineState actual_native_snapshot(bool waiting) {
 StateHardware::marlin.wait_for_user=waiting;
 return StateHardware::host_machine_state_snapshot();
}
''')
    main=output/'wire/state_main.cpp';text=main.read_text()
    start=text.index('HostMachineState host_machine_state_snapshot() {')
    end=text.index('void command(',start)
    text=text[:start]+'''extern HostMachineState actual_native_snapshot(bool);
HostMachineState host_machine_state_snapshot() {
 ++snapshots;return actual_native_snapshot(marlin.wait_for_user);
}
'''+text[end:]
    main.write_text(text)
    compiler=shutil.which('clang++') or shutil.which('g++')
    subprocess.run([compiler,'-std=gnu++14','-Wall','-Wextra','-DINCH_MODE_SUPPORT=0',
        '-DHAS_WORKSPACE_OFFSET=1','-DHAS_HOME_OFFSET=1','-DDISABLE_IDLE_X=1',
        '-DDISABLE_IDLE_Y=1','-DDISABLE_IDLE_Z=0','-DDISABLE_IDLE_E=1',str(main),str(sampler),
        '-o',str(output/'wire/state-actions')],check=True)
    for scenario in ('bound','repeat','saturation','malformed','response'):
        subprocess.run([str(output/'wire/state-actions'),scenario],check=True,
            capture_output=True,text=True,timeout=5)
    # Exercise the real wait flag across a supported response through the
    # linked getter/serializer. No virtual motion fields supply these samples.
    frames=('M876 V1 I Q00000001\n','M876 V1 B'+'0'*31+'1 Q00000002\n',
        'M876 V1 I Q00000003\n','M876 V1 R'+'0'*31+'1 P00000001 S00 Q00000004\n',
        'M876 V1 I Q00000005\n')
    output_text=subprocess.check_output([str(output/'wire/state-actions'),'runtime'],
        input=''.join(frame+'~' for frame in frames),text=True,timeout=5)
    reports=[line for line in output_text.splitlines() if line.startswith('//llmstate:')]
    assert len(reports)==2 and ' Q00000003 ' in reports[0] and ' Q00000005 ' in reports[1]
    assert ' F0f ' in reports[0] and ' F0d ' in reports[1]
    (output/'linked-state-runtime.txt').write_text(output_text)
    verify(source,manifest)  # Native execution may not alter pinned input bytes.
    return output/'wire/state-actions'
