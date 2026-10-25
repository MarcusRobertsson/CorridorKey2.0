import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from clip_job import drive, frame_path, valid_frame


class Output:
    def __init__(self, directory, mode="ok"):
        self.directory, self.mode, self.calls = directory, mode, []

    def GetValue(self, frame, *args):
        self.calls.append(frame)
        if self.mode == "fail": return None
        # Non-zero origin exercises the native frame-header contract.
        frame_path(self.directory, frame).write_bytes(struct.pack("<iiii", -1, 2, 1, 4) + bytes(64))
        if self.mode == "cancel": (self.directory / "cancel").touch()
        return object()


class Node:
    def __init__(self, output): self.output, self.status = output, []
    def FindMainOutput(self, _): return self.output
    def SetInput(self, _, message): self.status.append(message)


class ClipJobTests(unittest.TestCase):
    def test_done_only_after_every_valid_frame(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d); output = Output(directory); node = Node(output)
            self.assertTrue(drive(node, None, directory, 12, 15))
            self.assertEqual(output.calls, [12, 13, 14, 15])
            self.assertTrue(all(valid_frame(frame_path(directory, f)) for f in output.calls))
            result = json.loads((directory / "progress.json").read_text())
            self.assertEqual((result["state"], result["done"], result["total"]), ("done", 4, 4))

    def test_cancel_is_never_reported_done(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d); node = Node(Output(directory, "cancel"))
            self.assertFalse(drive(node, None, directory, 0, 3))
            self.assertEqual(json.loads((directory / "progress.json").read_text())["state"], "cancelled")

    def test_failed_frame_does_not_increment_progress(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d); node = Node(Output(directory, "fail"))
            with self.assertLogs(level="ERROR"):
                self.assertFalse(drive(node, None, directory, 0, 3, frame_timeout=.01))
            result = json.loads((directory / "progress.json").read_text())
            self.assertEqual((result["state"], result["done"]), ("error", 0))

    def test_resume_skips_already_complete_frames(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d); output = Output(directory); node = Node(output)
            output.GetValue(2); output.calls.clear()
            self.assertTrue(drive(node, None, directory, 2, 4))
            self.assertEqual(output.calls, [3, 4])

    def test_truncated_cache_is_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            path = frame_path(Path(d), -.5)
            self.assertEqual(path.name, "frame_-500000.rgba")
            path.write_bytes(struct.pack("<iiii", 0, 0, 2, 2) + bytes(60))
            self.assertFalse(valid_frame(path))


if __name__ == "__main__": unittest.main()
