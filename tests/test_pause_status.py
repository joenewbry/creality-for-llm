import json

import pytest

from printer_control.journal import RequestRejected
from printer_control.tools import ToolRouter
from test_tools import router  # strict cold tool fixture
from test_restored_resume import ready
from test_restore_effects import setup


def configure_backend(service):
    service.backend.supported_operations=('job.resume','job.pause','job.cancel')
    service.backend.inspect=lambda:dict(job_state='paused',job_id='j1',resumable=False,
                                        resume_blockers=['restoration_handoff_required'])
    return ToolRouter(service)


def test_pause_read_is_strict_read_only_and_retained_as_polled_history(router):
    value=router.call('job.pause_status',{})
    assert value['phase']=='inactive' and not value['resume_authorized']
    assert value['thermal']['durable_latest'] is None
    assert router.service.backend.commands==[]
    history=router.call('printer.observations',{'after':0,'limit':10})
    assert history['observations'][-1]['source']=='job.pause_status'
    assert history['observations'][-1]['snapshot']==value
    with pytest.raises(RequestRejected,match='unexpected_read_arguments'):
        router.call('job.pause_status',{'resume':True})


def test_restored_status_does_not_consume_or_refresh_thermal_evidence(tmp_path):
    service,restore,request,calls=ready(tmp_path/'journal',hot=True)
    with service:
        router=configure_backend(service)
        before=service._thermal_pause.policy.last_sample
        value=router.call('job.pause_status',{})
        assert value['phase']=='restored'
        assert value['resume_request_available'] and value['resume_gate']=='fresh_recheck_required'
        assert not value['resume_authorized']
        assert value['restoration']['durable_latest']['state']=='completed'
        assert value['thermal']['durable_latest']['effects'][0]['status']=='completed'
        assert service._thermal_pause.policy.last_sample==before and calls==[]
        assert 'G1 F' not in json.dumps(value) and 'commands' not in value
        assert restore.status=='completed'


@pytest.mark.parametrize('state,phase', [('short_pause','holding'),('standby','standby'),
    ('reheating','reheating'),('ready','thermal_ready_restore_required'),
    ('consumed','restoration_required'),('invalid','recovery_required')])
def test_live_phase_is_distinct_from_latest_durable_history(tmp_path,state,phase):
    service,restore,request,calls=ready(tmp_path/'journal',hot=True)
    with service:
        router=configure_backend(service)
        service._restore=None  # isolated phase-view fixture; no dispatch
        service._thermal_pause.policy.state=state
        service._thermal_pause.policy.reason='synthetic_loss' if state=='invalid' else None
        value=router.call('job.pause_status',{})
        assert value['phase']==phase
        assert not value['resume_request_available']
        assert value['restoration']['active'] is False
        assert value['restoration']['durable_latest']['state']=='completed'
        assert calls==[]


def test_restart_reports_recovery_instead_of_turning_old_completion_into_authority(tmp_path):
    path=tmp_path/'journal'
    service,restore,request,calls=ready(path,hot=True)
    service.close()
    restarted,_,_=setup(path)
    with restarted:
        router=configure_backend(restarted)
        value=router.call('job.pause_status',{})
        assert value['phase']=='recovery_required'
        assert 'prior_pause_requires_recovery' in value['blockers']
        assert not value['resume_request_available'] and not value['resume_authorized']
        assert value['restoration']['active'] is False
        assert value['restoration']['durable_latest']['state']=='completed'
        assert value['restoration']['durable_latest']['invalidated']
        assert restarted.backend.dispatched==[]
