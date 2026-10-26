"""Actual firmware mailbox code with deterministic producer/consumer schedules.

Native calls are sequential; IRQ exclusion is an integration prerequisite,
not something this harness can prove.
"""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_prompt_mailbox_preserves_frames_and_isolates_ports(tmp_path):
    for name in ('host_prompt_identity.h', 'host_prompt_wire.h', 'host_prompt_inbox.h'):
        shutil.copyfile(ROOT / 'firmware/Marlin/src/feature' / name, tmp_path / name)
    source = tmp_path / 'inbox.cpp'
    source.write_text(r'''
#include <cassert>
#include <string>
#include "host_prompt_inbox.h"
static void feed(HostPromptInbox &inbox, const std::string &line) {
  for (unsigned char c : line) inbox.receive(c);
}
static uint32_t consume(HostPromptInbox &inbox) {
  HostPromptInbox::Frame frame;
  assert(inbox.take(frame) && !frame.invalid);
  HostPromptCommand command;
  assert(HostPromptWire::parse(frame.line, frame.length, command));
  return command.request;
}
int main() {
  static_assert(sizeof(HostPromptInbox) <= 144, "mailbox memory must stay bounded");
  const std::string first = "M876 V1 I Q00000001\n";
  const std::string second = "M876 V1 I Q00000002\n";
  HostPromptInbox inbox, other;
  HostPromptInbox::Frame frame;
  // Partial input cannot appear as a complete command at any split boundary.
  for (size_t split = 0; split < first.size(); ++split) {
    inbox.reset();
    feed(inbox, first.substr(0, split));
    assert(!inbox.take(frame));
    feed(inbox, first.substr(split));
    assert(consume(inbox) == 1);
    assert(!inbox.take(frame) && !inbox.take_overflow());
  }
  // Interleaved independent ports do not complete each other's partial frames.
  feed(inbox, first.substr(0, 12));
  feed(other, first.substr(12));
  assert(!inbox.take(frame) && !other.take(frame));
  feed(inbox, first.substr(12));
  assert(consume(inbox) == 1);
  feed(other, second);
  assert(consume(other) == 2);
  // An occupied mailbox preserves the first request and latches loss evidence.
  feed(inbox, first + second + second);
  assert(inbox.take_overflow() && !inbox.take_overflow());
  assert(consume(inbox) == 1);
  assert(!inbox.take(frame));
  feed(inbox, second);
  assert(consume(inbox) == 2);
  // Normal G-code/legacy emergency commands never fill the protocol mailbox.
  feed(inbox, "G1 X1\nM112\nM410\nM108\nM876 S0\nM876 V10 I Q00000001\n");
  assert(!inbox.take(frame) && !inbox.take_overflow());
  // Oversized or control-containing candidates must not become a valid prefix.
  feed(inbox, first.substr(0, first.size()-1) + std::string(100000, 'x') + "\n");
  assert(inbox.take(frame) && frame.invalid);
  feed(inbox, first.substr(0, 8) + std::string(1, '\0') + first.substr(8));
  assert(inbox.take(frame) && frame.invalid);
  feed(inbox, first);
  assert(consume(inbox) == 1);
  // Reconnection reset discards both partial bytes and queued commands.
  feed(inbox, first + second.substr(0, 12));
  inbox.reset();
  assert(!inbox.take(frame) && !inbox.take_overflow());
  feed(inbox, second.substr(12));
  assert(!inbox.take(frame));
  feed(inbox, second);
  assert(consume(inbox) == 2);
  // Consumer drains while a subsequent producer frame is still assembling.
  for (size_t split = 0; split < second.size(); ++split) {
    inbox.reset();
    feed(inbox, first + second.substr(0, split));
    assert(consume(inbox) == 1);
    feed(inbox, second.substr(split));
    assert(consume(inbox) == 2);
    assert(!inbox.take_overflow());
  }
}
''')
    binary = tmp_path / 'inbox'
    subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++14',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(source), '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)
