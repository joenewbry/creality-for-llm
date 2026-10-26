import asyncio
from pathlib import Path
import sys
import time
import pytest
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('finish',['job.resume','job.cancel','printer.stop'])
def test_external_hot_client_controls_pause_and_finish(tmp_path,finish):
    async def run():
        params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'tests/octoprint_virtual_smoke.py'),
            '--mcp','--model-fixture','--scenario','hot_console','--journal',str(tmp_path/'hot.sqlite')],cwd=str(ROOT))
        async with stdio_client(params) as (incoming,outgoing):
            async with ClientSession(incoming,outgoing,read_timeout_seconds=20.) as client:
                await client.initialize()
                async def call(name,args=None):
                    result=await client.call_tool(name,args or {})
                    assert not result.is_error,result
                    return result.structured_content
                number=0
                async def submit(name,arguments=None,expected='completed'):
                    nonlocal number
                    deadline=time.time()+15
                    for attempt in range(5):
                        number+=1
                        status=await call('job.inspect')
                        args={} if name=='printer.stop' else {'job_id':status['job_id']}
                        if arguments is not None:args=dict(arguments)
                        if name=='job.stage':args['file_sha256']=status['file_sha256']
                        request=dict(request_id=f'{name}-{number}',printer_id=status['printer_id'],
                            connection_generation=status['connection_generation'],expected_state_version=status['state_version'],
                            deadline=deadline,arguments=args)
                        result=await call(name,request)
                        if result['status']=='rejected' and result['result'].get('reason')=='stale_state':
                            assert await call(name,request)==result
                            assert time.time()<deadline,result
                            continue  # Rejected before dispatch; new observation and request ID.
                        break
                    else:
                        pytest.fail('state kept changing before dispatch')
                    end=time.monotonic()+15
                    while result['status']=='running' and time.monotonic()<end:
                        await asyncio.sleep(.02)
                        result=await call('operation.get',{'operation_id':result['operation_id']})
                    assert result['status']==expected,result
                    assert await call(name,request)==result
                    return result
                status=await call('job.inspect')
                assert not status['physical_printer'] and status['scripted_temperatures']
                calibration=await call('calibration.inspect')
                original=calibration['steps_per_mm']['X']
                original_saved=calibration['saved_settings_sha256']
                await submit('calibration.set_temporary',{'axis':'x','steps_per_mm':original*1.05,
                    'expected_settings_sha256':calibration['settings_sha256']})
                temporary=await call('calibration.inspect')
                assert temporary['temporary_changes'] and temporary['saved_settings_sha256']==original_saved
                await submit('calibration.persist',{'expected_settings_sha256':calibration['settings_sha256']},expected='rejected')
                saved=await submit('calibration.persist',{'expected_settings_sha256':temporary['settings_sha256']})
                assert saved['result']['persisted'] and not saved['result']['physical_calibration_qualified']
                after=await call('calibration.inspect')
                assert not after['temporary_changes'] and after['saved_steps_per_mm']['X']==original*1.05
                await submit('calibration.set_temporary',{'axis':'x','steps_per_mm':original,
                    'expected_settings_sha256':after['settings_sha256']})
                restored=await call('calibration.inspect')
                await submit('calibration.persist',{'expected_settings_sha256':restored['settings_sha256']})
                for heater,target in [('nozzle',180),('bed',50)]:
                    temperature=await submit('printer.set_temperature',{'heater':heater,'target_celsius':target})
                    assert temperature['result']['target_celsius']==target
                    assert not temperature['result']['temperature_readiness_verified']
                    assert not temperature['result']['physical_heating_qualified']
                    observed=(await call('printer.status'))['setup']['temperature_observations'][heater]
                    assert observed['target_celsius']==target and observed['actual_celsius']!=target
                await submit('printer.set_temperature',{'heater':'bed','target_celsius':91},expected='rejected')
                await submit('printer.home',{},expected='rejected')
                for heater in ('nozzle','bed'):
                    await submit('printer.set_temperature',{'heater':heater,'target_celsius':0})
                fan=await submit('printer.set_fan',{'fan':0,'pwm':128})
                assert fan['result']['pwm']==128 and not fan['result']['physical_fan_qualified']
                assert (await call('printer.status'))['setup']['fan_pwm']==128
                await submit('printer.set_fan',{'fan':0,'pwm':0})
                capture = await call('printer.machine_events', {'after':0,'limit':1000})
                assert capture['scope'] == 'serial_machine_observations'
                assert capture['health']['capturing'] and capture['health']['fault'] is None
                assert not capture['recovery_authority']
                assert any(row['event']['kind']=='ack' for row in capture['events'])
                assert all(row['capture_id']==capture['health']['capture_id'] for row in capture['events'])
                await submit('job.stage');await submit('job.start')
                await asyncio.sleep(.15)
                await submit('job.pause')
                end=time.monotonic()+5
                while True:
                    pause=await call('job.pause_status')
                    if pause['phase']=='standby':break
                    assert time.monotonic()<end,pause
                    await asyncio.sleep(.02)
                assert pause['resume_request_available'] and not pause['resume_authorized']
                await submit('printer.set_fan',{'fan':0,'pwm':64},expected='rejected')
                await submit('printer.set_temperature',{'heater':'nozzle','target_celsius':0},expected='rejected')
                result=await submit(finish)
                if finish=='printer.stop':
                    assert result['result']['requires_recovery'] and not result['result']['resume_allowed']
                    assert (await call('job.inspect'))['job_state']=='stopped'
                    assert not (await call('job.pause_status'))['resume_request_available']
                elif finish=='job.cancel':
                    assert result['result']['heater_targets_zero']
                else:
                    end=time.monotonic()+30
                    while True:
                        status=await call('job.inspect')
                        if status['job_state']=='completed':break
                        assert time.monotonic()<end,status
                        await asyncio.sleep(.02)
                    assert status['simulation_evidence']['all_job_moves_once_in_order']
                    assert status['simulation_evidence']['model_moves']==82
                    assert (await call('job.pause_status'))['phase']=='inactive'
    asyncio.run(run())
