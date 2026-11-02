"""Whether Framo's 3D editor is following Blender live.

While "Live from Blender" is on, the editor asks for the latest model's
description every half second with ``?live=1`` on the URL. A send made while
those polls keep arriving skips Draco and decimation. The editor reads the
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

# The editor polls every 0.5 s, and every 2 s while Blender is unreachable. A
# send more than this long after the last poll is not live: the editor's
# switch went off, or its tab closed.
LIVE_WINDOW_S = 3.0

_lock = threading.Lock()
_last_live_poll = None


def is_live_poll(raw_path):
    """True for a request whose query carries ``live=1``."""
    return parse_qs(urlsplit(raw_path).query).get("live") == ["1"]


def note_poll(raw_path, now=None):
    """Record a poll from the live editor; any other request is ignored."""
    global _last_live_poll
    if not is_live_poll(raw_path):
        return
    with _lock:
        _last_live_poll = time.monotonic() if now is None else now


def is_live(now=None):
    """True while the live editor has polled within the window."""
    with _lock:
        last = _last_live_poll
    if last is None:
        return False
    now = time.monotonic() if now is None else now
    return 0 <= now - last <= LIVE_WINDOW_S


def reset():
    """Forget every poll (tests, and the server stopping)."""
    global _last_live_poll
    with _lock:
        _last_live_poll = None
