"""Exercise the actual CUDA model and file mailbox; writes a machine-readable report.

Run with .venv-ofx/Scripts/python.exe ofx/tests/smoke_real.py after setup.
Synthetic plates verify execution/HDR transport, not real-shot matte quality.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "ofx/build-smoke"
WORK.mkdir(exist_ok=True)
for name in ("stop", "ready.json", "done.json", "request.json", "error.txt"):
    (WORK / name).unlink(missing_ok=True)
width, height, count = 192, 128, 6
y, x = np.indices((height, width))
for i in range(count):
    frame = np.full((height, width, 4), [.03, .55, .02, 1.], dtype="<f4")
    subject = ((x - 95 - i * 2) / 30) ** 2 + ((y - 64) / 48) ** 2 < 1
    frame[subject] = [.65, .2, .12, 1]
    frame[40:48, 90:100, :3] = [2, .5, -.01]
    frame.tofile(WORK / f"source_{i:06d}.bin")
    mask = np.zeros_like(frame); mask[..., 3] = subject
    mask.tofile(WORK / f"hint_{i:06d}.bin")
settings = WORK / "settings.json"
configuration = dict(auto_vram=False, hann_chunk=4, hann_stride=2,
                     low_vram=True, carry_hint=True)
req = dict(protocol=2, width=width, height=height, start=0, count=count, index=1,
           mode=0, quality=.95, colorspace=1, premult=False, output=2,
           despill=0, despeckle=0, refiner=1, settings=str(settings))
report = {"gpu": torch.cuda.get_device_name(), "torch": torch.__version__, "jobs": []}
log = (WORK / "console.log").open("w")
process = subprocess.Popen([sys.executable, str(ROOT / "ofx/backend/worker.py"),
    "--work", str(WORK), "--root", str(ROOT), "--parent", str(os.getpid())],
    stdout=log, stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def wait(name):
    deadline = time.monotonic() + 900
    while not (WORK / name).exists():
        if process.poll() is not None:
            raise RuntimeError(f"Worker exited ({process.returncode}); see {WORK / 'console.log'}")
        if time.monotonic() > deadline: raise TimeoutError(name)
        time.sleep(.1)


try:
    wait("ready.json")
    for mode, carry, low_vram in ((0, True, True), (1, True, True),
                                 (2, True, True), (2, False, False)):
        req["mode"] = mode
        configuration.update(carry_hint=carry, low_vram=low_vram)
        settings.write_text(json.dumps(configuration))
        (WORK / "done.json").unlink(missing_ok=True)
        (WORK / "error.txt").unlink(missing_ok=True)
        (WORK / "request.tmp").write_text(json.dumps(req))
        start = time.monotonic()
        (WORK / "request.tmp").rename(WORK / "request.json")
        wait("done.json")
        if not json.loads((WORK / "done.json").read_text())["ok"]:
            raise RuntimeError((WORK / "error.txt").read_text())
        output = np.fromfile(WORK / "output.bin", dtype="<f4").reshape(height, width, 4)
        assert np.isfinite(output).all()
        assert (output[..., 3] >= 0).all() and (output[..., 3] <= 1).all()
        job = dict(hint_mode=mode, carry_hint=carry, low_vram=low_vram,
                   seconds=round(time.monotonic() - start, 3),
                   rgb_min=float(output[..., :3].min()), rgb_max=float(output[..., :3].max()),
                   alpha_min=float(output[..., 3].min()), alpha_max=float(output[..., 3].max()))
        report["jobs"].append(job)
        print(json.dumps(job), flush=True)
    report["ok"] = True
finally:
    (WORK / "report.json").write_text(json.dumps(report, indent=2))
    (WORK / "stop").touch()
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: process.kill(); process.wait()
    log.close()
