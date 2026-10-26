#!/usr/bin/env python3
"""Compile a supplied HeaterWatch template before/after the zero-sentinel fix.

No network access, firmware modification, full firmware build or hardware IO.
The periods and hysteresis below are synthetic test parameters, not a board config.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def extract(text):
    start = text.index('template <int INCREASE, int HYSTERESIS, millis_t PERIOD>')
    opening = text.index('{', start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + ';'


PREAMBLE = r'''
#include <cstdint>
#include <iostream>
#include <algorithm>
using millis_t=uint32_t;
using celsius_t=int16_t;
uint32_t tick=0;
millis_t millis(){return tick;}
#define ELAPSED(a,b) (int32_t((a)-(b))>=0)
#define SEC_TO_MS(x) ((x)*1000UL)
#define _MIN(a,b) std::min(a,b)
'''
TAIL = r'''
template<int PERIOD> void test(){
 HeaterWatch<2,4,PERIOD> watch{};
 uint32_t duration=PERIOD*1000;
 tick=uint32_t(0-duration);watch.restart(20,200);
 bool armed=watch.next_ms!=0;
 tick=0;bool zero=watch.elapsed();tick=1;bool one=watch.elapsed();
 tick=1000;watch.restart(20,200);
 tick=1000+duration-1;bool early=watch.elapsed();
 ++tick;bool due=watch.elapsed();
 bool rise=watch.check(22),stall=watch.check(21);
 watch.restart(20,0);bool off=watch.next_ms==0;
 tick=UINT32_MAX-1000;watch.restart(20,200);
 uint32_t deadline=watch.next_ms;
 tick=deadline-1;bool wrap_early=watch.elapsed();
 tick=deadline;bool wrap_due=watch.elapsed();
 std::cout<<"{\"period\":"<<PERIOD<<",\"zero_armed\":"<<armed
 <<",\"zero_elapsed_at_zero\":"<<zero<<",\"zero_elapsed_at_one\":"<<one
 <<",\"early\":"<<early<<",\"due\":"<<due<<",\"rise\":"<<rise
 <<",\"stall\":"<<stall<<",\"off\":"<<off
 <<",\"wrap_early\":"<<wrap_early<<",\"wrap_due\":"<<wrap_due<<"}";
}
int main(){std::cout<<"[";test<40>();std::cout<<",";test<180>();std::cout<<"]\n";}
'''


def check(header):
    source = Path(header).read_bytes()
    snippet = extract(source.decode())
    assignment = 'next_ms = millis() + SEC_TO_MS(PERIOD);'
    if snippet.count(assignment) != 1 or 'if (!next_ms)' in snippet:
        raise ValueError('unexpected or already corrected source; review manually')
    candidate = snippet.replace(assignment, assignment + '\n        if (!next_ms) next_ms = 1;')
    results = {}
    with tempfile.TemporaryDirectory(prefix='heater-watch-check-') as directory:
        for name, code in [('original', snippet), ('candidate', candidate)]:
            cpp = Path(directory) / f'{name}.cpp'
            binary = Path(directory) / name
            cpp.write_text(PREAMBLE + code + TAIL)
            subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(binary)], check=True, capture_output=True, text=True)
            results[name] = json.loads(subprocess.check_output([str(binary)], text=True, timeout=5))
    for name, rows in results.items():
        for row in rows:
            assert row['zero_armed'] == row['zero_elapsed_at_one'] == (name == 'candidate'), (name, row)
            assert row['zero_elapsed_at_zero'] == row['early'] == row['stall'] == row['wrap_early'] == 0
            assert row['due'] == row['rise'] == row['off'] == row['wrap_due'] == 1
    return dict(header_sha256=hashlib.sha256(source).hexdigest(),
                template_sha256=hashlib.sha256(snippet.encode()).hexdigest(),
                result='reproduced_and_candidate_passed', synthetic_parameters=True,
                full_firmware_build=False, physical_test=False, observations=results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--header', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = check(args.header)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'result': report['result']}))
