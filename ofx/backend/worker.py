"""One persistent CUDA worker per OFX instance, atomic file mailbox protocol v2.

The existing v2 runtime owns temporal inference and all foundation models.
No neural networks run inside Resolve's process.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np
from core import PROTOCOL, cache_key, compose, read_rgba, to_linear, to_srgb


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


class Engine:
    def __init__(self, root, work, mock=False):
        self.root, self.work, self.mock = root, work, mock
        self.model = None
        self.key = None
        self.output_dir = work / "predictions"

    def settings(self, path):
        settings = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
        if not isinstance(settings, dict):
            raise ValueError("Advanced settings must be a JSON object")
        # Transport/output are owned by OFX. Other InferenceConfig knobs are exposed.
        forbidden = {"outputs", "output_format", "exr_dtype", "temp_dir", "num_frames",
                     "start_mode", "frame_dir_linear", "frame_dir_fps", "cutout_linear"}
        if forbidden.intersection(settings):
            raise ValueError("OFX owns these settings: " + ", ".join(sorted(forbidden.intersection(settings))))
        return settings

    def process(self, req):
        if req.get("protocol") != PROTOCOL:
            raise ValueError("OFX/backend protocol mismatch; rebuild and reinstall")
        width, height, count = req["width"], req["height"], req["count"]
        if not 1 <= count <= 1000000 or not 0 <= req["index"] < count:
            raise ValueError("Invalid temporal range/index")
        settings = self.settings(req.get("settings", ""))
        source_files = [self.work / f"source_{i:06d}.bin" for i in range(count)]
        hint_files = ([self.work / f"hint_{i:06d}.bin" for i in range(count)]
                      if req["mode"] == 2 else
                      [self.work / "hint_000000.bin"] if req["mode"] == 1 else [])
        key = cache_key(req, source_files + hint_files, settings)
        if key != self.key:
            self.key = None
            if self.mock:
                self.mock_predict(req, source_files)
            else:
                self.predict(req, source_files, hint_files, settings)
            self.key = key
        i = req["index"]
        if self.mock:
            fg = np.load(self.output_dir / f"fg_{i:06d}.npy")
            alpha = np.load(self.output_dir / f"alpha_{i:06d}.npy")
        else:
            import cv2
            fg = cv2.imread(str(self.output_dir / "source_fg_exr" / f"fg_{i:06d}.exr"), cv2.IMREAD_UNCHANGED)
            alpha = cv2.imread(str(self.output_dir / "source_alpha_exr" / f"alpha_{i:06d}.exr"), cv2.IMREAD_UNCHANGED)
            if fg is None or alpha is None:
                raise RuntimeError("v2 runtime did not produce the expected frame")
            fg = fg[..., ::-1].copy()
            gain, contrast = settings.get("linear_brightness", 1), settings.get("linear_contrast", 1)
            pivot = settings.get("linear_contrast_pivot", .18)
            fg = (fg / gain - pivot) / contrast + pivot
        src = self.source_linear(source_files[i], req)
        result = compose(fg, alpha, output=req["output"], colorspace=req["colorspace"],
                         despill=req["despill"], despeckle=req["despeckle"],
                         refiner=req["refiner"], source=src)
        result.tofile(self.work / "output.tmp")
        os.replace(self.work / "output.tmp", self.work / "output.bin")

    def source_linear(self, path, req):
        rgba = read_rgba(path, req["width"], req["height"])
        rgb = rgba[..., :3].copy()
        if req["premult"]:
            np.divide(rgb, rgba[..., 3:4], out=rgb, where=rgba[..., 3:4] > 1e-6)
            rgb[rgba[..., 3] <= 1e-6] = 0
        return to_linear(rgb) if req["colorspace"] == 0 else rgb

    def mock_predict(self, req, files):
        """Transport-only test fixture. Never selected by the shipped plugin."""
        self.output_dir.mkdir(exist_ok=True)
        for i, path in enumerate(files):
            rgba = read_rgba(path, req["width"], req["height"])
            np.save(self.output_dir / f"fg_{i:06d}.npy", self.source_linear(path, req))
            np.save(self.output_dir / f"alpha_{i:06d}.npy", rgba[..., 3:4])

    def predict(self, req, files, hints, settings):
        import cv2
        import torch
        sys.path.insert(0, str(self.root))
        from corridorkey.runtime import InferenceConfig, RuntimePaths, load_model, run_inference
        from corridorkey.video_io import WriterConfig
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA PyTorch is required. Run ofx/setup.ps1; CPU-only PyTorch cannot key frames.")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("CorridorKey 2 requires a GPU supporting bfloat16 (RTX 30 series or newer).")
        cfg = InferenceConfig(**settings)
        if cfg.hann_chunk < 1 or not 1 <= cfg.hann_stride <= cfg.hann_chunk:
            raise ValueError("Require 1 <= hann_stride <= hann_chunk")
        if cfg.linear_brightness <= 0 or cfg.linear_contrast <= 0 or cfg.model_scale <= 0:
            raise ValueError("Brightness, contrast and model_scale must be positive")
        cfg.outputs, cfg.output_format, cfg.exr_dtype = {"alpha", "fg"}, "exr", "float"
        cfg.frame_dir_linear = True
        cfg.hint_quality = req["quality"]
        cfg.temp_dir = self.work / "runtime-temp"
        cfg.auto_vram = settings.get("auto_vram", "hann_chunk" not in settings and not cfg.low_vram)
        source_dir, hint_dir = self.work / "source", self.work / "hints"
        for directory in (source_dir, hint_dir, self.output_dir):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir()
        for i, path in enumerate(files):
            rgb = self.source_linear(path, req)
            # The upstream frame-directory reader does not apply video adjustments.
            rgb = cfg.linear_brightness * (cfg.linear_contrast_pivot + cfg.linear_contrast * (rgb - cfg.linear_contrast_pivot))
            if not cv2.imwrite(str(source_dir / f"frame_{i:06d}.exr"), np.ascontiguousarray(rgb[..., ::-1])):
                raise RuntimeError("Could not write source EXR (check cache disk space)")
        for i, path in enumerate(hints):
            rgba = read_rgba(path, req["width"], req["height"])
            # PNG hints are coverage, and intentionally clamped/quantized to 16 bits.
            alpha = (np.clip(rgba[..., 3], 0, 1) * 65535).round().astype(np.uint16)
            if not cv2.imwrite(str(hint_dir / f"hint_{i:06d}.png"), alpha):
                raise RuntimeError("Could not write hint")
        if req["mode"] >= 3:
            self.generate_birefnet_hints(req, files, hint_dir)
        if self.model is None:
            self.model = load_model(RuntimePaths.from_package(self.root), device="cuda")
        run_inference(self.model, str(source_dir), str(self.output_dir), cfg, WriterConfig(),
                      initial_hint_path=str(hint_dir / "hint_000000.png") if req["mode"] == 1 else None,
                      hint_video_path=str(hint_dir) if req["mode"] >= 2 else None)
        # Release all CUDA models between inference jobs, allowing other Resolve
        # nodes/instances to share the GPU. Disk predictions survive output tweaks.
        del self.model
        self.model = None
        torch.cuda.empty_cache()

    def generate_birefnet_hints(self, req, files, directory):
        """Optional v1 auto-hint choices; downloaded explicitly, local-only at render."""
        import cv2
        import torch
        import torch.nn.functional as F
        from transformers import AutoModelForImageSegmentation
        variant = {3: "general", 4: "portrait", 5: "matting"}[req["mode"]]
        path = self.root / "vfms/birefnet" / variant
        if not (path / "config.json").exists():
            raise RuntimeError(f"BiRefNet {variant} is not installed. Run .venv-ofx/Scripts/python.exe ofx/download_birefnet.py {variant}")
        model = AutoModelForImageSegmentation.from_pretrained(
            str(path), trust_remote_code=True, local_files_only=True).eval().half().cuda()
        try:
            mean = torch.tensor([.485, .456, .406], device="cuda").view(1, 3, 1, 1)
            std = torch.tensor([.229, .224, .225], device="cuda").view(1, 3, 1, 1)
            with torch.inference_mode():
                for i, file in enumerate(files):
                    rgb = np.clip(to_srgb(self.source_linear(file, req)), 0, 1)
                    image = torch.from_numpy(rgb.copy()).permute(2, 0, 1)[None].cuda()
                    image = F.interpolate(image, size=(1024, 1024), mode="bilinear", align_corners=False)
                    pred = model(((image - mean) / std).half())[-1].sigmoid()
                    pred = F.interpolate(pred.float(), size=rgb.shape[:2], mode="bilinear", align_corners=False)
                    mask = (pred[0, 0].cpu().numpy() * 65535).round().astype(np.uint16)
                    if not cv2.imwrite(str(directory / f"hint_{i:06d}.png"), mask):
                        raise RuntimeError("Could not write BiRefNet hint")
                    del image, pred
        finally:
            del model
            torch.cuda.empty_cache()


def run(work, root, parent_pid, mock=False):
    work.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=work / "worker.log", level=logging.INFO, force=True)
    engine = Engine(root, work, mock)
    # Serialize GPU jobs across instances; OS releases ownership on process death.
    mutex = None
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        mutex = kernel.CreateMutexW(None, False, "Local\\CorridorKey2CudaJobs")
        parent = kernel.OpenProcess(0x00100000, False, parent_pid) if parent_pid else None
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
    atomic_json(work / "ready.json", {"protocol": PROTOCOL})
    try:
        idle = time.monotonic()
        while not (work / "stop").exists():
            if os.name == "nt" and parent_pid and (not parent or kernel.WaitForSingleObject(parent, 0) == 0):
                break
            request_path = work / "request.json"
            if not request_path.exists():
                if time.monotonic() - idle > 3600:
                    break
                time.sleep(.025)
                continue
            req = json.loads(request_path.read_text(encoding="utf-8"))
            request_path.unlink()
            locked = False
            try:
                if mutex:
                    while True:
                        status = kernel.WaitForSingleObject(mutex, 100)
                        if status in (0, 0x80):
                            locked = True
                            break
                        if status != 0x102:
                            raise RuntimeError("GPU mutex wait failed")
                        if (work / "stop").exists():
                            return
                engine.process(req)
                atomic_json(work / "done.json", {"ok": True})
            except Exception:
                logging.exception("Inference failed")
                # Native side displays the detailed error from this UTF-8 text file.
                (work / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                atomic_json(work / "done.json", {"ok": False})
                engine.key = None
                engine.model = None
                if not mock:
                    import torch
                    torch.cuda.empty_cache()
            finally:
                if locked:
                    kernel.ReleaseMutex(mutex)
            idle = time.monotonic()
    finally:
        if mutex:
            kernel.CloseHandle(mutex)
            if parent:
                kernel.CloseHandle(parent)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent", type=int, default=0)
    parser.add_argument("--mock", action="store_true", help="Transport tests only; no AI")
    args = parser.parse_args()
    run(args.work, args.root, args.parent, args.mock)
