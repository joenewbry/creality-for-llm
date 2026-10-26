from printer_control.protocol import ProtocolObserver


def feed(observer, text):
    return observer.feed(text.encode("ascii"), 10.0)


def test_fragmented_temperature_ack_keeps_sensor_fault():
    p = ProtocolObserver("g")
    assert feed(p, "ok T:-15.") == []
    result = feed(p, "00 /0.00 B:17.34 /0.00 @:0\r\n")
    assert [e.kind for e in result] == ["ack", "temperature", "sensor_suspect"]
    assert result[1].data["values"]["T"] == {"actual": -15.0, "target": 0.0}
    assert result[0].data["meaning"] == "protocol_only"


def test_prompt_only_visible_after_show_and_never_guesses_safety():
    p = ProtocolObserver("g")
    before = feed(p, "//action:prompt_begin Paused\n//action:prompt_button PurgeMore\n//action:prompt_button DisableRunout\n")
    assert [e.kind for e in before] == ["prompt_assembling"]
    event, = feed(p, "//action:prompt_show\n")
    assert event.kind == "prompt_open"
    assert event.data["buttons"] == ["PurgeMore", "DisableRunout"]
    assert event.data["remote_response_safe"] is False
    assert event.data["firmware_token"] is None


def test_identical_replacement_prompt_has_new_id_and_invalidates_old():
    p = ProtocolObserver("g")
    transcript = "//action:prompt_begin M0 Stop\n//action:prompt_button Continue\n//action:prompt_show\n"
    first = feed(p, transcript)[-1]
    replacement = feed(p, transcript)
    assert replacement[0].kind == "prompt_invalidated"
    assert replacement[-1].data["prompt_id"] != first.data["prompt_id"]


def test_reset_invalidates_capabilities_and_prompts():
    p = ProtocolObserver("g")
    feed(p, "Cap:PROMPT_SUPPORT:1\n//action:prompt_begin Wait\n")
    events = feed(p, "start\n")
    assert [e.kind for e in events] == ["prompt_invalidated", "connection_invalidated"]
    assert events[-1].data["physical_state"] == "unknown"
    assert not p.capabilities
    assert p.generation != "g"


def test_overlong_and_invalid_lines_do_not_become_prompts_or_acks():
    p = ProtocolObserver("g")
    events = p.feed(b"x" * 100000 + b"ok\n" + b"\xffok\n", 10)
    assert [e.kind for e in events] == ["protocol_error", "protocol_error"]
    assert len(p._buffer) == 0
    assert feed(p, "ok\n")[0].kind == "ack"


def test_checksum_and_thermal_errors_have_different_dispositions():
    p = ProtocolObserver("g")
    result = feed(p, "Error:checksum mismatch, Last Line: 1\nResend: 2\nError:THERMAL RUNAWAY\n")
    assert [e.kind for e in result] == ["transport_error", "resend_requested", "fault"]
    assert result[1].data["line_number"] == 2


def test_extra_buttons_after_show_invalidate_prompt():
    p = ProtocolObserver("g")
    feed(p, "//action:prompt_begin X\n//action:prompt_show\n")
    result = feed(p, "//action:prompt_button Continue\n")
    assert [e.kind for e in result] == ["protocol_error", "prompt_invalidated"]


def test_unknown_action_is_observation_not_permission():
    p = ProtocolObserver("g")
    event, = feed(p, "//action:resume\n")
    assert event.kind == "host_action" and event.data["verified"] is False



def test_controller_position_is_a_report_without_homing_or_planner_authority():
    event,=feed(ProtocolObserver('g'),'X:1.25 Y:-2 Z:.5 E:+4 Count X:100 Y:-160 Z:200\n')
    assert event.kind=='reported_position'
    assert event.data==dict(position=dict(x=1.25,y=-2.,z=.5,e=4.),units=None,
        position_kind='controller_report',independent_measurement=False,homing_state=None,planner_completion=None)


def test_huge_numbers_are_protocol_errors_instead_of_nonfinite_json():
    for line,reason in [('T:'+('9'*400)+' /0\n','nonfinite_temperature'),
                        ('X:'+('9'*400)+' Y:0 Z:0 E:0\n','nonfinite_reported_position')]:
        event,=feed(ProtocolObserver('g'),line)
        assert event.kind=='protocol_error' and event.data['reason']==reason


def test_position_can_share_a_line_with_protocol_ack():
    events=feed(ProtocolObserver('g'),'ok X:1 Y:2 Z:3 E:4 Count: A:100 B:200 C:300\n')
    assert [event.kind for event in events]==['ack','reported_position']
    assert events[1].data['position']['x']==1.
