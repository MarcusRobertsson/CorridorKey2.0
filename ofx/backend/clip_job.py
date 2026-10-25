"""Background Fusion frame driver for the OFX Process Entire Clip button.

Only Fusion asks the OFX for images; no host suite calls occur on plugin-created
threads. Each returned frame must have an atomically committed native cache file
before it counts toward completion.
"""
from __future__ import annotations
import argparse
import json
import logging
import math
import os
from pathlib import Path
import struct
import sys
import time


def frame_path(directory, frame):
    # Match C++ llround for positive and negative frame times.
    value = frame * 1000000
    index = math.floor(value + .5) if value >= 0 else math.ceil(value - .5)
    return directory / f"frame_{index}.rgba"


def valid_frame(path):
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
        if len(header) != 16:
            return False
        x1, y1, x2, y2 = struct.unpack("<iiii", header)
        return x2 > x1 and y2 > y1 and path.stat().st_size == 16 + (x2 - x1) * (y2 - y1) * 16
    except OSError:
        return False


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temp, path)


def drive(node, comp, directory, start, end, token=None, frame_timeout=3600):
    count = int(math.floor(end - start + .00001)) + 1
    if count < 1:
        raise ValueError("The clip has no frames")
    started = time.monotonic()
    completed = 0

    def status(state, text):
        atomic_json(directory / "progress.json", dict(state=state, text=text, done=completed,
            total=count, elapsed=round(time.monotonic() - started, 2)))
        temporary = directory / "status.tmp"
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, directory / "status.txt")
        # A non-evaluating text parameter: updating it cannot invalidate renders.
        if token is None or node.GetInput("bakeToken") == token:
            node.SetInput("processStatus", text)

    try:
        status("running", f"Processing 0 / {count} frames (0%)")
        output = node.FindMainOutput(1)
        for index in range(count):
            if (directory / "cancel").exists():
                status("cancelled", f"Cancelled — {completed} / {count} frames cached")
                return False
            frame = start + index
            cached = frame_path(directory, frame)
            # Already completed frames can be resumed without re-evaluating Fusion.
            if not valid_frame(cached):
                status("running", f"Rendering {index + 1} / {count} — {completed} cached ({completed * 100 // count}%)")
                # Proxy=1 requests full-resolution frames regardless of viewer proxy.
                value = output.GetValue(frame, "", 0, 1)
                # Fusion may return a placeholder while the render continues.
                # A committed cache file, not the proxy object's truthiness, is
                # the authoritative indication that this frame has finished.
                deadline = time.monotonic() + frame_timeout
                while not valid_frame(cached):
                    if (directory / "cancel").exists():
                        status("cancelled", f"Cancelled — {completed} / {count} frames cached")
                        return False
                    error = directory / "render-error.txt"
                    if error.exists():
                        raise RuntimeError(error.read_text(encoding="utf-8", errors="replace"))
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"Frame {frame:g} did not produce a complete cached image before timeout. See the OFX console.")
                    time.sleep(.1)
                del value
            completed += 1
            status("running", f"Processing {completed} / {count} frames ({completed * 100 // count}%)")
        # Do not declare success if cancelled/edited during the last render.
        if (directory / "cancel").exists():
            status("cancelled", f"Cancelled — {completed} / {count} frames cached")
            return False
        status("done", f"DONE — {count} / {count} frames cached. Ready for playback.")
        return True
    except Exception as error:
        status("error", f"FAILED at {completed} / {count}: {error}")
        logging.exception("Clip processing failed")
        return False


def connect():
    modules = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules"
    sys.path.insert(0, str(modules))
    import DaVinciResolveScript
    resolve = DaVinciResolveScript.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError("Cannot connect to Resolve. Enable Preferences > System > General > External scripting using Local.")
    return resolve


def main(directory):
    logging.basicConfig(filename=directory / "clip-job.log", level=logging.INFO)
    config = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    try:
        fusion = connect().Fusion()
        comps = list((fusion.GetCompList() or {}).values())
        current = fusion.GetCurrentComp()
        if current:
            comps.insert(0, current)
        found = None
        for comp in comps:
            for node in comp.GetToolList().values():
                if node.GetAttrs().get("TOOLS_RegID") == "ofx.org.corridorkey.community.v2" and node.GetInput("bakeToken") == config["token"]:
                    found = comp, node
                    break
            if found:
                break
        if not found:
            raise RuntimeError("Could not find the node. Keep its Fusion composition open and retry.")
        comp, node = found
        # Fusion refuses SetInput on a disabled control. Enable the control for
        # RPC updates but make its text editor read-only for the user.
        for item in node.GetInputList().values():
            if item.GetAttrs().get("INPS_ID") == "processStatus":
                item.SetAttrs({"INPB_Disabled": False, "INPB_TextEditControl_ReadOnly": True})
        # Respect the clip's trimmed composition range, not unused source handles.
        attrs = comp.GetAttrs()
        start = max(config["start"], float(attrs.get("COMPN_GlobalStart", config["start"])))
        end = min(config["end"], float(attrs.get("COMPN_GlobalEnd", config["end"])))
        drive(node, comp, directory, start, end, token=config["token"],
              frame_timeout=float(node.GetInput("timeoutSeconds") or 3600))
    except Exception as error:
        logging.exception("Cannot start Fusion driver")
        atomic_json(directory / "progress.json", dict(state="error", text=str(error), done=0, total=0))
        # A sidecar keeps startup failures readable with the Refresh Status button.
        (directory / "failure.txt").write_text(str(error), encoding="utf-8")
    finally:
        (directory / "running").unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    main(args.job)
