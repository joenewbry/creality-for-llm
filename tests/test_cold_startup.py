"""Trusted pre-open qualification admission; no printer factories or devices."""
import copy
import pytest
from printer_control.cold_provider import digest
from printer_control.cold_startup import ColdStartupReadiness
from printer_control.journal import RequestRejected
from printer_control.service import OperationService,StartupState
from test_cold_provider import qualification
from test_service_startup import Backend


def startup_data(request,q,now):
    return dict(request_sha256=digest(request),observed_at=now,expires_at=now+.5,evidence_id='synthetic-preopen-input',
        unit={k:q[k] for k in ('board','processor','bootloader','screen','firmware_sha256')},
        **{k:q[k] for k in ('assignment_sha256','protected_exclusion_sha256','driver_sha256','recovery_sha256')},
        verified={k:True for k in ColdStartupReadiness.FACTS},
        temperatures={k:dict(actual=25.,target=0.) for k in ('T','B')},prior_state_reconciled=False)


def checker():
    q=qualification('a'*64);now=[10.];hook=[None];calls=[]
    def observe(request):
        calls.append(copy.deepcopy(request));data=startup_data(request,q.to_dict(),now[0])
        if hook[0]:hook[0](data,request)
        return data
    return ColdStartupReadiness(q,observe=observe,clock=lambda:now[0]),now,hook,calls


def test_real_service_checks_trusted_inputs_and_commits_intent_before_factory(tmp_path):
    check,now,hook,calls=checker();events=[];backend=Backend()
    def factory(lease,state,admission):
        events.append('factory');assert calls and admission.evidence_id=='synthetic-preopen-input';lease.check()
        return backend
    with OperationService.from_backend_factory(tmp_path/'new.sqlite',factory,readiness_check=check,clock=lambda:now[0]) as service:
        assert service.backend is backend and len(events)==1
    assert backend.closed


@pytest.mark.parametrize('fact',sorted(ColdStartupReadiness.FACTS))
def test_unknown_or_false_physical_qualification_blocks_before_any_factory(tmp_path,fact):
    check,now,hook,calls=checker();hook[0]=lambda data,request:data['verified'].update({fact:False})
    def forbidden(*args):pytest.fail('unqualified input reached backend factory')
    with pytest.raises(RequestRejected):OperationService.from_backend_factory(tmp_path/'new.sqlite',forbidden,readiness_check=check,clock=lambda:now[0])
    assert len(calls)==1


@pytest.mark.parametrize('kind',['unit','assignment','protected','driver','recovery','sensor','target','stale','request','replay','partial','bool_time'])
def test_wrong_stale_or_incomplete_inputs_cannot_be_approved(tmp_path,kind):
    check,now,hook,calls=checker();saved=[]
    def mutate(data,request):
        if kind=='unit':data['unit']['processor']='wrong'
        if kind in ('assignment','protected','driver','recovery'):
            data[{'assignment':'assignment_sha256','protected':'protected_exclusion_sha256','driver':'driver_sha256','recovery':'recovery_sha256'}[kind]]='0'*64
        if kind=='sensor':data['temperatures']['T']['actual']=-15
        if kind=='target':data['temperatures']['B']['target']=50
        if kind=='stale':now[0]+=1.1
        if kind in ('request','replay'):data['request_sha256']='0'*64
        if kind=='partial':data['verified'].pop('sensors_valid')
        if kind=='bool_time':data['observed_at']=True
    hook[0]=mutate
    with pytest.raises(RequestRejected):check(StartupState(False,0,0,False,False,0))


def test_prior_state_reconciliation_is_scoped_and_explicit():
    check,now,hook,calls=checker();state=StartupState(True,1,1,False,False,0)
    with pytest.raises(RequestRejected,match='reconciliation'):check(state)
    hook[0]=lambda data,request:data.update(prior_state_reconciled=True)
    assert check(state).prior_state_reconciled
    assert calls[-1]['startup_state']['unknown_operations']==1


def test_preopen_observation_cannot_be_replayed_or_mutate_retained_binding():
    check,now,hook,calls=checker();state=StartupState(False,0,0,False,False,0);saved=[]
    hook[0]=lambda data,request:saved.append(copy.deepcopy(data))
    assert check(state)
    check.observe=lambda request:saved[0]
    with pytest.raises(RequestRejected):check(state)
