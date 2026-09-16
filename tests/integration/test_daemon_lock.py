import subprocess
import sys
from pathlib import Path

import pytest

from quant_platform.core.daemon import AlreadyRunningError, InstanceLock


def test_lock_excludes_real_subprocess_and_is_acquirable_after_release(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "subprocess.lock"
    script = """
import sys
from quant_platform.core.daemon import InstanceLock

with InstanceLock(sys.argv[1]):
    print("ready", flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(AlreadyRunningError):
            InstanceLock(lock_path).acquire()

        assert child.stdin is not None
        child.stdin.write("release\n")
        child.stdin.flush()
        _, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr

        with InstanceLock(lock_path):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
