"""Opt-in, one-operation resume preparation; no raw LLM command interface."""
import threading

from .checkpoint import PauseCheckpoint
from .journal import RequestRejected
from .pause_restore import InPlaceRestorePlan
from .restore_effects import DurableRestore


class ResumeWorkflow:
    def __init__(self, service, operation_id, deadline):
        self.service,self.operation_id,self.deadline=service,operation_id,deadline
        self.thermal,self.monitor=service._thermal_pause,service._thermal_monitor
        if self.thermal is None or self.monitor is None or self.monitor.closed:
            raise RequestRejected('active_pause_monitor_required')
        for name in ('restoration_checkpoint','send_restore_plan','restore_marker_time'):
            if not callable(getattr(service.backend,name,None)):
                raise RequestRejected('managed_restoration_adapter_required')
        self.stop=threading.Event()
        self.done=threading.Event()
        self.thread=None
        self.error=None
        self.retired=False
        self.phase='accepted'

    def start(self):
        if self.thread is not None:
            raise RequestRejected('resume_workflow_already_started')
        self.thermal.bind_resume_operation(self.operation_id)
        self.thread=threading.Thread(target=self._run,name='printer-resume-workflow',daemon=True)
        self.thread.start()

    def _check(self):
        if self.stop.is_set():
            raise RequestRejected('resume_workflow_interrupted')
        if self.service.clock()>=self.deadline:
            raise RequestRejected('resume_workflow_deadline_expired')
        if self.thermal.policy.state=='invalid' or self.thermal.receipt_failure:
            raise RequestRejected('resume_workflow_thermal_invalid')

    def _run(self):
        service=self.service
        try:
            self.monitor.request_resume()
            self.phase='reheating'
            while not self.stop.wait(.01):
                with service.backend.guard():
                    self._check()
                    if self.thermal.policy.state=='ready':break
            self._check()
            # Never hold the backend guard while taking the monitor cycle lock
            # or joining its worker: its observations need that same guard.
            self.monitor.consume_resume()
            self.monitor.close()
            with service.backend.guard():
                self._check()
                saved=PauseCheckpoint(self.thermal.policy.saved)
                plan=InPlaceRestorePlan(saved,service.backend.restoration_checkpoint(),
                    now=service.clock(),max_observation_age=1)
                epoch=service.backend.witness.snapshot()[2]
                restore=DurableRestore(service,plan,service.backend.send_restore_plan,
                    expected_epoch=epoch,owning_operation=self.operation_id)
                self.restore=restore
                self.phase='restoring'
                restore.dispatch(now=service.clock())
            while not self.stop.wait(.01):
                with service.backend.guard():
                    self._check()
                    barrier=service.backend.restore_marker_time(plan)
                    if barrier is None:continue
                    restore.complete(service.backend.restoration_checkpoint(),marker=plan.marker,
                                     barrier_completed_at=barrier,now=service.clock())
                    self._check()
                    self.phase='resuming'
                    restore.dispatch_resume(self.operation_id,{'job_id':saved.to_dict()['job_id']},
                                            deadline=self.deadline)
                    self.phase='dispatched'
                    return
            self._check()
        except Exception:
            self.interrupt('managed_resume_preparation_failed')
        finally:
            try:
                self.monitor.close()
            except Exception:
                self.interrupt('managed_resume_monitor_shutdown_failed')
            self.done.set()

    def interrupt(self, reason):
        if self.retired:return
        self.stop.set()
        with self.service.backend.guard():
            self.error=self.error or reason
            self.phase='invalid'
            self.thermal.interrupt(reason)
            restore=self.service._restore
            if restore is not None:restore.interrupt(reason)

    def close(self):
        if not self.retired:self.interrupt('resume_workflow_closed')
        if self.thread is not None:
            self.thread.join(2)
            if self.thread.is_alive():raise RuntimeError('resume_workflow_shutdown_incomplete')
