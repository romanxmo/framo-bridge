"""The queue between the HTTP thread and Blender's main thread.

bpy is not thread-safe, so the server never runs an operation itself: it
queues the command and waits on an Event; ``process_pending_jobs`` drains the
queue on the main thread, from a ``bpy.app.timers`` timer in a normal session,
or called directly by a script in background mode, where timers never fire.

A command that times out is marked abandoned; if Blender has not started it by
then, it never runs, so a late Blender cannot act on an answer nobody waits for.
"""

import queue
import threading
import traceback
from contextlib import contextmanager

import bpy

from . import ops, render
from .scene_builder import BuildError
from ..utils.thread_safety import record_control_command, take_redraw_request

TIMER_INTERVAL = 0.1

_queue = queue.Queue()
_lock = threading.Lock()


class _Pending:
    __slots__ = ("id", "op", "payload", "event", "response", "abandoned", "started")

    def __init__(self, command_id, op, payload):
        self.id = command_id
        self.op = op
        self.payload = payload
        self.event = threading.Event()
        self.response = None
        self.abandoned = False
        self.started = False


def submit(command_id, op, payload, timeout=None):
    """Queue a command and wait for Blender's answer. Called from the HTTP thread."""
    if op not in ops.OPS:
        return {"ok": False, "error": f"unknown op {op!r}; known: {', '.join(ops.OPS)}"}
    pending = _Pending(command_id, op, payload)
    _queue.put(pending)
    if pending.event.wait(ops.OP_TIMEOUTS[op] if timeout is None else timeout):
        return pending.response
    with _lock:
        if not pending.started:
            pending.abandoned = True
    if pending.event.is_set():
        # Finished in the instant between the wait running out and the lock.
        return pending.response
    return {"ok": False, "error": "timeout"}


def _run(op, payload):
    try:
        return {"ok": True, "result": ops.OPS[op](payload)}
    except BuildError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@contextmanager
def main_window_context():
    """Give an op the context a button press would have.

    A timer runs with no window in its context, so ``bpy.context.view_layer``
    and operators that poll for a window fail there. Background Blender has
    no windows at all, and needs no override.
    """
    wm = bpy.context.window_manager
    window = wm.windows[0] if wm is not None and len(wm.windows) > 0 else None
    if window is None or bpy.context.window is not None:
        yield
        return
    override = {"window": window, "screen": window.screen}
    area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None)
    if area is not None:
        override["area"] = area
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is not None:
            override["region"] = region
    with bpy.context.temp_override(**override):
        yield


def _redraw_panels():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def process_pending_jobs():
    """Run every queued command, then (on an otherwise idle tick) one queued render.

    Main thread only. Each command's failure becomes its own error answer;
    nothing raised here escapes into the timer.
    """
    handled = 0
    while True:
        try:
            pending = _queue.get_nowait()
        except queue.Empty:
            break
        with _lock:
            if pending.abandoned:
                continue
            pending.started = True
        with main_window_context():
            pending.response = _run(pending.op, pending.payload)
        record_control_command(pending.op, pending.response["ok"], pending.response.get("error"))
        pending.event.set()
        handled += 1

    # A render blocks the main thread for its whole length, so it only starts
    # on a tick that answered nothing: the render_start reply is out first.
    rendered = False
    if handled == 0:
        with main_window_context():
            rendered = render.run_next_render()

    if handled or rendered or take_redraw_request():
        _redraw_panels()


def _timer():
    try:
        process_pending_jobs()
    except Exception:
        traceback.print_exc()
    return TIMER_INTERVAL


def register():
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TIMER_INTERVAL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    # Anyone still waiting gets an answer instead of a hung request.
    while True:
        try:
            pending = _queue.get_nowait()
        except queue.Empty:
            break
        pending.response = {"ok": False, "error": "the Framo Bridge add-on was disabled"}
        pending.event.set()
    render.forget_all_jobs()
