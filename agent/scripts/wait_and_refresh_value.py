"""Wait for a cache-refresh process, then run the atomic Value snapshot refresh."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def process_alive(pid: int) -> bool:
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    while process_alive(args.wait_pid):
        time.sleep(5)
    script = Path(__file__).with_name("refresh_value_snapshot.py")
    return subprocess.run([sys.executable, str(script), "--as-of", args.as_of], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
