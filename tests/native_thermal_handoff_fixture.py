"""Drive extracted watch/runaway functions together; excludes full manager IO."""
HANDOFF_TAIL = r'''
int main(int argc,char**argv){
 if(argc!=3)return 2;
 std::string scenario=argv[1];bool bed=std::string(argv[2])=="bed";
 int watch_period=bed?WATCH_BED_TEMP_PERIOD:WATCH_TEMP_PERIOD;
 int period=bed?THERMAL_PROTECTION_BED_PERIOD:THERMAL_PROTECTION_PERIOD;
 int hysteresis=bed?THERMAL_PROTECTION_BED_HYSTERESIS:THERMAL_PROTECTION_HYSTERESIS;
 float requested=bed?60:200;
 Temperature::tr_state_machine_t state{};
 if(bed)Temperature::start_watching_bed();else Temperature::start_watching_hotend(0);
 uint32_t drop_at=uint32_t(watch_period+1)*1000;
 int before_drop_state=-1;bool before_drop_watch=false;
 for(tick=0;tick<=600000 && !kills;tick+=1000){
   if(tick>=1000){
     if(scenario=="normal")actual=requested;
     else if(scenario=="stall")actual=requested-hysteresis-1;
     else if(scenario=="boundary_hold"||scenario=="drop")actual=requested-hysteresis;
     else if(scenario=="above_drop")actual=requested-hysteresis+1;
     else return 3;
   }
   if(tick==drop_at){
     before_drop_state=int(state.state);
     before_drop_watch=bed?watch_bed.next_ms!=0:watch_hotend[0].next_ms!=0;
   }
   if((scenario=="drop"||scenario=="above_drop") && tick>=drop_at)actual=20;
   // Match the sampled branch order in this pinned source. Stop the driver
   // when the recording kill stub returns; a real terminal kill does not.
   if(bed)Temperature::check_watch(true);
   if(kills)break;
   state.run(actual,requested,bed?-1:0,period,hysteresis);
   if(kills)break;
   if(!bed)Temperature::check_watch(false);
   if(kills)break;
 }
 std::cout<<"{\"kills\":"<<kills<<",\"now\":"<<tick<<",\"state\":"<<int(state.state)
 <<",\"before_drop_state\":"<<before_drop_state<<",\"before_drop_watch\":"<<(before_drop_watch?"true":"false")
 <<",\"serial_message\":\""<<serial_message<<"\",\"nozzle_target\":"<<temp_hotend[0].target
 <<",\"bed_target\":"<<temp_bed.target<<",\"nozzle_pin\":"<<nozzle_pin<<",\"bed_pin\":"<<bed_pin<<"}\n";
}
'''
