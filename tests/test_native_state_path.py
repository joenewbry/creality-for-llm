"""Actual native state wire -> owned PTY -> pinned sender -> journaled tools."""
import json
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.skipif(sys.platform!='linux',reason='Linux owned software terminal')
def test_native_state_report_crosses_the_assigned_sender_and_llm_tools():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'tests/assigned_job_smoke.py','--scenario','native_state'],
        cwd=root,capture_output=True,text=True,timeout=75)
    assert result.returncode==0,result.stderr+result.stdout
    data=json.loads(result.stdout.splitlines()[-1])
    assert data['native_state_read'] and data['native_state_scope']=='stubbed_controller_variables'
    assert data['native_prompt_frames']==4 and data['moves_completed']==80
    assert data['source_changed_after_stage'] and data['source_bound_serial_capture']
    assert not data['physical_provider_implemented']
    assert data['physical_serial_open_or_discovery_attempts']==0
