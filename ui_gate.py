"""Nonblocking per-session gate: separate visitors run concurrently; a session cannot race itself."""
from threading import Lock

_registry_lock = Lock()
_active_sessions = {}


def run_session(request, action, on_busy):
    key = getattr(request, 'session_hash', None)
    if not key:
        return action()
    with _registry_lock:
        lock = _active_sessions.setdefault(key, Lock())
        acquired = lock.acquire(blocking=False)
    if not acquired:
        return on_busy()
    try:
        return action()
    finally:
        with _registry_lock:
            lock.release()
            _active_sessions.pop(key, None)
