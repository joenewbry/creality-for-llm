"""Compile unchanged thermal-runaway/error/shutdown functions with simulated IO."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
from native_restore_harness import function
ROOT = Path(__file__).resolve().parents[1]


def build_thermal_protection_harness(directory, *, heating_watch=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    original = (ROOT / 'firmware/Marlin/src/module/temperature.cpp').read_text()
    header = (ROOT / 'firmware/Marlin/src/module/temperature.h').read_text()
    config = (ROOT / 'firmware/Marlin/Configuration_adv.h').read_text()
    names = ('THERMAL_PROTECTION_PERIOD', 'THERMAL_PROTECTION_HYSTERESIS',
             'THERMAL_PROTECTION_BED_PERIOD', 'THERMAL_PROTECTION_BED_HYSTERESIS')
    settings = {name: int(re.search(r'^\s*#define ' + name + r'\s+(\d+)', config, re.M)[1]) for name in names}
    declarations = header[header.index('      enum TRState'):header.index('      static tr_state_machine_t tr_state_machine')]
    snippets = {name: function(original, signature) for name, signature in {
        'runaway': 'void Temperature::tr_state_machine_t::run(',
        'error': 'void Temperature::_temp_error(',
        'disable': 'void Temperature::disable_all_heaters()'}.items()}
    preamble = r'''
#include <cstdint>
#include <algorithm>
#define _MIN(a,b) std::min(a,b)
#include <iostream>
#include <string>
#include <stdexcept>
#define ENABLED(x) x
#define HEATER_IDLE_HANDLER 1
#define RTS_AVAILABLE 1
#define ADAPTIVE_FAN_SLOWING 0
#define HAS_HOTEND 1
#define HAS_TEMP_HOTEND 1
#define HAS_HEATED_BED 1
#define HAS_HEATED_CHAMBER 0
#define HAS_COOLER 0
#define HOTENDS 1
#define HOTEND_LOOP() for(int e=0;e<1;++e)
#define TERN_(option,...) TERN_##option(__VA_ARGS__)
#define TERN_AUTOTEMP(...) __VA_ARGS__
#define TERN_PROBING_HEATERS_OFF(...)
#define TERN_DWIN_CREALITY_LCD(...)
#define TERN0(...) false
#define TERN1(...) true
#define SEC_TO_MS(x) ((x)*1000UL)
#define PENDING(a,b) (int32_t((a)-(b))<0)
#define ELAPSED(a,b) (!PENDING(a,b))
#define GET_TEXT(x) "thermal runaway"
#define SERIAL_ERROR_START() serial_error=true
#define SERIAL_ECHOPGM_P(x) serial_message=(x)
#define SERIAL_ECHOPGM(...)
#define SERIAL_ECHO(...)
#define SERIAL_EOL()
#define LOW 0
#define REPEAT(n,f) f(0)
#define WRITE_HEATER_0(x) nozzle_pin=(x)
#define WRITE_HEATER_BED(x) bed_pin=(x)
using millis_t=uint32_t;
using const_celsius_float_t=const float;
using celsius_t=int16_t;
using heater_id_t=int8_t;
using PGM_P=const char*;
using IdleIndex=int;
uint32_t tick=0;
millis_t millis(){return tick;}
int nozzle_pin=1,bed_pin=1,kills=0,watchdogs=0,last_heater=99;
bool running=true,serial_error=false;
std::string serial_message;
int ExchangePageBase=1000,ExchangepageAddr=5,change_page_font=0;
struct RTS {int calls=0;void RTS_SndData(int,int){++calls;}} rtscheck;
struct Idle {bool timed_out=false;} heater_idle[2];
IdleIndex idle_index_for_id(heater_id_t h){return h<0?1:0;}
struct Planner {bool autotemp_enabled=true;} planner;
struct Heater {float target;int soft_pwm_amount;} temp_hotend[1]={{200,100}},temp_bed={60,100};
void setTargetHotend(int t,int e){temp_hotend[e].target=t;}
void setTargetBed(int t){temp_bed.target=t;}
bool IsRunning(){return running;}
void watchdog_refresh(){++watchdogs;}
void loud_kill(PGM_P,heater_id_t h){++kills;last_heater=h;running=false;}
const char*str_t_thermal_runaway="THERMAL RUNAWAY";
struct Temperature {
static void _temp_error(heater_id_t,PGM_P,PGM_P);
static void disable_all_heaters();
'''
    tail = r'''
int main(int argc,char**argv){
 if(argc!=3)return 2;
 std::string scenario=argv[1];bool bed=std::string(argv[2])=="bed";
 int period=bed?THERMAL_PROTECTION_BED_PERIOD:THERMAL_PROTECTION_PERIOD;
 int hysteresis=bed?THERMAL_PROTECTION_BED_HYSTERESIS:THERMAL_PROTECTION_HYSTERESIS;
 float target=bed?60:200;
 int heater=bed?-1:0;
 Temperature::tr_state_machine_t state{};
 auto sample=[&](uint32_t at,float current,float requested){tick=at;state.run(current,requested,heater,period,hysteresis);};
 uint32_t start=scenario=="wrap"?UINT32_MAX-1000:1000;
 sample(start,target,target);
 uint32_t deadline=start+uint32_t(period*1000);
 int before_kills=0;
 if(scenario=="deadline"||scenario=="wrap"){
   sample(deadline-1,target-hysteresis-1,target);before_kills=kills;
   sample(deadline,target-hysteresis-1,target);
 }else if(scenario=="hysteresis_edge"){
   sample(deadline,target-hysteresis,target);
 }else if(scenario=="recovered"){
   sample(deadline-1,target-hysteresis-1,target);
   sample(deadline,target,target);
   sample(deadline+1,target-hysteresis-1,target);
 }else if(scenario=="off"){
   sample(deadline,20,0);
 }else if(scenario=="first_heating"){
   state={};sample(0,20,target);sample(period*10000,20,target);
 }else if(scenario=="target_changed"){
   sample(deadline,20,target+10);
 }else if(scenario=="idle_timeout"){
   heater_idle[idle_index_for_id(heater)].timed_out=true;
   sample(deadline,20,target);
 }else if(scenario=="repeat_fault"){
   sample(deadline,20,target);before_kills=kills;
   sample(deadline+1,20,target);
 }else return 3;
 std::cout<<"{\"state\":"<<int(state.state)<<",\"kills\":"<<kills
  <<",\"before_kills\":"<<before_kills<<",\"last_heater\":"<<last_heater
  <<",\"nozzle_target\":"<<temp_hotend[0].target<<",\"bed_target\":"<<temp_bed.target
  <<",\"nozzle_pwm\":"<<temp_hotend[0].soft_pwm_amount<<",\"bed_pwm\":"<<temp_bed.soft_pwm_amount
  <<",\"nozzle_pin\":"<<nozzle_pin<<",\"bed_pin\":"<<bed_pin
  <<",\"watchdogs\":"<<watchdogs<<",\"screen_fault_updates\":"<<rtscheck.calls
  <<",\"autotemp_enabled\":"<<(planner.autotemp_enabled?"true":"false")
  <<",\"serial_error\":"<<(serial_error?"true":"false")
  <<",\"serial_message\":\""<<serial_message<<"\",\"period_seconds\":"<<period
  <<",\"hysteresis\":"<<hysteresis<<"}\n";
}
'''
    watch_evidence = None
    if heating_watch:
        from native_heater_watch_fixture import watch_fixture
        globals_code, members, tail, watch_evidence = watch_fixture(header, original, config,
            (ROOT / 'firmware/Marlin/Configuration.h').read_text())
        preamble = preamble.replace('struct Temperature {', globals_code + '\nstruct Temperature {')
        declarations += members
        if heating_watch == 'handoff':
            from native_thermal_handoff_fixture import HANDOFF_TAIL
            tail = HANDOFF_TAIL
    cpp = directory / 'main.cpp'
    cpp.write_text(''.join(f'#define {k} {v}\n' for k, v in settings.items()) + preamble + declarations + '};\n' + ''.join(snippets.values()) + tail)
    binary = directory / 'thermal-protection'
    subprocess.run(['clang++', '-std=c++17', '-O0', str(cpp), '-o', str(binary)], check=True, capture_output=True, text=True)
    evidence = dict(settings=settings, fixture_tail_sha256=hashlib.sha256(tail.encode()).hexdigest(), temperature_file_sha256=hashlib.sha256(original.encode()).hexdigest(),
                    header_file_sha256=hashlib.sha256(header.encode()).hexdigest(),
                    configuration_adv_sha256=hashlib.sha256(config.encode()).hexdigest(),
                    declaration_sha256=hashlib.sha256(declarations.encode()).hexdigest(),
                    snippet_sha256={name: hashlib.sha256(value.encode()).hexdigest() for name, value in snippets.items()},
                    physical_qualified=False, pin_io='recording stub', kill='recording stub', sensors='scripted', idle_handler=True, autotemp=True, target_setters='recording stubs',
                    excluded=['sensor sampling', 'interrupts', 'initial heating watchdog', 'full manager', 'physical kill', 'adaptive fan slowing', 'bogus temperature grace period'])
    if heating_watch:
        evidence['heating_watch'] = watch_evidence
        evidence['excluded'].remove('initial heating watchdog')
    (directory / 'source-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return binary
