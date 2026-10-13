"""One lock per state directory, held by every command that changes Flight Check state."""

import contextlib
import os
import stat
import time

LOCK_NAME = "state.lock"
WAIT_SECONDS = 30
STALE_SECONDS = 600


class LockError(Exception):
    pass


def lock_path(directory):
    return os.path.join(os.path.abspath(directory), LOCK_NAME)


@contextlib.contextmanager
def locked(directory, wait=None):
    """Hold <directory>/state.lock from a command's first read to its last write.

    Two commands updating the same state at once would otherwise each write their own copy, and one set of
    updates would be lost. A lock file older than STALE_SECONDS is left over from a crash and is removed.
    """
    wait = WAIT_SECONDS if wait is None else wait
    lock = lock_path(directory)
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                info = os.lstat(lock)
            except FileNotFoundError:
                continue  # released between the two calls: try again at once
            if not stat.S_ISREG(info.st_mode):
                raise LockError("%s is not a regular file (a symbolic link or something else was put there); "
                                "remove it after checking that no Flight Check command is running" % lock)
            if time.time() - info.st_mtime > STALE_SECONDS:
                try:
                    os.unlink(lock)
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise LockError("Flight Check state in %s is in use by another Flight Check command (lock file %s); "
                                "try again, or delete the lock file if no Flight Check command is running"
                                % (os.path.dirname(lock), lock))
            time.sleep(0.1)
            continue
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        break
    try:
        yield
    finally:
        try:
            os.unlink(lock)
        except FileNotFoundError:
            pass
