"""Build the shared native firmware harness; all physical dependencies are stubs.

Used by pytest and the native-only prompt console. Never a hardware daemon.
"""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build_native_harness(build: Path):
    src = ROOT / "firmware/Marlin/src"
    build = Path(build)
    build.mkdir(parents=True, exist_ok=True)
    for folder in ("feature", "inc", "HAL/shared", "gcode"):
        (build / folder).mkdir(parents=True, exist_ok=True)
    for name in ("host_actions.cpp", "host_actions.h", "host_prompt_identity.h", "host_prompt_wire.h", "host_prompt_inbox.h", "host_prompt_wire.cpp"):
        shutil.copyfile(src / "feature" / name, build / "feature" / name)
        assert (src / "feature" / name).read_bytes() == (build / "feature" / name).read_bytes()
    (build / "inc/MarlinConfigPre.h").write_text(r'''
#pragma once
#include <cstdint>
#include <iostream>
#include <string>
extern std::string serial_output;
#define HOST_ACTION_COMMANDS 1
#define HOST_PROMPT_SUPPORT 1
#define LLM_PROMPT_PROTOCOL 1
#define ADVANCED_PAUSE_FEATURE 1
#define HAS_FILAMENT_SENSOR 1
#define HAS_RESUME_CONTINUE 1
#define M600_PURGE_MORE_RESUMABLE 1
#define SDSUPPORT 1
#define ENABLED(V) V
#define BOTH(A,B) ((A) && (B))
#define TERN_(C,V) V
#define TERN0(C,V) V
#define PSTR(S) S
#define PGMSTR(N,S) const char N[] = S
using PGM_P = const char *;
#define PORT_REDIRECT(X) do {} while (0)
#define SERIAL_ECHOPGM(X) do { serial_output += (X); } while (0)
#define SERIAL_ECHOPGM_P(X) do {} while (0)
#define SERIAL_ECHOLN(X) do {} while (0)
#define SERIAL_ECHOLNPGM_P(X) do {} while (0)
#define SERIAL_CHAR(X) do { serial_output += char(X); } while (0)
#define SERIAL_EOL() do { serial_output += '\n'; } while (0)
#define CRITICAL_SECTION_START() do {} while (0)
#define CRITICAL_SECTION_END() do {} while (0)
extern bool wait_for_user;
''')
    (build / "inc/MarlinConfig.h").write_text('#include "MarlinConfigPre.h"\n')
    (build / "HAL/shared/Marduino.h").write_text('#include "../../inc/MarlinConfigPre.h"\n')
    (build / "feature/pause.h").write_text('enum { PAUSE_RESPONSE_EXTRUDE_MORE=1, PAUSE_RESPONSE_RESUME_PRINT=2 }; extern int pause_menu_response;\n')
    (build / "feature/runout.h").write_text('struct Runout { bool filament_ran_out=true; bool enabled=true; void reset() {filament_ran_out=false;} }; extern Runout runout;\n')
    (build / "gcode/queue.h").write_text('struct Queue { int calls=0; void inject_P(const char*) {++calls;} void enqueue_one_now(const char*) {++calls;} }; extern Queue queue;\n')
    (build / "main.cpp").write_text(r'''
#include <cstdlib>
#include <cassert>
#include <string>
#include "feature/host_actions.cpp"
#include "feature/host_prompt_wire.h"
#include "feature/host_prompt_wire.cpp"
std::string serial_output;
bool wait_for_user = true;
int pause_menu_response = 0;
Runout runout;
Queue queue;
extern const char M24_STR[] = "M24";
int main(int argc, char **argv) {
  if (argc == 2) {
    const std::string scenario(argv[1]);
    const HostPromptSession a = { 123, 456 }, b = { 123, 457 };
    runout.filament_ran_out = scenario == "runout" || scenario == "user_runout" || scenario == "runtime_runout" || scenario.find("screen_") == 0;
    if (scenario != "unbound" && scenario != "deferred" && scenario != "stale_deferred" && scenario != "wire" && scenario != "runtime" && scenario != "runtime_runout") assert(host_prompt_identity.bind(a));
    host_prompt_do(PROMPT_USER_CONTINUE, PSTR("Continue?"), CONTINUE_STR);
    const uint32_t first = host_prompt_identity.generation();
    if (scenario == "runtime" || scenario == "runtime_runout") {
      serial_output.clear();
      char byte;
      while (std::cin.get(byte)) {
        if (byte == '~') {
          host_prompt_wire_process();
          std::cout << serial_output;
          serial_output.clear();
          std::cout << "STATE " << wait_for_user << " " << host_prompt_protocol_busy << "\n";
        }
        else host_prompt_receive_byte(uint8_t(byte));
      }
      return 0;
    }
    if (scenario == "wire") {
      HostPromptFramer framer;
      char byte;
      while (std::cin.get(byte)) {
        HostPromptCommand command;
        const auto frame = framer.feed(uint8_t(byte), command);
        if (frame == HostPromptFramer::Result::Incomplete) continue;
        int result = -1;
        const bool parsed = frame == HostPromptFramer::Result::Command;
        if (parsed) {
          if (command.kind == HostPromptCommandKind::Bind) result = host_prompt_identity.bind(command.session) ? 0 : -2;
          else if (command.kind == HostPromptCommandKind::Respond) result = int(host_token_response(command.session, command.prompt, command.choice));
          else result = 0;
        }
        std::cout << "{\"parsed\":" << parsed << ",\"request\":" << command.request
          << ",\"result\":" << result << ",\"active\":" << host_prompt_identity.active()
          << ",\"waiting\":" << wait_for_user << "}\n";
      }
      return 0;
    }
    if (scenario.find("screen_") == 0) {
      const bool blocked = scenario.find("blocked") != std::string::npos;
      recdat.data[0] = scenario.find("runout") != std::string::npos ? 3 : 1;
      sensor_pin = blocked;
      screen_resume();
      assert(wait_for_user == blocked);
      assert(host_prompt_identity.active() == blocked);
      assert(queue.calls == 0 && thermalManager.calls == 0);
      if (!blocked) {
        wait_for_user = true;
        assert(host_token_response(a, first, 0) == HostPromptCheck::NoPrompt);
        assert(wait_for_user);
      }
      else assert(runout.filament_ran_out && runout.enabled);
    }
    else if (scenario.find("wait_") == 0) {
      idle_scenario = scenario;
      wait_for_user_response(scenario == "wait_timeout" ? 3 : 0, false);
      assert(!wait_for_user);
      if (scenario == "wait_replacement") {
        assert(host_prompt_identity.active());
        assert(host_prompt_reason == PROMPT_USER_CONTINUE);
      }
      else {
        assert(!host_prompt_identity.active());
        assert(host_prompt_reason == PROMPT_NOT_DEFINED);
        wait_for_user = true;
        assert(host_token_response(host_prompt_identity.session(), first, 0) == HostPromptCheck::NoPrompt);
        assert(wait_for_user);
      }
    }
    else if (scenario == "unbound") {
      assert(host_token_response(a, first, 0) == HostPromptCheck::NoSession);
      assert(wait_for_user);
    }
    else if (scenario == "wrong_session") {
      assert(host_token_response(b, first, 0) == HostPromptCheck::WrongSession);
      assert(wait_for_user);
    }
    else if (scenario == "replacement") {
      host_prompt_do(PROMPT_USER_CONTINUE, PSTR("Continue?"), CONTINUE_STR);
      assert(host_token_response(a, first, 0) == HostPromptCheck::StalePrompt);
      assert(wait_for_user);
      assert(host_token_response(a, host_prompt_identity.generation(), 0) == HostPromptCheck::Ready);
      assert(!wait_for_user);
    }
    else if (scenario == "duplicate") {
      assert(host_token_response(a, first, 0) == HostPromptCheck::Ready);
      wait_for_user = true; // another wait cannot be cleared by a duplicate reply
      assert(host_token_response(a, first, 0) == HostPromptCheck::NoPrompt);
      assert(wait_for_user);
    }
    else if (scenario == "assembling") {
      host_action_prompt_begin(PROMPT_USER_CONTINUE, PSTR("New"));
      host_action_prompt_button(CONTINUE_STR);
      assert(host_token_response(a, host_prompt_identity.generation(), 0) == HostPromptCheck::NotShown);
      assert(wait_for_user);
    }
    else if (scenario == "choice") {
      assert(host_token_response(a, first, 1) == HostPromptCheck::InvalidChoice);
      assert(wait_for_user && host_prompt_identity.active());
    }
    else if (scenario == "ended") {
      host_action_prompt_end();
      assert(host_prompt_reason == PROMPT_NOT_DEFINED);
      assert(host_token_response(a, first, 0) == HostPromptCheck::NoPrompt);
      assert(wait_for_user);
    }
    else if (scenario == "local") {
      assert(host_response_handler(0));
      wait_for_user = true;
      assert(host_token_response(a, first, 0) == HostPromptCheck::NoPrompt);
      assert(wait_for_user);
    }
    else if (scenario == "rebind") {
      assert(host_prompt_identity.bind(b));
      assert(host_token_response(a, first, 0) == HostPromptCheck::WrongSession);
      uint32_t current = host_prompt_identity.generation();
      assert(host_prompt_identity.bind(b));
      assert(host_prompt_identity.generation() == current);
      assert(host_token_response(b, current, 0) == HostPromptCheck::Ready);
    }
    else if (scenario == "runout") {
      host_prompt_do(PROMPT_FILAMENT_RUNOUT, PSTR("Reload"), PSTR("Purge"), CONTINUE_STR);
      assert(host_token_response(a, host_prompt_identity.generation(), 1) == HostPromptCheck::PhysicalActionRequired);
      assert(runout.enabled && runout.filament_ran_out && pause_menu_response == 0);
    }
    else if (scenario == "user_runout") {
      // Creality's nozzle-parked runout wait is advertised as USER_CONTINUE.
      assert(host_token_response(a, first, 0) == HostPromptCheck::PhysicalActionRequired);
      assert(!host_response_handler(0));
      assert(wait_for_user && host_prompt_identity.active() && runout.filament_ran_out);
    }
    else if (scenario == "unsupported") {
      host_prompt_do(PROMPT_FILAMENT_RUNOUT_REHEAT, PSTR("Reheat"), CONTINUE_STR);
      assert(host_token_response(a, host_prompt_identity.generation(), 0) == HostPromptCheck::UnsupportedReason);
      assert(host_prompt_identity.active());
    }
    else if (scenario == "reboot") {
      host_prompt_identity = HostPromptIdentity();
      host_prompt_do(PROMPT_USER_CONTINUE, PSTR("Continue?"), CONTINUE_STR);
      assert(host_token_response(a, first, 0) == HostPromptCheck::NoSession);
      assert(wait_for_user);
    }
    else if (scenario == "deferred") {
      assert(host_response_enqueue(0));
      assert(wait_for_user); // ISR-side enqueue must not clear the wait
      assert(!host_response_enqueue(1)); // never overwrite an earlier request
      host_response_process();
      assert(!wait_for_user);
      wait_for_user = true;
      host_response_process();
      assert(wait_for_user); // drained mailbox never repeats an action
      assert(!host_response_enqueue(UINT8_MAX));
    }
    else if (scenario == "stale_deferred") {
      assert(host_response_enqueue(0));
      host_prompt_do(PROMPT_USER_CONTINUE, PSTR("Replacement"), CONTINUE_STR);
      host_response_process();
      assert(wait_for_user && host_prompt_identity.active());
    }
    else if (scenario == "bound_legacy") {
      assert(host_response_enqueue(0));
      host_response_process();
      assert(wait_for_user && host_prompt_identity.active());
    }
    else return 3;
    std::cout << "{\"ok\":true}";
    return 0;
  }
  if (argc != 4) return 2;
  host_prompt_reason = static_cast<PromptReason>(std::atoi(argv[1]));
  runout.filament_ran_out = std::atoi(argv[3]);
  host_response_handler(std::atoi(argv[2]));
  std::cout << "{\"reason\":" << int(host_prompt_reason)
    << ",\"waiting\":" << wait_for_user << ",\"sensor_enabled\":" << runout.enabled
    << ",\"runout_latched\":" << runout.filament_ran_out
    << ",\"pause_response\":" << pause_menu_response << ",\"queued\":" << queue.calls << "}";
}
''')
    # Compile the actual wait implementation as well, without reproducing its
    # timeout/cleanup logic in Python. Only clock and idle side effects are stubbed.
    core = (src / "MarlinCore.cpp").read_text()
    start = core.index("  void wait_for_user_response(")
    opening = core.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (core[end] == "{") - (core[end] == "}")
        end += 1
    wait_source = core[start:end]
    wait_dependencies = r'''
using millis_t = uint32_t;
#define UNUSED(X) (void)(X)
#define KEEPALIVE_STATE(X) do {} while (0)
#define ELAPSED(NOW, DEADLINE) (int32_t((NOW) - (DEADLINE)) >= 0)
static millis_t clock_value = 100;
static std::string idle_scenario;
millis_t millis() { return clock_value; }
void idle(bool) {
  ++clock_value;
  if (idle_scenario == "wait_timeout") return;
  wait_for_user = false;
  if (idle_scenario == "wait_replacement")
    host_prompt_do(PROMPT_USER_CONTINUE, PSTR("Replacement"), CONTINUE_STR);
  if (idle_scenario == "wait_rebind")
    host_prompt_identity.bind({123, 457});
  if (idle_scenario == "wait_answered") host_response_handler(0);
}
'''
    screen = (src / "lcd/dwin/lcd_rts.cpp").read_text(errors="surrogateescape")
    resume_case = screen[screen.index("    case ResumePrintKey:"):screen.index("    case ZoffsetEnterKey:")]
    screen_dependencies = r'''
#include <cstring>
#include <cstdio>
#define FILAMENT_RUNOUT_SENSOR 1
#define READ(PIN) sensor_pin
#define sprintf_P std::sprintf
static int sensor_pin = 0;
static int change_page_font = 0, temphot = 0, Update_Time_Value = 0;
static bool sdcard_pause_check = false;
static char commandbuf[64];
static constexpr int ResumePrintKey = 1, ExchangePageBase = 0, ExchangepageAddr = 0, EXTRUDE_MINTEMP = 170;
static struct { int data[1]; } recdat;
static struct { void synchronize() {} } planner;
static struct { void start() {} } print_job_timer;
static struct {
  struct { int target = 0; } temp_hotend[1];
  int calls = 0;
  void setTargetHotend(int, int) { ++calls; }
} thermalManager;
void RTS_SndData(int, int) {}
'''
    screen_source = screen_dependencies + '\nvoid screen_resume() { switch (ResumePrintKey) {\n' + resume_case + '\n}}\n'
    main = build / "main.cpp"
    main.write_text(main.read_text().replace("int main(int argc", wait_dependencies + wait_source + screen_source + "\nint main(int argc"), errors="surrogateescape")
    compiler = shutil.which("clang++") or shutil.which("g++")
    assert compiler, "Native compiler required"
    binary = build / "actions"
    subprocess.run([compiler, "-std=gnu++14", str(build / "main.cpp"), "-o", str(binary)], check=True, capture_output=True, text=True)
    return binary

