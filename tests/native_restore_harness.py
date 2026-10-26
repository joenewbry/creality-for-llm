"""Native extracted Marlin restoration handlers, with explicit hardware stubs.

The small parser accepts the space-separated numeric dialect emitted by our
planner. It is NOT Marlin's parser, scheduler, interrupt layer or full build.
Feature branches other than single-extruder FDM are disabled for this harness.
"""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def function(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def build_restore_harness(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'firmware/Marlin/src'
    snippets = []
    evidence = []

    def extract(path, signature):
        original = (source / path).read_text()
        result = function(original, signature)
        evidence.append(dict(path=path, signature=signature,
                             file_sha256=hashlib.sha256(original.encode()).hexdigest(),
                             snippet_sha256=hashlib.sha256(result.encode()).hexdigest()))
        return result

    mode_source = (source / 'gcode/gcode.h').read_text()
    modes = mode_source
    modes = modes[modes.index('  static uint8_t axis_relative;'):modes.index('\n  #if ENABLED(CNC_WORKSPACE_PLANES)')]
    evidence.append(dict(path='gcode/gcode.h', signature='axis_relative mode methods',
                         file_sha256=hashlib.sha256(mode_source.encode()).hexdigest(),
                         snippet_sha256=hashlib.sha256(modes.encode()).hexdigest()))
    for path, signature in (
        ('gcode/temp/M106_M107.cpp', 'void GcodeSuite::M106()'),
        ('gcode/config/M220.cpp', 'void GcodeSuite::M220()'),
        ('gcode/config/M221.cpp', 'void GcodeSuite::M221()'),
        ('gcode/gcode.cpp', 'void GcodeSuite::get_destination_from_command()'),
        ('gcode/motion/G0_G1.cpp', 'void GcodeSuite::G0_G1('),
        ('module/temperature.cpp', 'void Temperature::set_fan_speed('),
    ):
        snippets.append(extract(path, signature))
    flow = extract('module/planner.h', 'static inline void set_flow(')
    refresh = extract('module/planner.h', 'FORCE_INLINE static void refresh_e_factor(')
    preamble = r'''
#include <cstdint>
#include <cstdlib>
#include <map>
#include <string>
#include <sstream>
#include <iostream>
#include <iomanip>
#include <algorithm>
#define ENABLED(x) 0
#define BOTH(a,b) 0
#define PIN_EXISTS(x) 0
#define TERN_(c,x)
#define TERN0(c,x) false
#define TERN(c,a,b) a
#define FORCE_INLINE inline
#define _BV(n) (1U << (n))
#define TEST(v,n) (((v) & _BV(n)) != 0)
#define CBI(v,n) ((v) &= ~_BV(n))
#define SBI(v,n) ((v) |= _BV(n))
#define NOMORE(v,limit) (v) = std::min<uint16_t>((v), (limit))
#define SERIAL_ECHOPAIR(...)
#define SERIAL_CHAR(...)
#define SERIAL_EOL(...)
#define SERIAL_ECHO_START(...)
#define LOOP_LINEAR_AXES(i) for (int i=0; i<3; ++i)
#define AXIS_CHAR(i) ("XYZE"[i])
#define LOGICAL_TO_NATIVE(v,i) (v)
#define _ALT_P 0
#define _CNT_P 1
#define FAN_COUNT 1
#define PREHEAT_COUNT 0
#define IS_SCARA 0
using feedRate_t = float;
enum AxisEnum { X_AXIS, Y_AXIS, Z_AXIS, E_AXIS };
enum AxisRelative : uint8_t { REL_X, REL_Y, REL_Z, REL_E, E_MODE_ABS, E_MODE_REL };
struct Position {
  float x,y,z,e;
  float& operator[](int i) { return i==0?x:i==1?y:i==2?z:e; }
};
struct xyze_bool_t {
  bool x,y,z,e;
  bool& operator[](int i) { return i==0?x:i==1?y:i==2?z:e; }
};
Position current_position{10,20,.2f,3}, destination{99,99,99,99};
float feedrate_mm_s=5;
int16_t feedrate_percentage=80;
uint8_t active_extruder=0;
bool running=true;
int prepare_calls=0, moved_calls=0;
bool IsRunning() { return running; }
void prepare_line_to_destination() {
  ++prepare_calls;
  for(int i=0;i<4;i++) if(destination[i]!=current_position[i]) { ++moved_calls; break; }
  current_position=destination;
}
struct Parser {
  std::map<char,float> values;
  char selected=0;
  bool seen(char c) { selected=c; return values.count(c); }
  bool seenval(char c) { return seen(c); }
  bool seen_test(char c) { return values.count(c); }
  bool seen_any() { return !values.empty(); }
  float value_axis_units(AxisEnum) { return values[selected]; }
  int16_t value_int() { return int16_t(values[selected]); }
  uint16_t value_ushort() { return uint16_t(values[selected]); }
  uint8_t byteval(char c, uint8_t fallback=0) { return seenval(c)?uint8_t(values[c]):fallback; }
  float linearval(char c) { return seenval(c)?values[c]:0; }
  float value_feedrate() { return values[selected]/60.0f; }
  std::string load(const std::string& line) {
    values.clear();
    std::istringstream input(line);
    std::string command, token;
    input >> command;
    while(input >> token) {
      char* end=nullptr;
      float value=std::strtof(token.c_str()+1,&end);
      if(end==token.c_str()+1 || *end || values.count(token[0])) std::exit(3);
      values[token[0]]=value;
    }
    return command;
  }
} parser;
class Planner { public:
  static int16_t flow_percentage[1];
  static float e_factor[1];
'''
    middle = r'''
};
int16_t Planner::flow_percentage[1]={85};
float Planner::e_factor[1]={.85f};
Planner planner;
class Temperature { public:
  static uint8_t fan_speed[1];
  static void set_fan_speed(uint8_t fan,uint16_t speed);
} thermalManager;
uint8_t Temperature::fan_speed[1]={42};
class GcodeSuite { public:
  static void M106(); static void M220(); static void M221();
  static void G0_G1(); static void get_destination_from_command();
  static int8_t get_target_extruder_from_command() { return parser.byteval('T'); }
'''
    tail = r'''
};
uint8_t GcodeSuite::axis_relative=0;
'''
    main = r'''
int main(int argc,char** argv) {
  for(int i=1;i<argc;++i) {
    std::string command=parser.load(argv[i]);
    if(command=="G90") GcodeSuite::set_relative_mode(false);
    else if(command=="G91") GcodeSuite::set_relative_mode(true);
    else if(command=="M82") GcodeSuite::set_e_absolute();
    else if(command=="M83") GcodeSuite::set_e_relative();
    else if(command=="M106") GcodeSuite::M106();
    else if(command=="M220") GcodeSuite::M220();
    else if(command=="M221") GcodeSuite::M221();
    else if(command=="G1") GcodeSuite::G0_G1();
    else if(command=="STOPPED") running=false;
    else return 2;
  }
  std::cout << std::setprecision(12)
    << "{\"xyz_relative\":" << GcodeSuite::axis_is_relative(X_AXIS)
    << ",\"e_relative\":" << GcodeSuite::axis_is_relative(E_AXIS)
    << ",\"fan_pwm\":" << int(thermalManager.fan_speed[0])
    << ",\"feedrate_percent\":" << feedrate_percentage
    << ",\"flow_percent\":" << planner.flow_percentage[0]
    << ",\"e_factor\":" << planner.e_factor[0]
    << ",\"feedrate_mm_s\":" << feedrate_mm_s
    << ",\"position\":[" << current_position.x << "," << current_position.y << ","
    << current_position.z << "," << current_position.e << "]"
    << ",\"prepare_calls\":" << prepare_calls << ",\"moved_calls\":" << moved_calls << "}\n";
}
'''
    cpp = directory / 'restore.cpp'
    cpp.write_text(preamble + refresh + '\n' + flow + middle + modes + tail + '\n'.join(snippets) + main)
    binary = directory / 'restore'
    subprocess.run(['clang++', '-std=c++11', str(cpp), '-o', str(binary)], check=True, capture_output=True)
    (directory / 'sources.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return binary
