"""Runs INSIDE Blender (headless) and executes code sent by BlenderSession.

Started as:  blender -b --factory-startup --python server.py -- --port 12345

Protocol: one JSON object per line over a localhost TCP socket.
    request:  {"cmd": "exec", "code": "...", "reset": true}   |  {"cmd": "ping"}  |  {"cmd": "shutdown"}
    response: {"ok": true/false, "output": "...", "error": "...", "traceback": "..."}

This file uses only Blender's bundled Python, so it must not import anything from our project.
"""

import argparse
import contextlib
import io
import json
import socket
import sys
import traceback

import bpy

READY_MARKER = "BLENDER_AGENT_READY"


def reset_scene() -> None:
    # Back to an empty factory scene: no objects, materials, cameras or leftover settings.
    bpy.ops.wm.read_factory_settings(use_empty=True)


def run_code(code: str) -> dict:
    buf = io.StringIO()
    namespace = {"__name__": "__main__"}
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(code, "<agent_code>", "exec"), namespace)
        return {"ok": True, "output": buf.getvalue()}
    except BaseException as exc:  # BaseException: agent code calling sys.exit() must not kill the server
        tb = traceback.format_exc()
        # Keep only the part of the traceback that points into the agent's code.
        lines = tb.splitlines()
        start = next((i for i, l in enumerate(lines) if 'File "<agent_code>"' in l), 0)
        return {
            "ok": False,
            "output": buf.getvalue(),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": "\n".join(lines[start:])[-3000:],
        }


def handle(request: dict) -> dict:
    cmd = request.get("cmd")
    if cmd == "ping":
        return {"ok": True, "output": f"pong (Blender {bpy.app.version_string})"}
    if cmd == "shutdown":
        return {"ok": True, "output": "bye"}
    if cmd == "exec":
        if request.get("reset", True):
            reset_scene()
        return run_code(request.get("code", ""))
    return {"ok": False, "error": f"unknown cmd {cmd!r}"}


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.port))  # localhost only: never reachable from the network
    server.listen(1)
    print(READY_MARKER, flush=True)

    conn, _ = server.accept()  # one client per Blender process
    stream = conn.makefile("rwb")
    for raw in stream:  # blocks until the next request line arrives
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as exc:
            response = {"ok": False, "error": f"bad request: {exc}"}
            request = {}
        else:
            response = handle(request)
        stream.write((json.dumps(response) + "\n").encode("utf-8"))
        stream.flush()
        if request.get("cmd") == "shutdown":
            break
    conn.close()
    server.close()


main()
