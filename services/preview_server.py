import json
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bpy

from . import request_guard
from ..control import commands, render
from ..control.ops import OPS, bridge_version
from ..utils.logging_config import get_logger
from ..utils.thread_safety import (
    is_control_enabled,
    request_redraw,
    safe_user_info_access,
)

log = get_logger()

# Global server instance
server_instance = None
server_thread = None

PROTOCOL_VERSION = 1
MAX_COMMAND_BYTES = 2 * 1024 * 1024
MAX_SMALL_JSON_BYTES = 64 * 1024

_RENDER_ROUTE = re.compile(r"/render/([0-9a-f]{32})(/file)?")

# Read once on the main thread at start; the HTTP threads never call into bpy.
_blender_version = None


class GLBRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress console output
        pass

    # -- guard and responses ----------------------------------------------

    def _guard(self):
        """Refuse before any route work unless the request is from Framo, to this server.

        See services/request_guard.py for why this is a server-side decision.
        """
        decision = request_guard.decide(
            self.command, self.path, self.headers.get("Origin"), self.headers.get("Host"))
        self._cors_origin = decision.cors_origin
        if not decision.allowed:
            log.info(f"Refused {self.command} {request_guard.route_path(self.path)}: {decision.reason}")
            self._respond(403)
            return False
        return True

    def _respond(self, status, body=b"", content_type=None, headers=None):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        cors_origin = getattr(self, "_cors_origin", None)
        if cors_origin:
            # The exact origin, never "*": only Framo may read the answer.
            self.send_header("Access-Control-Allow-Origin", cors_origin)
        self.send_header("Vary", "Origin")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status, obj):
        self._respond(status, json.dumps(obj).encode(), "application/json")

    def _read_body(self, max_bytes):
        """The request body, or None after answering 411/413 (the body is never read then)."""
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.strip().isdigit():
            self._json(411, {"ok": False, "error": "Content-Length required"})
            return None
        length = int(raw)
        if max_bytes is not None and length > max_bytes:
            self._json(413, {"ok": False, "error": f"body larger than {max_bytes} bytes"})
            return None
        return self.rfile.read(length)

    # -- methods ----------------------------------------------------------

    def do_OPTIONS(self):
        if not self._guard():
            return
        self._respond(204, headers={
            "Access-Control-Allow-Methods": request_guard.ALLOWED_METHODS,
            "Access-Control-Allow-Headers": request_guard.ALLOWED_HEADERS,
            # Chrome's private/local network access preflight.
            "Access-Control-Allow-Private-Network": "true",
            "Access-Control-Max-Age": "600",
        })

    def do_GET(self):
        if not self._guard():
            return
        path = request_guard.route_path(self.path)
        if path == '/ping':
            self._respond(200, b'pong', 'text/plain')
        elif path == '/latest-model':
            if hasattr(self.server, 'latest_glb'):
                self._respond(200, self.server.latest_glb, 'model/gltf-binary')
            else:
                self._respond(404, b'No model available')
        elif path == '/latest-model-info':
            if hasattr(self.server, 'latest_metadata'):
                self._json(200, self.server.latest_metadata)
            else:
                self._respond(404, b'No model metadata available')
        elif path == '/user-info':
            # Return current connected user info
            with safe_user_info_access() as user_info:
                body = json.dumps(user_info).encode()
            self._respond(200, body, 'application/json')
        elif path == '/bridge-info':
            self._json(200, {
                "protocol": PROTOCOL_VERSION,
                "bridgeVersion": bridge_version(),
                "blenderVersion": _blender_version,
                "controlEnabled": is_control_enabled(),
            })
        else:
            match = _RENDER_ROUTE.fullmatch(path)
            if match:
                self._render_route(match.group(1), bool(match.group(2)))
            else:
                self._respond(404)

    def do_POST(self):
        if not self._guard():
            return
        path = request_guard.route_path(self.path)
        if path == '/upload-model':
            try:
                glb_data = self._read_body(None)
                if glb_data is None:
                    return

                # Check if metadata is sent as header
                metadata_header = self.headers.get('X-Model-Metadata')

                if metadata_header:
                    # Metadata sent as header, body is just GLB
                    metadata = json.loads(metadata_header)

                    # Store GLB data and metadata
                    self.server.latest_glb = glb_data
                    self.server.latest_metadata = metadata
                else:
                    # Legacy format: just GLB data
                    self.server.latest_glb = glb_data
                    # Create minimal metadata
                    self.server.latest_metadata = {
                        "filename": "model.glb",
                        "size": len(glb_data),
                        "size_mb": f"{len(glb_data)/(1024*1024):.2f}",
                        "timestamp": None
                    }

                self._json(200, {"success": True})
            except Exception as e:
                self._respond(500, str(e).encode())

        elif path == '/connect-user':
            try:
                post_data = self._read_body(MAX_SMALL_JSON_BYTES)
                if post_data is None:
                    return
                user_data = json.loads(post_data)
                name = user_data.get('name') if isinstance(user_data, dict) else None
                email = user_data.get('email') if isinstance(user_data, dict) else None

                with safe_user_info_access() as user_info:
                    user_info['name'] = name if isinstance(name, str) else None
                    user_info['email'] = email if isinstance(email, str) else None
                    user_info['last_connected'] = datetime.now().isoformat()

                self._json(200, {"success": True})
                # The main-thread timer redraws the panel to show the user.
                request_redraw()
            except Exception as e:
                self._respond(500, str(e).encode())

        elif path == '/disconnect-user':
            with safe_user_info_access() as user_info:
                user_info['name'] = None
                user_info['email'] = None
            self._json(200, {"success": True})
            request_redraw()

        elif path == '/command':
            self._command()
        else:
            self._respond(404)

    # -- control routes ---------------------------------------------------

    def _command(self):
        if not is_control_enabled():
            self._json(403, {"ok": False, "error": "control_disabled"})
            return
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._json(415, {"ok": False, "error": "Content-Type must be application/json"})
            return
        body = self._read_body(MAX_COMMAND_BYTES)
        if body is None:
            return
        try:
            command = json.loads(body, parse_constant=_refuse_constant)
        except ValueError as e:
            self._json(400, {"ok": False, "error": f"the body is not valid JSON: {e}"})
            return
        problem = _command_problem(command)
        if problem:
            self._json(400, {"ok": False, "error": problem})
            return
        response = commands.submit(command["id"], command["op"], command["payload"])
        self._json(504 if response.get("error") == "timeout" else 200, response)

    def _render_route(self, job_id, wants_file):
        if wants_file:
            path = render.job_file(job_id)
            if path is None:
                self._respond(404, b"No finished render with that id")
                return
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                self._respond(404, b"The render file is gone")
                return
            self._respond(200, data, "video/mp4")
            return
        status = render.job_status(job_id)
        if status is None:
            self._json(404, {"state": "failed", "error": "unknown render job"})
        else:
            self._json(200, status)


