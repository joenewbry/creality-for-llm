"""Exercise unchanged copied host_actions.cpp against stubbed physical dependencies.

The C++ functions under test are the real development source. Sensor, queue,
serial-output and advanced-pause dependencies are substitutes, not a printer.
"""
import json
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


from native_harness import build_native_harness


@pytest.fixture(scope="module")
def actions_binary(tmp_path_factory):
    return build_native_harness(tmp_path_factory.mktemp("host-actions"))


def run(binary, reason, response, runout=0):
    result = subprocess.run([str(binary), str(reason), str(response), str(runout)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_valid_continue_releases_wait(actions_binary):
    result = run(actions_binary, 2, 0)
    assert result["waiting"] == 0 and result["reason"] == 0


@pytest.mark.parametrize("reason,response", [(2, 1), (2, 9), (1, 9), (4, 1), (5, 9)])
def test_invalid_response_preserves_prompt_and_does_nothing(actions_binary, reason, response):
    result = run(actions_binary, reason, response)
    assert result["waiting"] == 1 and result["reason"] == reason
    assert result["queued"] == result["pause_response"] == 0


def test_remote_continue_never_disables_triggered_sensor(actions_binary):
    result = run(actions_binary, 1, 1, runout=1)
    assert result["sensor_enabled"] == result["runout_latched"] == 1
    assert result["pause_response"] == 0 and result["reason"] == 1


def test_continue_with_cleared_runout_does_not_change_sensor_enablement(actions_binary):
    result = run(actions_binary, 1, 1, runout=0)
    assert result["sensor_enabled"] == 1 and result["pause_response"] == 2


def test_no_prompt_and_unimplemented_reheat_are_not_consumed(actions_binary):
    for reason in (0, 3):
        result = run(actions_binary, reason, 0)
        assert result["waiting"] == 1 and result["reason"] == reason
        assert result["queued"] == result["pause_response"] == 0


@pytest.mark.parametrize('scenario', ['unbound', 'wrong_session', 'replacement', 'duplicate',
    'assembling', 'choice', 'ended', 'local', 'rebind', 'runout', 'unsupported', 'reboot', 'deferred', 'stale_deferred', 'bound_legacy',
    'wait_timeout', 'wait_manual', 'wait_replacement', 'wait_rebind', 'wait_answered',
    'screen_continue', 'screen_continue_blocked', 'screen_runout', 'screen_runout_blocked', 'user_runout'])
def test_firmware_token_response_is_bound_to_exact_prompt(actions_binary, scenario):
    result = subprocess.run([str(actions_binary), scenario], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['ok'] is True


def test_identity_counters_exhaust_without_reusing_tokens(tmp_path):
    header = ROOT / 'firmware/Marlin/src/feature/host_prompt_identity.h'
    shutil.copyfile(header, tmp_path / header.name)
    source = tmp_path / 'identity.cpp'
    source.write_text(r'''
#include <cstdint>
#include <cassert>
#define private public
#include "host_prompt_identity.h"
#undef private
int main() {
  HostPromptIdentity identity;
  HostPromptSession a = { 1, 2 }, b = { 1, 3 };
  assert(!identity.bind({0,0}));
  assert(identity.bind(a));
  identity.generation_ = UINT32_MAX - 1; // white-box boundary setup only
  identity.begin(); identity.button(); identity.show();
  assert(identity.generation() == UINT32_MAX);
  assert(identity.check(a, UINT32_MAX, 0) == HostPromptCheck::Ready);
  identity.begin(); identity.button(); identity.show();
  assert(identity.check(a, UINT32_MAX, 0) == HostPromptCheck::Exhausted);
  identity.bind(a);
  assert(identity.check(a, UINT32_MAX, 0) == HostPromptCheck::Exhausted);
  identity.bind(b);
  assert(identity.check(a, UINT32_MAX, 0) == HostPromptCheck::WrongSession);
  assert(identity.check(b, identity.generation(), 0) == HostPromptCheck::Exhausted);
  identity = HostPromptIdentity();
  assert(identity.bind(b));
  identity.begin();
  for (int i=0; i<256; ++i) identity.button();
  identity.show();
  assert(identity.check(b, identity.generation(), 0) == HostPromptCheck::Exhausted);
}
''')
    binary = tmp_path / 'identity'
    subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++14', str(source), '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True)
