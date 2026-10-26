"""Compile the actual firmware header unchanged with minimal external stubs.

This validates parser logic only, not interrupts, the USB peripheral or hardware.
Malformed resume commands are regression tests against the development branch.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def parser_binary(tmp_path_factory):
    firmware = ROOT / "firmware/Marlin/src/feature/e_parser.h"
    if not firmware.exists():
        pytest.fail("Pinned firmware worktree missing; native qualification cannot be skipped")
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        pytest.fail("C++ compiler required for native firmware parser tests")
    build = tmp_path_factory.mktemp("firmware-parser")
    (build / "feature").mkdir()
    (build / "inc").mkdir()
    source = firmware.read_bytes()
    (build / "feature/e_parser.h").write_bytes(source)
    (build / "inc/MarlinConfigPre.h").write_text("""
#pragma once
#include <cstdint>
#define HOST_PROMPT_SUPPORT 1
#define LLM_PROMPT_PROTOCOL 1
#define ENABLED(V) V
#define FORCE_INLINE inline
#define ISEOL(C) ((C) == '\\n' || (C) == '\\r')
""")
    (build / "feature/host_actions.h").write_text("#pragma once\nbool host_response_enqueue(uint8_t);\nextern volatile bool host_prompt_token_mode, host_prompt_protocol_busy;\n")
    (build / "main.cpp").write_text(r'''
#include <iostream>
#include <string>
#include "feature/e_parser.h"
bool wait_for_user = true, wait_for_heatup = true;
volatile bool host_prompt_token_mode = false, host_prompt_protocol_busy = false;
bool EmergencyParser::enabled = true;
bool EmergencyParser::killed_by_M112 = false;
bool EmergencyParser::quickstop_by_M410 = false;
uint8_t EmergencyParser::M876_reason = 0;
int response = -1, calls = 0;
bool host_response_enqueue(uint8_t value) { response = value; ++calls; return true; }
void HAL_reboot() {}
int main(int argc, char **argv) {
  if (argc < 2) return 2;
  if (argc > 2) {
    const std::string mode(argv[2]);
    if (mode == "disabled") EmergencyParser::disable();
    if (mode == "bound") host_prompt_token_mode = true;
    if (mode == "pending") host_prompt_protocol_busy = true;
  }
  auto state = EmergencyParser::EP_RESET;
  for (unsigned char c : std::string(argv[1])) EmergencyParser::update(state, c);
  std::cout << "{\"wait\":" << wait_for_user
    << ",\"heat_wait\":" << wait_for_heatup
    << ",\"killed\":" << EmergencyParser::killed_by_M112
    << ",\"quickstop\":" << EmergencyParser::quickstop_by_M410
    << ",\"response\":" << response << ",\"calls\":" << calls << "}";
}
''')
    binary = build / "parser"
    subprocess.run([compiler, "-std=gnu++14", "-Wall", "-Wextra", str(build / "main.cpp"), "-o", str(binary)], check=True, capture_output=True, text=True)
    assert hashlib.sha256((build / "feature/e_parser.h").read_bytes()).digest() == hashlib.sha256(source).digest()
    return binary


def run(binary, text, disabled=False):
    args = [str(binary), text] + (["disabled"] if disabled else [])
    return json.loads(subprocess.run(args, check=True, capture_output=True, text=True).stdout)


@pytest.mark.parametrize("text", ["M108\n", "N123 M108*55\n", " M108\r\n"])
def test_actual_parser_releases_wait_without_normal_queue(parser_binary, text):
    result = run(parser_binary, text)
    assert result["wait"] == result["heat_wait"] == 0
    assert result["killed"] == 0


def test_actual_parser_stop_and_quickstop_are_distinct(parser_binary):
    kill = run(parser_binary, "M112\n")
    quick = run(parser_binary, "M410\n")
    assert kill["killed"] == 1 and kill["quickstop"] == 0
    assert quick["killed"] == 0 and quick["quickstop"] == 1


def test_actual_parser_prompt_response_and_disabled_mode(parser_binary):
    result = run(parser_binary, "M876 S1\n")
    assert result["response"] == 1 and result["calls"] == 1
    disabled = run(parser_binary, "M108\nM112\nM876 S1\n", disabled=True)
    assert disabled["wait"] == 1 and disabled["killed"] == 0 and disabled["calls"] == 0


def test_actual_parser_ignores_comments_and_partial_lines(parser_binary):
    result = run(parser_binary, ";M108\nM108")
    assert result["wait"] == 1


@pytest.mark.parametrize("text", ["M1080\n", "M108.0\n", "M108 X1\n", "M108foo\n"])
def test_malformed_wait_command_must_not_resume(parser_binary, text):
    assert run(parser_binary, text)["wait"] == 1


@pytest.mark.parametrize("text", ["M876 S10\n", "M876 S-1\n", "M876 S1.0\n", "M876 S1 X1\n", "M876 Sfoo1\n"])
def test_malformed_prompt_response_must_not_select_button_one(parser_binary, text):
    assert run(parser_binary, text)["calls"] == 0


@pytest.mark.parametrize("text", ["M876 S1 ;comment\n", "N8 M876 S1*24\n", "M876 S1\r\n"])
def test_valid_prompt_suffixes_still_work_once(parser_binary, text):
    result = run(parser_binary, text)
    assert result["calls"] == 1 and result["response"] == 1


@pytest.mark.parametrize("mode", ["bound", "pending"])
@pytest.mark.parametrize("command", ["M108\n", "M108 ;comment\n"])
def test_token_mode_and_pending_negotiation_preserve_wait_but_not_stop(parser_binary, mode, command):
    result = subprocess.run([str(parser_binary), command + "M112\nM410\n", mode], check=True, capture_output=True, text=True)
    state = json.loads(result.stdout)
    assert state["wait"] == state["heat_wait"] == 1
    assert state["killed"] == state["quickstop"] == 1