def _refuse_constant(name):
    raise ValueError(f"{name} is not a number JSON allows")


def _command_problem(command):
    if not isinstance(command, dict):
        return "the body must be an object {id, op, payload}"
    unknown = sorted(set(command) - {"id", "op", "payload"})
    if unknown:
        return f"unknown field(s): {', '.join(unknown)}"
    if not isinstance(command.get("id"), str) or not (0 < len(command["id"]) <= 128):
        return "id must be a string of 1-128 characters"
    if command.get("op") not in OPS:
        return f"unknown op {command.get('op')!r}; known: {', '.join(OPS)}"
    if not isinstance(command.get("payload"), dict):
        return "payload must be an object"
    return None


def start_server():
    global server_instance, server_thread, _blender_version

    if server_instance:
        return

    _blender_version = ".".join(str(n) for n in bpy.app.version)
    try:
        server_instance = ThreadingHTTPServer(('localhost', request_guard.PORT), GLBRequestHandler)
        # /command waits on Blender for up to 90 s; other routes must not queue behind it.
        server_instance.daemon_threads = True
        server_thread = threading.Thread(target=server_instance.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        print(f"Framo Bridge server started on http://localhost:{request_guard.PORT}")
    except Exception as e:
        print(f"Failed to start server: {e}")
        server_instance = None

def stop_server():
    global server_instance, server_thread

    if server_instance:
        try:
            server_instance.shutdown()
            server_instance.server_close()

            # Wait for thread to finish (with timeout)
            if server_thread and server_thread.is_alive():
                server_thread.join(timeout=2.0)

            print("Framo Bridge server stopped")
        except Exception as e:
            print(f"Error stopping server: {e}")
        finally:
            server_instance = None
            server_thread = None
