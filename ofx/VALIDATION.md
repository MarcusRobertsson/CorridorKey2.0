# Validation — 23 October 2026

Windows x64, NVIDIA GeForce RTX 5080 (16 GB), Python 3.11,
PyTorch 2.9.1+cu128, OpenCV 4.11.0.86. Native bundle compiled in Release
using Zig 0.14.1 / Clang 19.1.7 and CMake/Ninja.

- **Build:** succeeded; x64 DLL loads and registers one OpenFX image-effect
  plugin, `org.corridorkey.community.v2`, version 2.0.
- **Regression suite:** 14 tests passed, including real worker subprocess IPC,
  failure/recovery, source and hint invalidation, display-only cache reuse,
  HDR/signed transfer, alpha association, reconstruction mix and despeckle.
- **Actual CUDA inference:** four jobs passed with the real downloaded v2,
  C-RADIO, MoGe and RVM weights. Each used six synthetic 192x128 float frames,
  Hann chunk 4 / stride 2 (two overlapping windows).

| Hint mode | Carry | Low VRAM | Result |
|---|---|---|---|
| No hint | On | On | Passed |
| First-frame external hint | On | On | Passed |
| Full external hints | On | On | Passed |
| Full external hints | Off | Off | Passed |

All returned float RGBA at the requested resolution, with finite values and
alpha within [0,1]. RGB retained negative values (about -0.0103) and values
above 1 (up to 2.147). The foundation downloads were verified against their
publishers' hashes: C-RADIO and MoGe SHA-256, RVM's upstream MD5 check.
The machine-readable run report is generated at `ofx/build-smoke/report.json`.

**Resolve integration:** the local Resolve 21.1 startup log recorded
`OFX: loading org.corridorkey.community.v2`. Its scripting connection did not
become available, so no render inside Resolve was verified. Real-shot quality,
Color-page alpha wiring, Fusion rendering, 4K performance, cancellation inside
Resolve, and optional BiRefNet inference still need host/footage testing.
Synthetic backend tests are not a claim of those results.

Run the commands in `README.md` here to reproduce the checks. Model files and
the isolated CUDA environment are local assets excluded from Git.
