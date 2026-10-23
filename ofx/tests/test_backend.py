"""Numerical and real subprocess IPC regression checks (no weights required)."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import numpy as np

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
from core import cache_key, compose, read_rgba, to_linear, to_srgb


def request():
    return dict(protocol=2, width=3, height=2, start=0, count=2, index=0,
                mode=0, colorspace=1, premult=False, quality=.95, output=0,
                despill=0, despeckle=0, refiner=1, settings="")


class NumericalTests(unittest.TestCase):
    def test_hdr_and_negative_transfer_round_trip(self):
        v = np.array([-3, -.1, 0, .0031308, .18, 1, 32], np.float32)
        np.testing.assert_allclose(to_linear(to_srgb(v)), v, rtol=2e-6, atol=1e-7)

    def test_premultiplies_once_and_keeps_hdr(self):
        fg = np.full((2, 3, 3), [8, -.2, 2], np.float32)
        alpha = np.full((2, 3, 1), .25, np.float32)
        out = compose(fg, alpha, colorspace=1, despill=0)
        np.testing.assert_allclose(out[..., :3], fg * .25)
        np.testing.assert_allclose(out[..., 3:], alpha)

    def test_straight_output_retains_rgb_at_zero_alpha(self):
        out = compose(np.full((1, 1, 3), 4), np.zeros((1, 1, 1)), output=2, colorspace=1, despill=0)
        np.testing.assert_array_equal(out, [[ [4, 4, 4, 0] ]])

    def test_premultiply_after_output_transfer(self):
        fg = np.full((1, 1, 3), .18)
        out = compose(fg, np.full((1, 1, 1), .5), despill=0)
        np.testing.assert_allclose(out[..., :3], to_srgb(fg) * .5, rtol=1e-6)

    def test_matte_is_opaque_coverage(self):
        out = compose(np.zeros((1, 1, 3)), np.array([[.2]]), output=1)
        np.testing.assert_allclose(out, [[[.2, .2, .2, 1]]])

    def test_despill_does_not_clip_hdr(self):
        fg = np.array([[[2., 8., 4.]]])
        out = compose(fg, np.ones((1, 1, 1)), output=2, colorspace=1, despill=1)
        np.testing.assert_allclose(out, [[[4.5, 3, 6.5, 1]]])

    def test_raw_output_ignores_reconstruction_mix(self):
        out = compose(np.full((1, 1, 3), 2), np.ones((1, 1, 1)), output=4,
                      colorspace=1, despill=10, refiner=0)
        np.testing.assert_array_equal(out, [[[2, 2, 2, 1]]])

    def test_despeckle_removes_only_small_components(self):
        try: import cv2
        except ImportError: self.skipTest("OpenCV unavailable")
        matte = np.zeros((8, 8, 1), np.float32)
        matte[1, 1] = 1; matte[4:7, 4:7] = .8
        out = compose(np.ones((8, 8, 3)), matte, output=2, colorspace=1,
                      despill=0, despeckle=3)
        self.assertEqual(out[1, 1, 3], 0)
        np.testing.assert_allclose(out[4:7, 4:7, 3], .8)

    def test_truncated_and_nonfinite_frames_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "frame.bin"
            np.zeros(23, dtype=np.float32).tofile(p)
            with self.assertRaises(ValueError): read_rgba(p, 3, 2)
            a = np.zeros(24, dtype=np.float32); a[0] = np.nan; a.tofile(p)
            with self.assertRaises(ValueError): read_rgba(p, 3, 2)

    def test_cache_includes_pixels_hint_and_settings_but_not_output(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "frame"; p.write_bytes(b"first")
            r = request(); key = cache_key(r, [p], {})
            r["output"], r["despill"], r["index"] = 2, .7, 1
            self.assertEqual(key, cache_key(r, [p], {}))
            self.assertNotEqual(key, cache_key(r, [p], {"carry_hint": False}))
            p.write_bytes(b"second")
            self.assertNotEqual(key, cache_key(r, [p], {}))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.proc = subprocess.Popen([sys.executable, str(BACKEND / "worker.py"),
            "--work", str(self.work), "--root", str(BACKEND.parents[1]), "--mock"],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.wait("ready.json")
        self.frames = []
        for i in range(2):
            a = np.full((2, 3, 4), [2 + i, .2, -.1, .5], dtype="<f4")
            a.tofile(self.work / f"source_{i:06d}.bin")
            self.frames.append(a)

    def wait(self, name):
        deadline = time.monotonic() + 15
        while not (self.work / name).exists():
            if self.proc.poll() is not None: self.fail("Worker exited")
            if time.monotonic() > deadline: self.fail("Worker timed out")
            time.sleep(.025)

    def send(self, req):
        (self.work / "done.json").unlink(missing_ok=True)
        (self.work / "error.txt").unlink(missing_ok=True)
        (self.work / "request.tmp").write_text(json.dumps(req), encoding="utf-8")
        (self.work / "request.tmp").rename(self.work / "request.json")
        self.wait("done.json")
        return json.loads((self.work / "done.json").read_text())["ok"]

    def tearDown(self):
        (self.work / "stop").touch()
        try: self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait()
        self.temp.cleanup()

    def test_multi_frame_job_cache_display_changes_and_invalidation(self):
        r = request(); self.assertTrue(self.send(r))
        predicted = self.work / "predictions/fg_000000.npy"
        stamp = predicted.stat().st_mtime_ns
        out = read_rgba(self.work / "output.bin", 3, 2)
        np.testing.assert_allclose(out[..., :3], self.frames[0][..., :3] * .5)
        r.update(index=1, output=2, despill=.1)
        self.assertTrue(self.send(r))
        self.assertEqual(predicted.stat().st_mtime_ns, stamp)
        out = read_rgba(self.work / "output.bin", 3, 2)
        np.testing.assert_allclose(out[..., 0], 3)
        self.frames[0][..., 0] = 9
        self.frames[0].tofile(self.work / "source_000000.bin")
        self.assertTrue(self.send(r))
        np.testing.assert_allclose(np.load(predicted)[..., 0], 9)

    def test_worker_reports_error_then_recovers(self):
        r = request(); r["protocol"] = 1
        self.assertFalse(self.send(r))
        self.assertIn("protocol mismatch", (self.work / "error.txt").read_text())
        self.assertTrue(self.send(request()))

    def test_external_hint_content_invalidates_predictions(self):
        r = request(); r["mode"] = 2
        for i in range(2): self.frames[i].tofile(self.work / f"hint_{i:06d}.bin")
        self.assertTrue(self.send(r))
        p = self.work / "predictions/fg_000000.npy"
        stamp = p.stat().st_mtime_ns
        time.sleep(.05)
        self.frames[0][..., 3] = .1
        self.frames[0].tofile(self.work / "hint_000000.bin")
        self.assertTrue(self.send(r))
        self.assertNotEqual(p.stat().st_mtime_ns, stamp)

    def test_advanced_settings_ownership(self):
        settings = self.work / "settings.json"
        settings.write_text('{"outputs": ["checker"]}')
        r = request(); r["settings"] = str(settings)
        self.assertFalse(self.send(r))
        self.assertIn("OFX owns", (self.work / "error.txt").read_text())


if __name__ == "__main__":
    unittest.main()
