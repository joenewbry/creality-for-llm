from types import SimpleNamespace
from printer_control.stream_witness import StreamWitness


def hook(witness, phase, command, tags=()):
    return witness.hook(SimpleNamespace(), phase, command, None, command.split()[0], tags=set(tags))


def test_checkpoint_gate_blocks_external_mutation_and_invalidates_resume():
    witness = StreamWitness()
    witness.start_job()
    hook(witness, 'sent', 'G90')
    epoch = witness.snapshot()[2]
    assert witness.freeze(epoch)
    assert hook(witness, 'queuing', 'G91') == (None,)
    assert witness.snapshot()[0]['xyz_relative'] is False
    assert not witness.arm_resume(epoch)


def test_resume_allows_file_commands_but_keeps_external_commands_blocked():
    witness = StreamWitness()
    witness.start_job()
    epoch = witness.snapshot()[2]
    assert witness.freeze(epoch)
    assert witness.arm_resume(epoch)
    assert hook(witness, 'queuing', 'G1 X20', ['source:file']) is None
    assert hook(witness, 'queuing', 'G1 X20', ['source:job']) is None
    assert hook(witness, 'queuing', 'G1 X99') == (None,)


def test_read_queries_and_emergency_stop_are_not_held_by_checkpoint_gate():
    witness = StreamWitness()
    witness.start_job()
    assert witness.freeze(witness.snapshot()[2])
    for code in ('M105', 'M114', 'M112', 'M410'):
        assert hook(witness, 'queuing', code) is None
    assert hook(witness, 'queuing', 'M108') == (None,)


def test_queued_command_between_snapshot_and_freeze_invalidates_capture():
    witness = StreamWitness()
    witness.start_job()
    epoch = witness.snapshot()[2]
    hook(witness, 'queued', 'M83')
    assert not witness.freeze(epoch)


def test_only_sent_commands_advance_modal_state():
    witness = StreamWitness()
    witness.start_job()
    hook(witness, 'queued', 'M83')
    assert witness.snapshot()[0]['e_relative'] is None
    hook(witness, 'sent', 'M83')
    assert witness.snapshot()[0]['e_relative'] is True


def test_mutation_already_queued_before_freeze_is_blocked_at_send_boundary():
    witness = StreamWitness()
    witness.start_job()
    hook(witness, 'queued', 'G91')
    epoch = witness.snapshot()[2]
    assert witness.freeze(epoch)
    assert hook(witness, 'sending', 'G91') == (None,)
    assert not witness.arm_resume(epoch)


def thermal_fixture():
    witness=StreamWitness()
    witness.start_job()
    epoch=witness.snapshot()[2]
    assert witness.freeze(epoch)
    effect='a'*32
    commands=['M104 S100.0','M140 S60.0','M118 E1 LLM_THERM_'+effect]
    tag=witness.authorize_thermal(effect,commands,epoch)
    return witness,epoch,commands,tag


def test_thermal_grant_is_exact_ordered_and_does_not_preserve_old_checkpoint():
    witness,epoch,commands,tag=thermal_fixture()
    assert not witness.arm_resume(epoch)
    for phase in ('queuing','queued','sending','sent'):
        for command in commands:
            assert hook(witness,phase,command,[tag]) is None
    modes,_,current=witness.snapshot()
    assert modes['nozzle_target']==100 and modes['bed_target']==60
    assert current>epoch and not witness.arm_resume(epoch)
    assert hook(witness,'queuing',commands[0],[tag])==(None,)


def test_thermal_grant_cannot_be_used_for_movement_or_wrong_order():
    for command in ('G1 X10','M104 S200.0','M140 S60.0'):
        witness,epoch,commands,tag=thermal_fixture()
        assert hook(witness,'queuing',command,[tag])==(None,)
        assert hook(witness,'queuing',commands[0],[tag])==(None,)
        assert not witness.arm_resume(epoch)


def test_unrelated_mutation_revokes_even_queued_thermal_commands():
    witness,epoch,commands,tag=thermal_fixture()
    for phase in ('queuing','queued'):
        for command in commands:hook(witness,phase,command,[tag])
    assert hook(witness,'queuing','G91')==(None,)
    assert hook(witness,'sending',commands[0],[tag])==(None,)


def test_emergency_stop_revokes_thermal_grant_without_blocking_stop():
    witness,epoch,commands,tag=thermal_fixture()
    assert hook(witness,'queuing','M112') is None
    assert hook(witness,'queuing',commands[0],[tag])==(None,)


