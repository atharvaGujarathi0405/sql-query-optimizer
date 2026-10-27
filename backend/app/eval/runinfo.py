"""Environment block written at the top of every results file (CLAUDE.md rule 7).

Server settings are read LIVE from MySQL, not from compose/config, because a
config can be silently ignored (it was: see the Phase 1 commit message).
"""
import ctypes
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config import QUERY_TIMEOUT_S

_VARS = ["version", "version_comment", "innodb_buffer_pool_size", "collation_server", "character_set_server",
         "local_infile", "log_bin", "max_execution_time", "transaction_isolation", "sql_mode", "optimizer_switch"]


def _host_ram_bytes() -> int | None:
    if sys.platform == "win32":
        class MEM(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong)] + \
                       [(f"p{i}", ctypes.c_ulonglong) for i in range(6)]  # MEMORYSTATUSEX has 7 ULONGLONGs after the 2 DWORDs
        m = MEM(); m.l = ctypes.sizeof(MEM)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.total
    try:
        return int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1]) * 1024
    except OSError:
        return None


def _cpu_name() -> str:
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    return platform.processor()


def _run(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True,
                              cwd=Path(__file__).resolve().parents[3]).stdout.strip()
    except Exception:
        return None


def run_metadata(conn) -> dict:
    """`conn` is any open MySQL connection (the reader is enough)."""
    cur = conn.cursor()
    server = {}
    for v in _VARS:
        cur.execute(f"SELECT @@GLOBAL.`{v}`")
        server[v] = cur.fetchone()[0]
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mysql": server,
        "query_timeout_s": QUERY_TIMEOUT_S,
        "hardware": {
            "cpu": _cpu_name(), "logical_cores": os.cpu_count(), "host_ram_bytes": _host_ram_bytes(),
            "os": platform.platform(),
            # RAM Docker gives the MySQL container VM: what the buffer pool actually competes for.
            "docker_vm_ram_bytes": int(_run(["docker", "info", "--format", "{{.MemTotal}}"]) or 0) or None,
        },
        "software": {"python": platform.python_version(), "git_commit": _run(["git", "rev-parse", "HEAD"]),
                     "git_dirty": bool(_run(["git", "status", "--porcelain"]))},
    }
