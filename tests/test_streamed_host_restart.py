"""SIGKILL the actual sender process; restart its journal with a fresh simulator.

The original virtual device dies with its host. This tests durable receipts and
fresh-connection refusal, not continuity/reconciliation of a surviving printer.
Physical serial opens are denied by the virtual launcher in both processes.
"""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Console:
    def __init__(self, journal, logs):
        self.log = logs.open('w+')
        self.child = subprocess.Popen([sys.executable, str(ROOT/'tests/octoprint_virtual_smoke.py'),
            '--console', '--service', '--model-fixture', '--journal', str(journal)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True,
            cwd=ROOT, env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')})
        self.next_id = 0

    def call(self, tool, arguments):
        self.next_id += 1
        self.child.stdin.write(json.dumps(dict(id=self.next_id,tool=tool,arguments=arguments))+'\n')
        self.child.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=20), 'virtual console response deadline'
        line = self.child.stdout.readline()
        assert line, 'virtual console exited before response'
        response = json.loads(line)
        assert response['id'] == self.next_id and 'error' not in response, response
        return response['result']

    def request(self, operation, identifier):
        status = self.call('job.inspect', {})
        arguments = {'job_id':status['job_id']}
        if operation == 'job.stage':arguments['file_sha256'] = status['file_sha256']
        return dict(request_id=identifier, printer_id=status['printer_id'],
            connection_generation=status['connection_generation'], expected_state_version=status['state_version'],
            deadline=time.time()+30, arguments=arguments)

    def completed(self, operation, identifier):
        request = self.request(operation,identifier)
        receipt = self.call(operation,request)
        deadline = time.monotonic()+15
        while receipt['status'] == 'running' and time.monotonic()<deadline:
            time.sleep(.01)
            receipt=self.call('operation.get',{'operation_id':receipt['operation_id']})
        assert receipt['status']=='completed',receipt
        return request,receipt

    def kill(self):
        self.child.kill()
        assert self.child.wait(timeout=5)<0

    def close(self):
        if self.child.poll() is None:
            self.child.kill();self.child.wait(timeout=5)
        self.child.stdin.close();self.child.stdout.close();self.log.close()


@pytest.mark.parametrize('phase', ['running','paused','resume_dispatched'])
def test_streamed_host_sigkill_never_replays_or_restores_old_checkpoint(tmp_path, phase):
    journal=tmp_path/'stream.sqlite'
    first=Console(journal,tmp_path/'first.log')
    requests=[]
    try:
        requests.append(('job.stage',*first.completed('job.stage','stage-before-crash')))
        requests.append(('job.start',*first.completed('job.start','start-before-crash')))
        deadline=time.monotonic()+10
        while True:
            status=first.call('job.inspect',{})
            moves=status['simulation_evidence']['completed_moves']
            if moves>=5:break
            assert time.monotonic()<deadline
            time.sleep(.02)
        assert 0<moves<80 and status['simulation_evidence']['physical_serial_open_attempts']==0
        old_generation=status['connection_generation']
        if phase != 'running':
            requests.append(('job.pause',*first.completed('job.pause','pause-before-crash')))
            paused=first.call('job.inspect',{})
            assert paused['job_state']=='paused' and paused['resumable']
            if phase=='resume_dispatched':
                request=first.request('job.resume','resume-before-crash')
                receipt=first.call('job.resume',request)
                assert receipt['status']=='running',receipt
                requests.append(('job.resume',request,receipt))
        first.kill()  # no backend.close or Python cleanup in the child
    finally:
        first.close()

    restarted=Console(journal,tmp_path/'restarted.log')
    try:
        current=restarted.call('job.inspect',{})
        assert current['connection_generation']!=old_generation
        assert not current['resumable']
        assert current['simulation_evidence']['completed_moves']==0
        assert current['simulation_evidence']['physical_serial_open_attempts']==0
        for operation,request,before in requests:
            historical=restarted.call(operation,request)
            if operation=='job.resume':
                assert historical['status']=='outcome_unknown'
                assert historical['result']=={'reason':'host_restarted','requires_reconciliation':True}
            else:
                assert historical==before
            assert restarted.call('operation.get',{'operation_id':historical['operation_id']})==historical
        for operation in ('job.start','job.resume'):
            refused=restarted.call(operation,restarted.request(operation,operation+'-after-crash'))
            assert refused['status']=='rejected',refused
        time.sleep(.1)
        final=restarted.call('job.inspect',{})
        assert final['simulation_evidence']['completed_moves']==0
        assert not final['resumable']
    finally:
        restarted.close()
