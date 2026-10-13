import threading
from contextlib import contextmanager
from datetime import datetime

# Thread-safe globals
_state_lock = threading.Lock()

# Private storage
_update_state = {
    "checking": False,
    "update_available": False,
    "latest_version": None,
    "update_info": None,
    "downloading": False,
    "installing": False,
    "download_progress": 0.0,
    "download_error": None,
    "pending_restart": False,
    "last_check_time": None
}

_framo_user_info = {
    "name": None,
    "email": None,
    "last_connected": None
}

_material_expanded_states = {}

# "Allow Framo to control Blender". Lives here, not in a saved property, so it
# is off on every Blender start and never travels inside a .blend file; and
# because the HTTP thread reads it, which must not touch bpy.
_control_state = {
    "enabled": False,
    "last_op": None,
    "last_at": None,      # datetime of the last command Blender ran
    "last_ok": None,
    "last_error": None,
}

# Set from the HTTP thread when something the panel shows has changed; the
# main-thread timer redraws and clears it. bpy is not called from the HTTP
# thread, not even to register a timer.
_redraw_requested = threading.Event()

@contextmanager
def safe_update_state_access():
    """Thread-safe access to update state"""
    with _state_lock:
        yield _update_state

def get_update_state_copy():
    """Get a copy of update state without lock (for reading only if consistency isn't critical)"""
    with _state_lock:
        return _update_state.copy()

@contextmanager
def safe_user_info_access():
    """Thread-safe access to user info"""
    with _state_lock:
        yield _framo_user_info

def get_user_info_copy():
    with _state_lock:
        return _framo_user_info.copy()

@contextmanager
def safe_material_states_access():
    """Thread-safe access to material expanded states"""
    with _state_lock:
        yield _material_expanded_states


def is_control_enabled():
    with _state_lock:
        return _control_state["enabled"]

def set_control_enabled(enabled):
    with _state_lock:
        _control_state["enabled"] = bool(enabled)

def get_control_state_copy():
    with _state_lock:
        return _control_state.copy()

def record_control_command(op, ok, error=None):
    """Remember the last command Blender ran, for the panel's status line."""
    with _state_lock:
        _control_state["last_op"] = op
        _control_state["last_at"] = datetime.now()
        _control_state["last_ok"] = ok
        _control_state["last_error"] = error

def request_redraw():
    _redraw_requested.set()

def take_redraw_request():
    """True once per request_redraw(); called from the main thread."""
    if _redraw_requested.is_set():
        _redraw_requested.clear()
        return True
    return False
