"""Extract actual hotend wait loop; timer, sensor, screen and idle are stubs."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
from native_restore_harness import function
ROOT=Path(__file__).resolve().parents[1]


def build_heating_harness(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    source=ROOT/'firmware/Marlin/src'
    original=(source/'module/temperature.cpp').read_text()
    snippet=function(original,'bool Temperature::wait_for_hotend(')
    bed_snippet=function(original,'bool Temperature::wait_for_bed(')
    config=(ROOT/'firmware/Marlin/Configuration.h').read_text()
    settings={name:int(re.search(r'^#define '+name+r'\s+(\d+)',config,re.M)[1])
        for name in ('TEMP_RESIDENCY_TIME','TEMP_WINDOW','TEMP_HYSTERESIS',
                     'TEMP_BED_RESIDENCY_TIME','TEMP_BED_WINDOW','TEMP_BED_HYSTERESIS')}
    (directory/'inc').mkdir(exist_ok=True);(directory/'feature').mkdir(exist_ok=True)
    (directory/'inc/MarlinConfigPre.h').write_text('')
    (directory/'feature/host_actions.h').write_text('extern bool host_prompt_token_mode,host_prompt_protocol_busy; inline void host_response_enqueue(uint8_t) {}\n')
    (directory/'feature/e_parser.h').write_bytes((source/'feature/e_parser.h').read_bytes())
    preamble=r'''
#include <cstdint>
#include <cmath>
#include <iostream>
#include <string>
#include <stdexcept>
#define ENABLED(x) x
#define DISABLED(x) (!(x))
#define HOST_PROMPT_SUPPORT 1
#define LLM_PROMPT_PROTOCOL 1
#define RTS_AVAILABLE 1
#define FORCE_INLINE inline
#define ISEOL(c) ((c)=='\n'||(c)=='\r')
#define OPTARG(...)
#define TERN_(...)
#define SERIAL_ECHOPGM(...)
#define SERIAL_ECHO(...)
#define SERIAL_CHAR(...)
#define SERIAL_EOL(...)
#define SEC_TO_MS(x) ((x)*1000UL)
#define ELAPSED(a,b) (int32_t((a)-(b))>=0)
#define PENDING(a,b) (!ELAPSED(a,b))
#define ABS(x) std::abs(x)
#define MIN_COOLING_SLOPE_TIME 60
#define MIN_COOLING_SLOPE_DEG 1.5
#define MIN_COOLING_SLOPE_TIME_BED 60
#define MIN_COOLING_SLOPE_DEG_BED 1.0
using millis_t=uint32_t;
using celsius_float_t=float;
bool wait_for_heatup=false,wait_for_user=false;
bool host_prompt_token_mode=false,host_prompt_protocol_busy=false;
#include "feature/e_parser.h"
bool EmergencyParser::enabled=true;
bool EmergencyParser::killed_by_M112=false,EmergencyParser::quickstop_by_M410=false;
uint8_t EmergencyParser::M876_reason=0;
struct Gcode { int resets=0; void reset_stepper_timeout(){++resets;} } gcode;
struct Card { struct {bool abort_sd_printing=false;} flag; } card;
struct UI {int resets=0;void reset_status(){++resets;}} ui;
int Update_Time_Value=0,RTS_UPDATE_VALUE=123,ExchangePageBase=1000,ExchangepageAddr=5;
struct RTS {int calls=0;void RTS_SndData(int,int){++calls;}} rtscheck;
uint32_t tick=0;int loops=0,reports=0;float actual=20,target=200;
std::string scenario;
millis_t millis(){return tick;}
void print_heater_states(uint8_t){++reports;}
float degHotend(uint8_t){return actual;}
float degTargetHotend(uint8_t){return target;}
bool isCoolingHotend(uint8_t){return actual>target;}
bool isHeatingHotend(uint8_t){return actual<target;}
uint8_t active_extruder=0;
float degBed(){return actual;}
float degTargetBed(){return target;}
bool isCoolingBed(){return actual>target;}
bool isHeatingBed(){return actual<target;}
void idle(){
    ++loops;tick+=1000;
    if(loops>100)throw std::runtime_error("fixture_iteration_limit");
    if((scenario=="heat_ready"||scenario=="heat_hysteresis") && loops>=3)actual=target;
    if(scenario=="heat_hysteresis" && loops==7)actual=target-5;
    if((scenario=="m108"||scenario=="token_m108") && loops==3){
        EmergencyParser::State state=EmergencyParser::EP_RESET;
        for(char c:std::string("M108\n"))EmergencyParser::update(state,c);
    }
    if(scenario=="sd_abort" && loops==3)card.flag.abort_sd_printing=true;
    if(scenario=="token_m108" && loops==5)card.flag.abort_sd_printing=true;
    if(scenario=="target_lowered" && loops==3)target=0;
}
struct Temperature {static bool wait_for_hotend(uint8_t,bool);static bool wait_for_bed(bool);};
'''
    tail=r'''
int main(int argc,char**argv){
    if(argc<2||argc>3)return 2;
    bool bed=argc==3 && std::string(argv[2])=="bed";
    if(bed)target=60;
    scenario=argv[1];
    bool no_cooling=true;
    if(scenario=="cool_skip"||scenario=="cool_stalled"){actual=200;target=20;}
    if(scenario=="cool_stalled")no_cooling=false;
    if(scenario=="token_m108")host_prompt_token_mode=true;
    bool result=bed?Temperature::wait_for_bed(no_cooling):Temperature::wait_for_hotend(0,no_cooling);
    std::cout<<"{\"returned\":"<<(result?"true":"false")<<",\"loops\":"<<loops
        <<",\"actual\":"<<actual<<",\"target\":"<<target<<",\"reports\":"<<reports
        <<",\"ui_status_resets\":"<<ui.resets<<",\"screen_ready_updates\":"<<rtscheck.calls<<",\"stepper_resets\":"<<gcode.resets
        <<",\"wait_active\":"<<(wait_for_heatup?"true":"false")<<"}\n";
}
'''
    cpp=directory/'main.cpp'
    cpp.write_text(''.join(f'#define {k} {v}\n' for k,v in settings.items())+preamble+snippet+bed_snippet+tail)
    binary=directory/'heating'
    subprocess.run(['clang++','-std=c++17','-O0',str(cpp),'-o',str(binary)],check=True,capture_output=True,text=True)
    (directory/'source-evidence.json').write_text(json.dumps(dict(settings=settings,
        temperature_file_sha256=hashlib.sha256(original.encode()).hexdigest(),
        wait_loop_sha256=hashlib.sha256(snippet.encode()).hexdigest(),
        bed_wait_loop_sha256=hashlib.sha256(bed_snippet.encode()).hexdigest(),
        emergency_parser_sha256=hashlib.sha256((source/'feature/e_parser.h').read_bytes()).hexdigest(),
        physical_qualified=False),indent=2)+'\n')
    return binary