def test_thermal_send_without_queue_or_replayed_phase_is_refused():
    witness,epoch,commands,tag=thermal_fixture()
    assert hook(witness,'sending',commands[0],[tag])==(None,)
    witness,epoch,commands,tag=thermal_fixture()
    assert hook(witness,'queuing',commands[0],[tag]) is None
    assert hook(witness,'queuing',commands[0],[tag])==(None,)


def restore_fixture():
    from printer_control.pause_restore import InPlaceRestorePlan
    from printer_control.checkpoint import PauseCheckpoint
    from test_checkpoint import data
    saved = PauseCheckpoint(data())
    live = data()
    live['created_at'] = 20
    plan = InPlaceRestorePlan(saved, PauseCheckpoint(live), now=20, max_observation_age=1)
    witness = StreamWitness()
    witness.start_job()
    for command in ('G21', 'G90', 'M83', 'T0', 'M106 S128', 'M104 S0', 'M140 S0', 'G1 F1200'):
        hook(witness, 'sent', command)
    epoch = witness.snapshot()[2]
    assert witness.freeze(epoch)
    tag = witness.authorize_restore(plan, epoch, now=20)
    commands = plan.commands + ('M400', 'M118 E1 ' + plan.marker)
    return witness, epoch, commands, tag, plan


def test_restore_grant_tracks_exact_sequence_but_never_automatically_resumes():
    witness, epoch, commands, tag, _ = restore_fixture()
    assert not witness.arm_resume(epoch)
    for phase in ('queuing', 'queued', 'sending', 'sent'):
        for command in commands:
            assert hook(witness, phase, command, [tag]) is None
    modes, blockers, current = witness.snapshot()
    assert current == epoch + 12  # six mutations, each queued and sent
    assert modes['xyz_relative'] is False and modes['e_relative'] is True
    assert modes['feedrate_percent'] == modes['flow_percent'] == 100
    assert modes['fan_pwm'] == 128 and modes['feedrate'] == 1200
    assert not blockers
    assert witness.frozen and not witness.allow_job_commands
    assert not witness.arm_resume(epoch)
    assert hook(witness, 'queuing', commands[0], [tag]) == (None,)


def test_restore_cannot_authorize_job_movement_wrong_commands_or_reordered_phases():
    cases = [('queuing', 'G1 X10', None), ('queuing', 'M104 S200', None),
             ('queuing', 'M82', None), ('sending', 'G90', None),
             ('queuing', 'G90', 'restore-grant:wrong')]
    for phase, command, wrong_tag in cases:
        witness, epoch, commands, tag, _ = restore_fixture()
        assert hook(witness, phase, command, [wrong_tag or tag]) == (None,)
        assert hook(witness, 'queuing', commands[0], [tag]) == (None,)
        assert not witness.arm_resume(epoch)
    witness, _, commands, tag, _ = restore_fixture()
    assert hook(witness, 'queuing', 'G1 X10', ['source:file']) == (None,)
    assert hook(witness, 'queuing', commands[0], [tag]) == (None,)


def test_restore_revoked_before_sending_by_stop_or_unrelated_mutation():
    for code in ('M112', 'M410', 'G91'):
        witness, _, commands, tag, _ = restore_fixture()
        for phase in ('queuing', 'queued'):
            for command in commands:
                assert hook(witness, phase, command, [tag]) is None
        assert hook(witness, 'queuing', code) == ((None,) if code == 'G91' else None)
        assert hook(witness, 'sending', commands[0], [tag]) == (None,)
    witness, _, commands, tag, _ = restore_fixture()
    witness.revoke_thermal()  # existing cancel coordinator also revokes restore
    assert hook(witness, 'queuing', commands[0], [tag]) == (None,)


def test_restore_and_thermal_grants_are_mutually_exclusive():
    import pytest
    witness, epoch, _, _, plan = restore_fixture()
    effect = 'a' * 32
    with pytest.raises(ValueError, match='not_frozen'):
        witness.authorize_thermal(effect, ['M104 S100', 'M140 S60', 'M118 E1 LLM_THERM_' + effect], epoch)
    witness, epoch, _, _ = thermal_fixture()
    with pytest.raises(ValueError, match='not_frozen'):
        witness.authorize_restore(plan, epoch, now=20)


def test_restore_admission_revalidates_deadline_freshness_and_epoch():
    import pytest
    witness, epoch, _, _, plan = restore_fixture()
    witness.revoke_pause()
    with pytest.raises(ValueError, match='not_frozen'):
        witness.authorize_restore(plan, epoch, now=20)
    current = witness.snapshot()[2]
    for now, reason in ((19, 'clock_reversed'), (22, 'stale'), (70, 'expired')):
        with pytest.raises(ValueError, match=reason):
            witness.authorize_restore(plan, current, now=now)
