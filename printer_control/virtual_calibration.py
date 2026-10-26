"""Explicit simulator settings operations; no physical EEPROM or calibration proof."""
import hashlib
import json
import math
from pathlib import Path
import uuid
from .journal import RequestRejected
from .service import Outcome


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class VirtualCalibration:
    OPERATIONS=('calibration.set_temporary','calibration.persist')

    def __init__(self,backend):
        self.backend=backend
        self.eeprom=backend.sender._serial._virtual_eeprom
        if self.eeprom is None:raise RequestRejected('virtual_settings_unavailable')
        self.approved=self.current()
        self.saved=self.disk()
        self.baseline=dict(self.eeprom.get_default_settings()['steps']['params'])
        self.pending={}
        self.markers=set()
        self.uncertain=False

    def current(self):
        return json.loads(canonical(self.eeprom.eeprom))

    def disk(self):
        return json.loads(Path(self.eeprom._eeprom_file_path).read_text())

    def attach(self,service):
        # A new process must not silently approve settings after an ambiguous
        # previous calibration. This profile provides no automatic recovery.
        with service.journal._db() as db:
            rows=db.execute("SELECT request FROM operations WHERE status='outcome_unknown'").fetchall()
        if any(json.loads(row['request'])['operation'] in self.OPERATIONS for row in rows):
            self.uncertain=True
            self.backend.setup.uncertain=True

    def observe(self,line):
        if line.startswith('echo:LLM_CAL_'):self.markers.add(line.strip()[5:])

    def check_integrity(self):
        try:
            valid=self.current()==self.approved and self.disk()==self.saved
        except (OSError,ValueError,TypeError,KeyError):
            valid=False
        if not valid:
            self.uncertain=self.backend.setup.uncertain=True
            raise RequestRejected('calibration_changed_outside_controller')

    def validate(self,operation,arguments):
        if self.uncertain:raise RequestRejected('calibration_requires_recovery')
        self.backend.setup.validate('printer.home',{})  # idle/heaters off/drained, no motion
        self.check_integrity()
        current=self.current()
        expected={'expected_settings_sha256'}
        if operation=='calibration.set_temporary':expected|={'axis','steps_per_mm'}
        if set(arguments)!=expected or arguments['expected_settings_sha256']!=digest(current):
            raise RequestRejected('calibration_snapshot_changed_or_invalid_fields')
        if operation=='calibration.set_temporary':
            axis,value=arguments['axis'],arguments['steps_per_mm']
            if (type(axis) is not str or axis not in ('x','y','z','e') or type(value) not in (int,float)
                    or not math.isfinite(value) or value!=round(value,6)
                    or not .9*self.baseline[axis.upper()]<=value<=1.1*self.baseline[axis.upper()]):
                raise RequestRejected('calibration_adjustment_out_of_profile')
        elif operation!='calibration.persist':raise RequestRejected('unsupported_calibration_operation')

    def dispatch(self,operation_id,operation,arguments,deadline):
        self.validate(operation,arguments)
        expected=self.current()
        if operation=='calibration.set_temporary':
            expected['steps']['params'][arguments['axis'].upper()]=float(arguments['steps_per_mm'])
            command=f"M92 {arguments['axis'].upper()}{arguments['steps_per_mm']:.6f}"
        else:command='M500'
        marker='LLM_CAL_'+uuid.uuid4().hex
        self.pending[operation_id]=dict(operation=operation,expected=expected,marker=marker,deadline=deadline)
        self.backend.setup.position=None
        self.backend.setup.revision+=1
        try:
            for line in (command,'M400','M118 E1 '+marker):
                if not self.backend.sender.sendCommand(line,tags={'source:llm-calibration'}):
                    raise OSError('calibration_command_not_queued')
        except Exception:
            self.uncertain=self.backend.setup.uncertain=True
            raise

    def poll(self,operation_id):
        pending=self.pending[operation_id]
        persisted=pending['operation']=='calibration.persist'
        expected=pending['expected']
        device=self.backend.sender._serial
        with device.buffered.all_tasks_done:drained=device.buffered.unfinished_tasks==0
        if pending['marker'] in self.markers and drained:
            try:
                matches=self.current()==expected and self.disk()==(expected if persisted else self.saved)
            except (OSError,ValueError,TypeError,KeyError):
                matches=False
            if matches:
                self.approved=expected
                if persisted:self.saved=json.loads(canonical(expected))
                self.backend.setup.revision+=1
                return Outcome('completed',dict(evidence_type='virtual_settings_barrier_ram_and_disk',
                    settings_sha256=digest(expected),persisted=persisted,
                    physical_calibration_qualified=False))
        if self.backend.clock()>=pending['deadline']:
            self.uncertain=self.backend.setup.uncertain=True
            return Outcome('outcome_unknown',{'reason':'calibration_state_unconfirmed','requires_recovery':True})
        return None

    def inspect(self):
        context=self.backend.context()
        current,saved=self.current(),self.disk()
        return dict(printer_id=context.printer_id,connection_generation=context.generation,
            state_version=context.state_version,observed_at=context.observed_at,
            scope='software_virtual_settings_only',settings_sha256=digest(current),
            saved_settings_sha256=digest(saved),steps_per_mm=current['steps']['params'],
            saved_steps_per_mm=saved['steps']['params'],temporary_changes=current!=saved,
            differs_from_last_verified_settings=current!=self.approved or saved!=self.saved,
            requires_recovery=self.uncertain,physical_calibration_qualified=False,
            supported_fields=['steps_per_mm'],default_steps_per_mm=dict(self.baseline),
            allowed_steps_per_mm={axis:[.9*value,1.1*value] for axis,value in self.baseline.items()},
            adjustment_fraction_of_default_baseline=[.9,1.1])
