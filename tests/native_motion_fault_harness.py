"""Extracted homing validation and probe-result handling; no physical motion.

Trigger bits, probe readings, deployment, screen, movement and kill are stubs.
This does not execute G28/G29, endstop interrupts, probe timing or terminal kill.
"""
from pathlib import Path
import hashlib
import json
import subprocess
from native_restore_harness import function

ROOT = Path(__file__).resolve().parents[1]


def build_motion_fault_harness(directory):
    directory = Path(directory)
    snippets, evidence = [], []
    for path, signature in [('module/endstops.cpp', 'void Endstops::validate_homing_move()'),
                            ('module/probe.cpp', 'float Probe::probe_at_point(')]:
        text = (ROOT / 'firmware/Marlin/src' / path).read_text()
        snippet = function(text, signature)
        snippets.append(snippet)
        evidence.append(dict(path=path, file_sha256=hashlib.sha256(text.encode()).hexdigest(),
                             snippet_sha256=hashlib.sha256(snippet.encode()).hexdigest()))
    preamble = r'''
#include <cmath>
#include <cstdint>
#include <iostream>
#include <string>
#include <algorithm>
using std::isnan;
#define RTS_AVAILABLE 1
#define G29_RETRY_AND_RECOVER 0
#define ENABLED(x) (x)
#define DISABLED(x) (!(x))
#define BOTH(a,b) ((a)&&(b))
#define BLTOUCH 1
#define BLTOUCH_HS_MODE 1
#define DEBUGGING(x) false
#define DEBUG_SECTION(...)
#define DEBUG_ECHOLNPAIR(...)
#define DEBUG_ECHOLNPGM(...)
#define DEBUG_POS(...)
#define TERN(c,a,b) b
#define _MIN(a,b) std::min(a,b)
#define GET_TEXT(x) x
#define MSG_KILL_HOMING_FAILED "Homing failed"
#define MSG_LCD_PROBING_FAILED "Probing failed"
#define STR_ERR_PROBING_FAILED "Probing failed"
#define LCD_MESSAGEPGM(x) lcd_message=x
#define SERIAL_ERROR_MSG(x) serial_message=x
#define SERIAL_ECHOLNPAIR(...)
using const_float_t = const float;
using feedRate_t = float;
constexpr float XY_PROBE_FEEDRATE_MM_S=10, Z_CLEARANCE_BETWEEN_PROBES=5, z_probe_fast_mm_s=2;
constexpr int ExchangePageBase=1000, ExchangepageAddr=1, Error_203=203, ABNORMAL_PAGE_TEXT_VP=2;
int waitway=7,errorway=0,change_page_font=0,kills=0,hits=0,moves=0,raises=0,deploys=0,stows=0,probes=0,resets=0;
bool trigger=false,reachable=true,deploy_error=false,stow_error=false,probe_triggered=false;
float sample=2;
std::string serial_message,lcd_message,kill_message;
struct TerminalKill{};
void kill(const char* message){++kills;kill_message=message;throw TerminalKill{};}
struct Screen { int calls=0; void RTS_SndData(int,int){++calls;} } rtscheck;
struct Touch { bool triggered(){return probe_triggered;} void _reset(){++resets;} } bltouch;
struct xy { float x,y; };
struct xyz_pos_t {float x,y,z; void operator-=(xy value){x-=value.x;y-=value.y;} };
xyz_pos_t current_position{0,0,10};
bool position_is_reachable(xyz_pos_t){return reachable;}
void do_blocking_move_to(xyz_pos_t pos,float){++moves;current_position=pos;}
void do_blocking_move_to_z(float z,float){++raises;current_position.z=z;}
struct Endstops {
 static bool trigger_state(){return trigger;}
 static void hit_on_purpose(){++hits;}
 static void validate_homing_move();
};
enum ProbePtRaise { PROBE_PT_NONE, PROBE_PT_RAISE, PROBE_PT_BIG_RAISE, PROBE_PT_STOW };
struct Probe {
 xy offset_xy{1,1}; xyz_pos_t offset{0,0,-1};
 bool can_reach(xyz_pos_t){return reachable;}
 bool deploy(){++deploys;return deploy_error;}
 bool stow(){++stows;return stow_error;}
 float run_z_probe(bool){++probes;return sample;}
 float probe_at_point(const_float_t,const_float_t,ProbePtRaise,uint8_t,bool,bool);
};
'''
    main = r'''
int main(int argc,char**argv){
 if(argc!=2)return 2;
 std::string scenario=argv[1];float measured=NAN;bool returned=false;
 if(scenario=="home_hit"||scenario=="home_miss"){
   trigger=scenario=="home_hit";
   try{Endstops::validate_homing_move();returned=true;}catch(TerminalKill&){}
 }else{
   if(scenario=="unreachable")reachable=false;
   else if(scenario=="deploy_error")deploy_error=true;
   else if(scenario=="measurement_error")sample=NAN;
   else if(scenario=="stow_error")stow_error=true;
   else if(scenario=="triggered_success")probe_triggered=true;
   else if(scenario!="success")return 3;
   Probe probe;measured=probe.probe_at_point(10,20,PROBE_PT_STOW,0,true,true);returned=true;
 }
 std::cout<<"{\"returned\":"<<(returned?"true":"false")<<",\"invalid_measurement\":"<<(isnan(measured)?"true":"false")
 <<",\"kills\":"<<kills<<",\"hits\":"<<hits<<",\"moves\":"<<moves<<",\"raises\":"<<raises
 <<",\"deploys\":"<<deploys<<",\"stows\":"<<stows<<",\"probes\":"<<probes<<",\"resets\":"<<resets
 <<",\"waitway\":"<<waitway<<",\"errorway\":"<<errorway<<",\"screen_calls\":"<<rtscheck.calls
 <<",\"page\":"<<change_page_font<<",\"serial_message\":\""<<serial_message
 <<"\",\"lcd_message\":\""<<lcd_message<<"\",\"kill_message\":\""<<kill_message<<"\"}\n";
}
'''
    source = directory / 'motion_faults.cpp'
    source.write_text(preamble + '\n'.join(snippets) + main)
    binary = directory / 'motion_faults'
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', str(source), '-o', str(binary)],
                   check=True, capture_output=True, text=True)
    (directory / 'build-evidence.json').write_text(json.dumps(dict(
        evidence_type='extracted_native_functions_with_physical_stubs', sources=evidence,
        generated_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        terminal_kill='throwing stub; shutdown internals not executed'), indent=2)+'\n')
    return binary
