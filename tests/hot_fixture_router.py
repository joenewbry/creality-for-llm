"""External-client hot simulator profile; all physical proofs remain synthetic."""
import time
from printer_control.tools import ToolRouter
from printer_control.pause_effects import DurablePauseEffects
from printer_control.pause_monitor import ThermalPauseMonitor
from printer_control.thermal_pause import ThermalPauseLimits,ThermalPausePolicy
from printer_control.virtual_thermal import VirtualThermalTransport


class HotFixtureRouter(ToolRouter):
    def __init__(self,service):
        super().__init__(service)
        self.pause_operations=set()
        self.installed=set()

    def call(self,name,arguments):
        result=super().call(name,arguments)
        if name=='job.pause' and 'operation_id' in result:
            self.pause_operations.add(result['operation_id'])
        if result.get('operation_id') in self.pause_operations and result.get('status')=='completed':
            with self.service.backend.guard():
                backend=self.service.backend
                saved=backend.checkpoint
                if (saved is not None and saved.id==result.get('result',{}).get('checkpoint_id')
                        and saved.id not in self.installed):
                    limits=ThermalPauseLimits(short_seconds=.2,max_pause_seconds=120 if getattr(backend,'model_fixture',False) else 20,standby_nozzle=100,
                        max_nozzle=250,max_bed=90,effect_timeout_seconds=2,reheat_timeout_seconds=5,
                        stable_seconds=.1,tolerance_celsius=2,max_observation_gap_seconds=1)
                    backend.thermal_policy=ThermalPausePolicy(saved,limits,started_at=time.monotonic())
                    transport=VirtualThermalTransport(backend,limits,clock=time.monotonic)
                    thermal=DurablePauseEffects(self.service,backend.thermal_policy,transport.send_targets)
                    def sample(at):
                        # Accelerated synthetic temperatures, never a hardware observation.
                        backend.sender._serial.temp[0]=float(backend.sender._serial.targetTemp[0])
                        backend.sender._serial.bedTemp=float(backend.sender._serial.bedTargetTemp)
                        return backend.thermal_observation(at,effect_id=transport.snapshot()['effect_id'])
                    ThermalPauseMonitor(thermal,sample,interval=.02).start()
                    self.installed.add(saved.id)
        return result
