"""Host-independent transport, cache keys, and float compositing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

PROTOCOL = 2
MAX_PIXELS = 16384 * 16384


def read_rgba(path: Path, width: int, height: int) -> np.ndarray:
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        raise ValueError("Invalid or excessive frame dimensions")
    if path.stat().st_size != width * height * 16:
        raise ValueError(f"Truncated or oversized float RGBA frame: {path.name}")
    data = np.fromfile(path, dtype="<f4").reshape(height, width, 4)
    if not np.isfinite(data).all():
        raise ValueError("Source contains NaN or infinite pixels")
    return data


def cache_key(request: dict, frames: list[Path], settings: dict) -> str:
    # Display-only controls must not invalidate the neural prediction cache.
    model = {k: request[k] for k in (
        "width", "height", "start", "count", "mode", "colorspace", "premult", "quality"
    )}
    digest = hashlib.sha256(json.dumps([model, settings], sort_keys=True).encode())
    for path in frames:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def to_linear(rgb):
    a = np.abs(rgb)
    return np.sign(rgb) * np.where(a <= .04045, a / 12.92, ((a + .055) / 1.055) ** 2.4)


def to_srgb(rgb):
    a = np.abs(rgb)
    return np.sign(rgb) * np.where(a <= .0031308, a * 12.92, 1.055 * a ** (1 / 2.4) - .055)


def compose(fg, alpha, *, output=0, colorspace=0, despill=.5,
            despeckle=0, refiner=1.0, source=None):
    """All physical operations use scene-linear straight RGB; alpha is coverage."""
    fg = np.asarray(fg, dtype=np.float32).copy()
    alpha = np.clip(alpha, 0, 1).astype(np.float32)
    if alpha.ndim == 2:
        alpha = alpha[..., None]
    if refiner != 1.0 and output != 4:
        if source is None:
            raise ValueError("Source required for refinement mix")
        # v2 has no CNN strength knob. Mix the v2 color reconstruction instead.
        fg = source + refiner * (fg - source)
    if despeckle:
        import cv2
        count, labels, stats, _ = cv2.connectedComponentsWithStats(
            (alpha[..., 0] > .01).astype(np.uint8), connectivity=8)
        keep = np.ones(count, dtype=bool)
        keep[0] = False
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= despeckle
        alpha = alpha * keep[labels, None]
    if output != 4:  # Raw foreground is available without despill/mix presentation.
        spill = np.maximum(fg[..., 1] - (fg[..., 0] + fg[..., 2]) * .5, 0) * despill
        fg += np.stack([spill * .5, -spill, spill * .5], axis=-1)
    if output == 1:
        return np.concatenate([np.repeat(alpha, 3, axis=-1), np.ones_like(alpha)], axis=-1)
    if output == 3:
        y, x = np.indices(alpha.shape[:2])
        bg = to_linear(np.where(((x // 32 + y // 32) % 2)[..., None], .35, .18))
        fg = fg * alpha + bg * (1 - alpha)
        alpha = np.ones_like(alpha)
    if colorspace == 0:
        fg = to_srgb(fg)
    if output == 0:
        fg *= alpha
    return np.ascontiguousarray(np.concatenate([fg, alpha], axis=-1), dtype="<f4")
