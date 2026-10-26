"""Extract the actual STM32 UART receive method; peripherals are native stubs."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_primary_uart_only_and_full_ring_buffer(tmp_path):
    text = (ROOT / 'firmware/Marlin/src/HAL/STM32/MarlinSerial.cpp').read_text()
    start = text.index('void MarlinSerial::_rx_complete_irq(')
    opening = text.index('{', start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    method = text[start:end]
    shutil.copyfile(ROOT / 'firmware/Marlin/src/feature/host_prompt_rx_router.h', tmp_path / 'host_prompt_rx_router.h')
    code = r'''
#include <cstdint>
#include <cassert>
#include <string>
#include "host_prompt_rx_router.h"
#define ENABLED(V) V
#define LLM_PROMPT_PROTOCOL 1
#define EMERGENCY_PARSER 1
#define SERIAL_RX_BUFFER_SIZE 8
using rx_buffer_index_t = unsigned;
struct serial_t { unsigned rx_head=0, rx_tail=0; uint8_t rx_buff[8]={}; };
struct MarlinSerial { void _rx_complete_irq(serial_t *obj); };
struct MSerialT : MarlinSerial { int emergency_state=0; };
MSerialT primary, screen;
#define MYSERIAL1 primary
uint8_t next_byte = 0;
bool read_error = false;
int host_calls = 0, emergency_calls = 0;
int uart_getc(serial_t*, unsigned char *out) { *out=next_byte; return read_error ? -1 : 0; }
void host_prompt_receive_byte(uint8_t c) { assert(c == next_byte); ++host_calls; }
struct { void update(int &, uint8_t c) { assert(c == next_byte); ++emergency_calls; } } emergency_parser;
'''
    code += method + r'''
int main() {
  serial_t host, lcd;
  next_byte='G'; primary._rx_complete_irq(&host);
  assert(host_calls == 1 && emergency_calls == 1 && host.rx_head == 1 && host.rx_buff[0] == 'G');
  screen._rx_complete_irq(&lcd);
  assert(host_calls == 1 && emergency_calls == 2 && lcd.rx_head == 1);
  host.rx_head=7; host.rx_tail=0; // full normal receive ring
  primary._rx_complete_irq(&host);
  assert(host_calls == 2 && emergency_calls == 3 && host.rx_head == 7);
  read_error=true; primary._rx_complete_irq(&host);
  assert(host_calls == 2 && emergency_calls == 3);
  read_error=false;
  host.rx_head=host.rx_tail=0;
  next_byte='\n'; primary._rx_complete_irq(&host); // finish the preceding ordinary line
  host.rx_tail=host.rx_head;
  const std::string token="M876 V1 I Q00000001\n";
  const auto head=host.rx_head;
  const int calls=host_calls, emergency_before=emergency_calls;
  for(unsigned char c:token) { next_byte=c; primary._rx_complete_irq(&host); }
  assert(host.rx_head==head); // no ordinary queue input, therefore no extra ordinary ok
  assert(host_calls==calls+int(token.size()) && emergency_calls==emergency_before+int(token.size()));
  // Screen traffic is never diverted, and does not enter the host mailbox.
  std::string screen_bytes;
  lcd.rx_head=lcd.rx_tail=0;
  for(unsigned char c:token) {
    next_byte=c; screen._rx_complete_irq(&lcd);
    while(lcd.rx_tail!=lcd.rx_head) {
      screen_bytes+=char(lcd.rx_buff[lcd.rx_tail]); lcd.rx_tail=(lcd.rx_tail+1)%8;
    }
  }
  assert(screen_bytes==token && host_calls==calls+int(token.size()));
  host.rx_head=7; host.rx_tail=0;
  for(unsigned char c:token) { next_byte=c; primary._rx_complete_irq(&host); }
  assert(host.rx_head==7 && host_calls==calls+2*int(token.size()));
}
'''
    source = tmp_path / 'uart.cpp'
    source.write_text(code)
    binary = tmp_path / 'uart'
    subprocess.run([shutil.which('clang++') or shutil.which('g++'), '-std=c++14', str(source), '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, capture_output=True)
