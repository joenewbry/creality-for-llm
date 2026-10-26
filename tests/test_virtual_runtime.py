"""End-to-end sender scenarios through the virtual-only typed service.

The runner denies physical serial opens. No model/API client is invoked.
"""
from pathlib import Path
import subprocess
import os
import sys
import pytest


@pytest.mark.parametrize('scenario', [
    'resume', 'unified_prompt', 'hot_resume_managed', 'hot_resume_twice',
    'hot_resume_managed_cancel', 'hot_resume_managed_shutdown',
    'hot_pause_disconnect', 'active_reset', 'resend_checksum', 'thermal_fault',
])
def test_virtual_sender_runtime(scenario):
    runner = Path(__file__).with_name('octoprint_virtual_smoke.py')
    result = subprocess.run([sys.executable, str(runner), '--service', '--scenario', scenario],
                            capture_output=True, text=True, timeout=45,
                            env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
