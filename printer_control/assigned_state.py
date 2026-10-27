"""Correlate controller variables with an owned, journaled inspect request.

No device access, job action or physical readiness authority is supplied here.
This is an evidence input for the still-unfinished physical job provider.
"""
import copy
import json
import math
import os

from .assigned_observations import AssignedMachineEventRecorder
from .journal import RequestRejected


class AssignedStateReader:
    def __init__(self, recorder):
        if type(recorder) is not AssignedMachineEventRecorder:
            raise TypeError('source-bound assigned capture required')
        self.recorder = recorder
        self._pid = os.getpid()

    def read(self, operation_id, *, max_age=1.):
        recorder = self.recorder
        if os.getpid() != self._pid:
            raise RequestRejected('state_reader_belongs_to_another_process')
        if type(operation_id) is not str or not 1 <= len(operation_id) <= 128:
            raise RequestRejected('bounded_inspect_operation_required')
        if type(max_age) not in (int,float) or not math.isfinite(max_age) or not 0 < max_age <= 1:
            raise RequestRejected('bounded_state_report_age_required')
        recorder.lease.check()
        # The durable request and result are read together. An arbitrary caller
        # dictionary or successful G-code ok cannot establish this correlation.
        with recorder.journal._db() as db:
            row = db.execute('SELECT request,status,result FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
            started = db.execute("SELECT MIN(observed_at) FROM events WHERE operation_id=? AND status='running'", (operation_id,)).fetchone()[0]
        if row is None or row['status'] != 'completed' or started is None:
            raise RequestRejected('completed_journaled_inspect_required')
        request, result = json.loads(row['request']), json.loads(row['result'])
        channel = recorder.endpoint.backend.channel
        if (request.get('operation') != 'prompt.inspect' or request.get('arguments') != {}
                or request.get('printer_id') != recorder._printer_id
                or request.get('connection_generation') != recorder._connection_generation
                or result.get('scope') != 'firmware_prompt_protocol'):
            raise RequestRejected('state_inspect_identity_or_scope_mismatch')
        wire, receipt = result.get('wire_request'), result.get('receipt')
        if (type(wire) is not int or not 1 <= wire <= 0xffffffff or type(receipt) is not dict
                or receipt.get('request') != wire or receipt.get('result') != 0
                or not receipt.get('session') or receipt.get('session') != channel.session):
            raise RequestRejected('accepted_bound_inspect_receipt_required')

        def check_channel():
            recorder.lease.check()
            accepted, _ = channel.snapshot()
            if (channel.state != 'ready' or not channel.bound
                    or channel.generation != recorder._connection_generation
                    or accepted is None or accepted.request != wire or accepted.session != channel.session):
                raise RequestRejected('inspect_no_longer_current_on_owned_channel')

        check_channel()
        snapshot = recorder.snapshot(max_age=max_age)
        if (not snapshot['source_usable'] or not snapshot['capturing'] or snapshot['fault']
                or snapshot['controller_resets_observed'] or snapshot['dispatch_inhibit']):
            raise RequestRejected('state_capture_requires_reconciliation')
        report = snapshot['reports'].get('machine_state')
        if report is None:
            if recorder.clock() >= request['deadline']:
                raise RequestRejected('state_report_unavailable_for_inspect')
            raise RequestRejected('correlated_state_report_pending')
        data, arrived = report['data'], report['observed_at']
        provenance = data.get('receive_provenance', {})
        if (data.get('request') != wire or data.get('session') != channel.session
                or report['generation'] != recorder._connection_generation
                or provenance.get('capture_id') != recorder.capture_id
                or provenance.get('printer_id') != recorder._printer_id
                or provenance.get('connection_generation') != recorder._connection_generation):
            raise RequestRejected('state_report_correlation_mismatch')
        now = recorder.clock()
        if (not report['arrival_fresh'] or type(now) not in (int,float) or not math.isfinite(now)
                or not started <= arrived < request['deadline'] or not 0 <= now-arrived <= max_age):
            raise RequestRejected('state_report_stale_or_outside_request')
        # A second source/channel check catches loss during the durable read.
        check_channel()
        if not recorder._source_usable() or not recorder.capturing:
            raise RequestRejected('state_source_lost_during_read')
        now = recorder.clock()  # Metadata/lease checks may have waited.
        if type(now) not in (int,float) or not math.isfinite(now) or not 0 <= now-arrived <= max_age:
            raise RequestRejected('state_report_expired_during_read')
        return dict(operation_id=operation_id, printer_id=recorder._printer_id,
            connection_generation=recorder._connection_generation, capture_id=recorder.capture_id,
            observed_at=arrived, arrival_age_seconds=now-arrived,
            state=copy.deepcopy(data), evidence_type='correlated_controller_variables',
            physical_readiness_qualified=False, resume_authorized=False,
            planner_completion_proven=False)
