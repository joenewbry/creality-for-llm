from itertools import product
from pathlib import Path
import shutil
import subprocess
import pytest
from printer_control.modal import ModalTracker


def test_mode_restore_order_preserves_extrusion_override():
    tracker = ModalTracker()
    for command in ['G21', 'G90', 'M83']:
        tracker.observe(command)
    assert tracker.mode_restore_commands() == ['G21', 'G90', 'M83']
    tracker.observe('G90')
    assert tracker.e_relative is False
    tracker.observe('G91')
    tracker.observe('M82')
    assert tracker.xyz_relative is True and tracker.e_relative is False


def test_snapshot_requires_known_targets_and_modes_without_inferring_defaults():
    tracker = ModalTracker()
    assert 'unknown_fan_pwm' in tracker.blockers()
    for command in ['G21', 'G90', 'M83', 'T0', 'M106 S128', 'M104 S0', 'M140 S0', 'G1 X10 F1200']:
        tracker.observe(command)
    assert tracker.blockers() == []
    tracker.observe('G20')
    assert tracker.feedrate is None
    assert 'unknown_feedrate' in tracker.blockers()


@pytest.mark.parametrize('command', ['M106 S10 S20', 'G1 Xnan', 'M104 S200 R210', 'G90 X1',
    'M106 P1 S100', 'M104 S-1', 'G1 F0', 'M220 S80.5', 'G1 X1\nM112', 'M84', 'G28'])
def test_uncertainty_latches_and_cannot_be_cleared_by_later_modes(command):
    tracker = ModalTracker()
    tracker.observe(command)
    tracker.observe('G21')
    tracker.observe('G90')
    tracker.observe('M82')
    assert tracker.uncertainty
    with pytest.raises(ValueError):
        tracker.mode_restore_commands()


def test_compact_parameters_and_text_do_not_confuse_modes():
    tracker = ModalTracker()
    tracker.observe('G1X10Y20F1500 ; comment')
    tracker.observe('M117 G91 M83 is display text')
    assert tracker.feedrate == 1500
    assert tracker.xyz_relative is None
    assert not tracker.uncertainty


def test_mode_sequences_match_extracted_pinned_firmware_functions(tmp_path):
    """Compile unchanged function text; dependencies/macros are a native harness.

    This exercises the exact pinned bit operations, not the whole firmware or
    its parser. Command-to-method mappings are from gcode.cpp/M82_M83.cpp.
    """
    source = Path(__file__).resolve().parents[1] / 'firmware/Marlin/src/gcode/gcode.h'
    if not source.exists() or not shutil.which('clang++'):
        pytest.skip('Pinned firmware checkout and clang++ required')
    text = source.read_text()
    start = text.index('  static uint8_t axis_relative;')
    end = text.index('\n  #if ENABLED(CNC_WORKSPACE_PLANES)', start)
    functions = text[start:end]
    cpp = '''#include <cstdint>
#include <iostream>
#define _BV(n) (1U << (n))
#define TEST(v,n) (((v) & _BV(n)) != 0)
#define CBI(v,n) ((v) &= ~_BV(n))
#define SBI(v,n) ((v) |= _BV(n))
enum AxisEnum { X_AXIS, Y_AXIS, Z_AXIS, E_AXIS };
enum AxisRelative : uint8_t { REL_X, REL_Y, REL_Z, REL_E, E_MODE_ABS, E_MODE_REL };
class GcodeSuite { public:
''' + functions + '''
};
uint8_t GcodeSuite::axis_relative = 0;
int main(int argc, char** argv) {
 for(int i=1;i<argc;i++) {
  GcodeSuite::axis_relative=0;
  for(char* c=argv[i];*c;c++) {
   switch(*c) {
    case '0': GcodeSuite::set_relative_mode(false); break;
    case '1': GcodeSuite::set_relative_mode(true); break;
    case '2': GcodeSuite::set_e_absolute(); break;
    case '3': GcodeSuite::set_e_relative(); break;
   }
  }
  std::cout << GcodeSuite::axis_is_relative(X_AXIS) << GcodeSuite::axis_is_relative(E_AXIS) << "\\n";
 }
}
'''
    path = tmp_path / 'modes.cpp'
    path.write_text(cpp)
    binary = tmp_path / 'modes'
    subprocess.run(['clang++', '-std=c++11', str(path), '-o', str(binary)], check=True, capture_output=True)
    sequences = [''.join(s) for s in product('0123', repeat=4)]
    actual = subprocess.check_output([str(binary), *sequences], text=True).splitlines()
    commands = ['G90', 'G91', 'M82', 'M83']
    for sequence, firmware in zip(sequences, actual):
        tracker = ModalTracker()
        tracker.observe('G90')  # same explicit initial condition as native fixture
        for code in sequence:
            tracker.observe(commands[int(code)])
        assert firmware == f'{int(tracker.xyz_relative)}{int(tracker.e_relative)}', sequence


@pytest.mark.parametrize('command', ['M220 S0', 'M220 S301', 'M221 S99.5', 'M221 T1 S100', 'M220', 'M221 S100 B1'])
def test_override_parser_refuses_unsupported_forms(command):
    tracker = ModalTracker()
    tracker.observe(command)
    assert tracker.uncertainty


def test_overrides_are_explicit_commanded_state_and_not_cold_job_support():
    from printer_control.preflight import cold_virtual_job
    from printer_control.journal import RequestRejected
    tracker = ModalTracker()
    assert tracker.snapshot()['feedrate_percent'] is None
    assert tracker.snapshot()['flow_percent'] is None
    tracker.observe('M220 S95')
    tracker.observe('M221 T0 S105')
    assert tracker.snapshot()['feedrate_percent'] == 95
    assert tracker.snapshot()['flow_percent'] == 105
    assert not tracker.uncertainty
    for command in (b'M220 S95', b'M221 T0 S105'):
        with pytest.raises(RequestRejected, match='cold_profile_override_unsupported'):
            cold_virtual_job(command)
