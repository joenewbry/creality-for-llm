"""Actual routing and mailbox headers: token bytes cannot create ordinary acks."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_token_frames_are_diverted_without_changing_ordinary_stream(tmp_path):
    for name in ('host_prompt_rx_router.h', 'host_prompt_inbox.h', 'host_prompt_wire.h', 'host_prompt_identity.h'):
        shutil.copyfile(ROOT / 'firmware/Marlin/src/feature' / name, tmp_path / name)
    source = tmp_path / 'route.cpp'
    source.write_text(r'''
#include <cassert>
#include <string>
#include "host_prompt_rx_router.h"
#include "host_prompt_inbox.h"
int main() {
  static_assert(sizeof(HostPromptRxRouter) <= 4, "bounded IRQ state");
  const std::string token = "M876 V1 I Q00000001\n";
  const std::string ordinary = "G1 X1\nM112\nM410\nM108\nM876 S0\nN12 M876 V1 I Q00000001*0\nM876 V10 I Q00000001\n;M876 V1 I Q00000001\n";
  for (size_t split=0; split<=token.size(); ++split) {
    HostPromptRxRouter router; HostPromptInbox inbox;
    std::string out;
    const auto emit=[&](uint8_t c) { out += char(c); };
    const auto feed=[&](const std::string &s) { for (uint8_t c:s) { inbox.receive(c); router.receive(c,emit); } };
    feed(ordinary); assert(out==ordinary);
    feed(token.substr(0,split)); feed(token.substr(split));
    assert(out==ordinary);
    HostPromptInbox::Frame frame; assert(inbox.take(frame) && !frame.invalid);
    HostPromptCommand cmd; assert(HostPromptWire::parse(frame.line,frame.length,cmd) && cmd.request==1);
    feed("G1 X2\n"); assert(out==ordinary+"G1 X2\n");
  }
  // Every partial prefix followed by a mismatch is preserved exactly.
  const std::string prefix="M876 V1";
  for (size_t n=0;n<=prefix.size();++n) {
    HostPromptRxRouter router; std::string out, input=prefix.substr(0,n)+"?\nG1 X3\n";
    for(uint8_t c:input) router.receive(c,[&](uint8_t b){out+=char(b);});
    assert(out==input);
  }
  HostPromptRxRouter router; HostPromptInbox inbox; std::string out;
  const auto feed=[&](const std::string &s) { for(uint8_t c:s) {
    inbox.receive(c); router.receive(c,[&](uint8_t b){out+=char(b);});
  }};
  // Malformed and oversized recognized candidates stay out of normal G-code.
  feed("M876 V1\n"); HostPromptInbox::Frame f; assert(inbox.take(f));
  feed("M876 V1 " + std::string(100000,'x') + "\n");
  assert(inbox.take(f) && f.invalid && out.empty());
  feed(token+token); assert(inbox.take_overflow() && inbox.take(f) && out.empty());
  feed("M876 V1 I Q00000001\r\n"); assert(inbox.take(f) && f.invalid && out.empty());
  feed("\r\nG1 X4\n"); assert(out=="\r\nG1 X4\n");
}
''')
    binary = tmp_path / 'route'
    subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++14', str(source), '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)
