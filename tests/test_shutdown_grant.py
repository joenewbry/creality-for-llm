import pytest
from printer_control.stream_witness import StreamWitness


def test_shutdown_grant_survives_pause_revocation_but_never_admits_heat_or_resume():
    witness=StreamWitness();witness.start_job()
    tag,commands=witness.authorize_shutdown('a'*32)
    witness.invalidate_resume()
    assert not witness.arm_resume(witness.snapshot()[2])
    assert witness.hook(None,'queuing','M104 S200',None,'M104',tags={'source:job'})==(None,)
    for phase in ('queuing','queued','sending','sent'):
        for command in commands:
            assert witness.hook(None,phase,command,None,command.split()[0],tags={tag}) is None
    assert witness._shutdown_grant is None and not witness.allow_job_commands
    assert witness.hook(None,'queuing',commands[0],None,'M104',tags={tag})==(None,)


@pytest.mark.parametrize('command',['M104 S200','M140 S60','G1 X10','M118 E1 wrong'])
def test_shutdown_grant_rejects_altered_sequence(command):
    witness=StreamWitness();witness.start_job()
    tag,_=witness.authorize_shutdown('a'*32)
    assert witness.hook(None,'queuing',command,None,command.split()[0],tags={tag})==(None,)
    assert witness._shutdown_grant is None and not witness.allow_job_commands
