"""Preflight for the initial virtual-only, cold test profile."""
from .journal import RequestRejected
from .modal import ModalTracker


def cold_virtual_job(data):
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as exc:
        raise RequestRejected('job_must_be_ascii_gcode') from exc
    tracker = ModalTracker()
    for number, line in enumerate(text.splitlines(), 1):
        tracker.observe(line)
        if tracker.uncertainty:
            raise RequestRejected(f'unqualified_command_at_line_{number}')
        if tracker.feedrate_percent is not None or tracker.flow_percent is not None:
            raise RequestRejected(f'cold_profile_override_unsupported_at_line_{number}')
        if tracker.nozzle_target not in (None, 0) or tracker.bed_target not in (None, 0) or tracker.fan_pwm not in (None, 0):
            raise RequestRejected(f'cold_profile_violation_at_line_{number}')
        if tracker.units == 'inch' or (tracker.xyz_relative is not None and tracker.e_relative != tracker.xyz_relative):
            raise RequestRejected(f'virtual_model_limit_at_line_{number}')
    if tracker.blockers():
        raise RequestRejected('incomplete_job_initialization')
