"""An OS lock prevents duplicate runners and releases automatically on crash."""

import os
from contextlib import contextmanager


@contextmanager
def episode_lease(path):
    file = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            file.seek(0)
            if not file.read(1):
                file.write(b"0")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        file.close()
        raise ValueError("episode_already_running") from error
    try:
        yield
    finally:
        file.close()
