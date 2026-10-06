"""Where the app's asyncio tasks wait: ``python -m asyncio pstree`` of the app.

Shows which coroutine a hanging request waits in (a plugin's browser fetch, a
semaphore, a resolver). Read-only: the remote-debugging interface of Python
3.14 reads the process, it does not stop it. ``ps`` style arguments pass
through, e.g. ``probe tasks ps`` for a flat task table.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def app_pid() -> str | None:
    """PID of the app process (``python -m scavengarr.interfaces.cli``)."""
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if b"scavengarr.interfaces.cli" in (proc / "cmdline").read_bytes():
                return proc.name
        except OSError:
            continue
    return None


pid = app_pid()
if pid is None:
    sys.exit("app process not found")
view = sys.argv[1] if len(sys.argv) > 1 else "pstree"
if view not in ("pstree", "ps"):
    sys.exit(f"unknown view {view!r}: pstree or ps")
done = subprocess.run(
    [sys.executable, "-m", "asyncio", view, pid],
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
)
print(done.stdout or done.stderr, end="")
sys.exit(done.returncode)
