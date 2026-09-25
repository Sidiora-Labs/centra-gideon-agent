"""Lifetime supervisor inside the existing sandbox and resource ceiling shim."""
import ctypes
import fcntl
import termios
import os
import signal
import subprocess
import sys
import time


def main():
    parent = int(sys.argv[1])
    def terminate(*_):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.killpg(os.getpgrp(), signal.SIGTERM)
        time.sleep(.3)
        os.killpg(os.getpgrp(), signal.SIGKILL)
    signal.signal(signal.SIGTERM, terminate)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM) != 0 or os.getppid() != parent:
        terminate()
    descriptors=tuple(int(value) for value in sys.argv[2].split(',') if value)
    if os.isatty(0):fcntl.ioctl(0,termios.TIOCSCTTY,0)
    process = subprocess.Popen(sys.argv[3:],pass_fds=descriptors)
    raise SystemExit(process.wait())


if __name__ == '__main__':
    main()
