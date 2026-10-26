"""Unchanged HeaterWatch and manager watch branches; synthetic clock and sensors."""
import hashlib
import re
from native_restore_harness import function


def watch_fixture(header, source, config, main_config):
    template = function(header, 'template <int INCREASE, int HYSTERESIS, millis_t PERIOD>') + ';'
    hot = function(source, 'if (watch_hotend[e].elapsed(ms))')
    bed = function(source, 'if (watch_bed.elapsed(ms))')
    start_hot = function(header, 'static inline void start_watching_hotend(')
    start_bed = function(header, 'static inline void start_watching_bed(')
    settings = {}
    for name in ('WATCH_TEMP_PERIOD', 'WATCH_TEMP_INCREASE', 'WATCH_BED_TEMP_PERIOD', 'WATCH_BED_TEMP_INCREASE', 'TEMP_HYSTERESIS', 'TEMP_BED_HYSTERESIS'):
        text = main_config if name.startswith('TEMP_') else config
        settings[name] = int(re.search(r'^\s*#define ' + name + r'\s+(\d+)', text, re.M)[1])
    typedefs = '\n'.join(re.search(r'  typedef struct HeaterWatch<[^\n]+> ' + name + ';', header)[0] for name in ('hotend_watch_t', 'bed_watch_t'))
    global_code = ''.join(f'#define {key} {value}\n' for key,value in settings.items()) + r'''
#define WATCH_HOTENDS 1
#define E_NAME e
#define HOTEND_INDEX e
#define UNUSED(x) (void)(x)
#define TERN_WATCH_BED(...) __VA_ARGS__
#define H_BED -1
float actual=20;
float degHotend(int){return actual;}
float degBed(){return actual;}
float degTargetHotend(int){return temp_hotend[0].target;}
float degTargetBed(){return temp_bed.target;}
const char*str_t_heating_failed="HEATING FAILED";
''' + template + '\n' + typedefs + r'''
hotend_watch_t watch_hotend[1]{};
bed_watch_t watch_bed{};
'''
    members = start_hot + start_bed + '\nstatic void check_watch(bool bed){const millis_t ms=millis();int e=0;if(bed){' + bed + '}else{' + hot + '}}\n'
    tail = r'''
int main(int argc,char**argv){
 if(argc!=3)return 2;
 std::string scenario=argv[1];bool bed=std::string(argv[2])=="bed";
 int period=bed?WATCH_BED_TEMP_PERIOD:WATCH_TEMP_PERIOD;
 int increase=bed?WATCH_BED_TEMP_INCREASE:WATCH_TEMP_INCREASE;
 int hysteresis=bed?THERMAL_PROTECTION_BED_HYSTERESIS:THERMAL_PROTECTION_HYSTERESIS;
 float target=bed?60:200;
 auto restart=[&](){if(bed)Temperature::start_watching_bed();else Temperature::start_watching_hotend(0);};
 auto next=[&](){return bed?watch_bed.next_ms:watch_hotend[0].next_ms;};
 auto watch_target=[&](){return bed?watch_bed.target:watch_hotend[0].target;};
 tick=scenario=="zero_deadline"?uint32_t(0-period*1000):(scenario=="wrap"?UINT32_MAX-1000:1000);
 restart();uint32_t deadline=next();int before=0;
 if(scenario=="no_rise"||scenario=="wrap"||scenario=="zero_deadline"){
   tick=deadline-1;Temperature::check_watch(bed);before=kills;
   tick=deadline;Temperature::check_watch(bed);
 }else if(scenario=="enough_rise"||scenario=="later_stall"){
   actual+=increase;tick=deadline;Temperature::check_watch(bed);before=kills;
   if(scenario=="later_stall"){tick=next();Temperature::check_watch(bed);}
 }else if(scenario=="insufficient"){
   actual+=increase-0.1f;tick=deadline;Temperature::check_watch(bed);
 }else if(scenario=="near_target"){
   actual=target-hysteresis;tick=deadline;Temperature::check_watch(bed);
 }else if(scenario=="off"){
   if(bed)temp_bed.target=0;else temp_hotend[0].target=0;
   restart();tick=deadline;Temperature::check_watch(bed);
 }else if(scenario=="repeat"){
   tick=deadline;Temperature::check_watch(bed);before=kills;
   Temperature::check_watch(bed);
 }else return 3;
 std::cout<<"{\"kills\":"<<kills<<",\"before_kills\":"<<before
  <<",\"last_heater\":"<<last_heater<<",\"period_seconds\":"<<period
  <<",\"next_ms\":"<<next()<<",\"watch_target\":"<<watch_target()
  <<",\"now\":"<<tick<<",\"nozzle_target\":"<<temp_hotend[0].target
  <<",\"bed_target\":"<<temp_bed.target<<",\"nozzle_pwm\":"<<temp_hotend[0].soft_pwm_amount
  <<",\"bed_pwm\":"<<temp_bed.soft_pwm_amount<<",\"nozzle_pin\":"<<nozzle_pin
  <<",\"bed_pin\":"<<bed_pin<<",\"watchdogs\":"<<watchdogs
  <<",\"screen_fault_updates\":"<<rtscheck.calls<<",\"serial_message\":\""<<serial_message
  <<"\",\"autotemp_enabled\":"<<(planner.autotemp_enabled?"true":"false")<<"}\n";
}
'''
    evidence = dict(settings=settings, snippets_sha256={key:hashlib.sha256(value.encode()).hexdigest()
        for key,value in dict(template=template,typedefs=typedefs,hotend_branch=hot,bed_branch=bed,start_hotend=start_hot,start_bed=start_bed).items()})
    return global_code, members, tail, evidence
