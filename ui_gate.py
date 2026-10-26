"""Nonblocking per-session gate: separate visitors run concurrently; a session cannot race itself."""
from threading import Lock
from contextlib import contextmanager

_registry_lock = Lock()
_active_sessions = {}


@contextmanager
def session_slot(request):
    key = getattr(request, 'session_hash', None)
    if not key:
        yield True
        return
    with _registry_lock:
        lock = _active_sessions.setdefault(key, Lock())
        acquired = lock.acquire(blocking=False)
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        with _registry_lock:
            lock.release()
            _active_sessions.pop(key, None)


def run_session(request, action, on_busy):
    with session_slot(request) as acquired:
        return action() if acquired else on_busy()
