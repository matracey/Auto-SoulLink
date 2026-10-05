from __future__ import annotations

import os
import tempfile


def writable_directory(path: str) -> tuple[bool, str]:
    try:
        os.makedirs(path, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".slink-ready-", dir=path)
        os.close(fd)
        os.unlink(probe)
        return True, ""
    except OSError as exc:
        return False, str(exc)
