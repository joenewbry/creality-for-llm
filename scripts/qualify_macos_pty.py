#!/usr/bin/env python3
"""Probe Mac software-terminal exclusion. No physical serial path is accepted.

The result characterizes a PTY only. USB serial drivers have separate open paths;
neither success nor failure qualifies access to a printer.
"""
import errno
import fcntl
import json
import os
import platform
import pty
import subprocess
import sys
import termios


def probe():
    if sys.platform != 'darwin':
        raise RuntimeError('Mac software-terminal probe requires macOS')
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    os.close(slave)
    owner = None
    exclusive = False
    try:
        flags = os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW
        owner = os.open(path, flags)
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(owner, termios.TIOCEXCL)
        exclusive = True
        code = '''import errno,fcntl,json,os,sys
fd=None
result={"open_errno":None,"flock_errno":None}
try:
 try:fd=os.open(sys.argv[1],os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK|os.O_CLOEXEC|os.O_NOFOLLOW)
 except OSError as error:result["open_errno"]=error.errno
 if fd is not None:
  try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError as error:result["flock_errno"]=error.errno
finally:
 if fd is not None:os.close(fd)
print(json.dumps(result))
'''
        child = subprocess.run([sys.executable, '-c', code, path], check=True,
                               capture_output=True, timeout=2)
        result = json.loads(child.stdout)
        return dict(scope='software_terminal_only', platform=platform.platform(),
                    kernel=platform.version(), python=platform.python_version(),
                    uid=os.getuid(), child=result,
                    kernel_excluded_competing_open=result['open_errno']==errno.EBUSY,
                    physical_serial_access=False, printer_commands_sent=0,
                    physical_driver_qualified=False)
    finally:
        try:
            if owner is not None:
                try:
                    if exclusive:fcntl.ioctl(owner, termios.TIOCNXCL)
                finally:os.close(owner)
        finally:os.close(master)


if __name__ == '__main__':
    print(json.dumps(probe(), indent=2))
