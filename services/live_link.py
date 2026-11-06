"""Whether Framo's 3D editor is following Blender live, and when to send.

While "Live from Blender" is on, the editor asks for the latest model's
description several times a second with ``?live=1`` on the URL. Those polls
switch change tracking on (services/live_watch.py), and a send made while they
keep arriving skips Draco and decimation. The editor reads the
model over loopback, where a 19 MB file moves in milliseconds, so compressing
it only adds wait: Draco took a 126k-face mesh from 149 ms to 283 ms. Texture
shrinking stays on, because it makes the export faster rather than slower: a
4K texture written at full size took the same send from 0.38 s to 1.0 s.

Pure: no bpy, so it loads in a plain-Python test. The HTTP threads write the
poll time and the main thread reads it, which a lock keeps whole.
"""

import threading
import time
from urllib.parse import parse_qs, urlsplit

# The editor polls several times a second, and every 2 s while Blender is
# unreachable. A send more than this long after the last poll is not live: the
# editor's switch went off, or its tab closed.
LIVE_WINDOW_S = 3.0

# A change is sent once nothing else has changed for this long, so a burst of
# edits costs one export.
QUIET_S = 0.15
# A stream that never goes quiet (the timeline playing, a script) still reaches
# Framo this often.
MAX_HOLD_S = 1.0

_lock = threading.Lock()
_last_live_poll = None
# Whether the last live poll said it can take moves on their own (``moves=1``).
_takes_moves = False
# Where everything stands, and a number that changes when it does (see publish_moves).
_moves = {}
_moves_seq = 0


def is_live_poll(raw_path):
    """True for a request whose query carries ``live=1``."""
    return parse_qs(urlsplit(raw_path).query).get("live") == ["1"]


def note_poll(raw_path, now=None):
    """Record a poll from the live editor; any other request is ignored."""
    global _last_live_poll, _takes_moves
    if not is_live_poll(raw_path):
        return
    takes_moves = parse_qs(urlsplit(raw_path).query).get("moves") == ["1"]
    with _lock:
        _last_live_poll = time.monotonic() if now is None else now
        _takes_moves = takes_moves


def takes_moves():
    """True when the live editor can apply moves; an older one only reads whole models."""
    with _lock:
        return _takes_moves


def publish_moves(objects):
    """Say where everything stands: object name to its 16-number matrix.

    The whole table every time, never a list of changes, so a reader can apply
    it twice, or after a whole model that was exported a moment earlier, and end
    up right. The number moves only when the table does.
    """
    global _moves, _moves_seq
    with _lock:
        if objects != _moves:
            _moves = objects
            _moves_seq += 1


def moves_seq():
    with _lock:
        return _moves_seq


def moves():
    """The table and its number, as the route answers them."""
    with _lock:
        return {"seq": _moves_seq, "objects": _moves}


def is_live(now=None):
    """True while the live editor has polled within the window."""
    with _lock:
        last = _last_live_poll
    if last is None:
        return False
    now = time.monotonic() if now is None else now
    return 0 <= now - last <= LIVE_WINDOW_S


class SendPacer:
    """When a tracked change should go out.

    An export runs on Blender's main thread, so it holds the interface still
    for as long as it takes. The next one waits at least that long again:
    however heavy the scene, the person keeps at least half of Blender's time.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._first_change = None
        self._last_change = None
        self._earliest = 0.0

    @property
    def pending(self):
        return self._first_change is not None

    def note_change(self, now):
        if self._first_change is None:
            self._first_change = now
        self._last_change = now

    def due(self, now):
        if self._first_change is None or now < self._earliest:
            return False
        return now - self._last_change >= QUIET_S or now - self._first_change >= MAX_HOLD_S

    def note_sent(self, now, took):
        """A send finished at ``now`` after ``took`` seconds; what it covered is no longer pending."""
        self._first_change = None
        self._last_change = None
        self._earliest = now + took


def reset():
    """Forget every poll (tests, and the server stopping)."""
    global _last_live_poll, _takes_moves, _moves
    with _lock:
        _last_live_poll = None
        _takes_moves = False
        _moves = {}
