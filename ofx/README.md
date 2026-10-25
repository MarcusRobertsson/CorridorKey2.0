# CorridorKey 2 for DaVinci Resolve — community OFX

Windows x64 native OFX with an isolated CUDA Python worker. The worker runs this
checkout's **original CorridorKey 2 runtime**, including its foundation models,
RVM, overlapping Hann windows, stage 2, and low-memory staging. This is a community
integration, separate from Corridor's official commercial plugin.

## Install

Requires an NVIDIA RTX 30-series or newer GPU, Python 3.11–3.13, Git, and Windows
10/11. Tested build uses Python 3.11, CUDA PyTorch 2.9.1/cu128 and an RTX 5080.
The isolated environment and upstream weights require several GB of disk space.
Resolve shares VRAM with the worker; start with native model scale and auto VRAM.

From the repository root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File ofx/setup.ps1
# Visual Studio 2022 C++ Build Tools and CMake:
powershell -ExecutionPolicy Bypass -File ofx/build.ps1
powershell -ExecutionPolicy Bypass -File ofx/install.ps1
```

The installer registers a per-user `OFX_PLUGIN_PATH` and `CORRIDORKEY2_ROOT`, then
copies the bundle to `%LOCALAPPDATA%/Programs/Common/OFX/Plugins`. Close Resolve
first. Start it with `ofx/Launch_Resolve.bat` to pick up the plugin and runtime
paths immediately, or use a fresh terminal/sign-in session.
If your host ignores the custom search path, copy the bundle into
`C:/Program Files/Common Files/OFX/Plugins` with administrator rights.

A portable compiler build is also supported:

```powershell
python -m pip install --target .tools ziglang==0.14.1
powershell -ExecutionPolicy Bypass -File ofx/build.ps1 -Zig .tools/ziglang/zig.exe
powershell -ExecutionPolicy Bypass -File ofx/install.ps1 -Bundle ofx/build-zig/CorridorKey2.ofx.bundle
```

The `Runtime folder` control must point to this checkout, including `.venv-ofx`,
`weights`, `vfms`, and `ofx/backend`. Moving the checkout requires updating it.
The bundle includes backend source, but is not a self-contained Python distribution.

## Resolve workflow

### Process once, then play back

1. Set up the key and choose your output view.
2. Click **Process Entire Clip** in the node Inspector. Processing runs through
   the open Fusion composition in the background; keep Resolve and that comp open.
3. **Processing Status** displays the completed-frame count and percentage.
   **DONE — N / N frames cached. Ready for playback.** means every output frame
   in the clip's composition range has been committed to disk.
4. Play the clip. Full-resolution cached frames bypass the AI and source-frame
   gathering. The first playback may still fill Fusion's RAM cache; performance
   also depends on SSD speed and downstream nodes.

**Cancel Processing** stops after the current frame finishes; it never marks a
partial job as done. Changing key settings or reconnecting inputs cancels and
invalidates the old bake. Click Process Entire Clip again after edits. Changes
to source files on disk or edits upstream that Fusion does not notify as input
changes require manually processing again. Do not edit upstream footage during
a job: it uses frozen per-block inputs.

This feature uses Resolve's local Fusion scripting API. If startup fails, click
**Refresh Status** to read its error. External scripting must allow **Local** in
Resolve Preferences > System > General. There is no network service involved.

Completed frames are persistent float RGBA files in `<Runtime folder>/.cache-ofx/`.
They survive worker/Resolve restarts if the project is saved with the node. Each
1080p frame takes about 32 MiB; a 360-frame bake is about 11.1 GiB. Use a fast SSD.
Each new Process request creates a new cache directory. Old directories can be
removed when their jobs are stopped and their saved project bakes are no longer
needed. This cache is excluded from Git. Viewer proxy renders use the preview
path; the bake driver requests full resolution.

The button honors **Frames Per Block**: 8 processes the whole clip in separate
8-frame blocks; 0 processes it as one continuous shot for full RVM/Hann continuity.
Use 0 for final temporal quality if the extra temporary disk storage is available.

### Node setup

1. In Fusion, add **CorridorKey 2 (Community)** from the CorridorKey tools group.
   Connect MediaIn to Source. The Color page also receives the OFX Filter context.
2. Set Runtime folder if not populated. Choose the input transfer explicitly.
3. Use **No Hint** for native v2 automatic subject selection, **First Frame Hint**
   for a starting matte, or **Every Frame Hint** for a roto/matte stream.
4. Connect AlphaHint for external modes. RGBA hints use their alpha channel;
   alpha-only inputs use their single channel. Convert opaque grayscale RGB to
   alpha before connecting. Match source size, placement, and timing.
5. The default **8 frames per block** is for preview. Set **0 (Whole Shot)** for
   final continuous temporal processing. Trim the source range to a single shot.
6. Use Resolve's render cache for playback once the key is approved.

The first request gathers the temporal input range, runs v2, and caches its raw
foreground/alpha. Whole Shot preserves chronological RVM, Hann overlaps and hint
carry across that range. Fixed blocks reset recurrent state and hint carry at
boundaries and can introduce seams. The first-frame hint always comes from the
first frame of the source clip, including when previewing a later block.
The host must permit temporal image access. Missing frames fail with an error;
they are never silently replaced with a different frame.

All renders run at the host-supplied image resolution. Proxy/optimized media and
Resolve render scale therefore affect model input quality. Use full resolution
for final comparison. Temporal controls are deliberately non-animated: one set
of settings applies to an entire inference job. Cuts should be separate clips.

### Colors and outputs

The model expects scene-linear RGB in sRGB/Rec.709 primaries. Use **sRGB** for
sRGB-coded plates, or **Scene Linear** for linear plates. DWG Intermediate,
ACEScg, S-Log, PQ/HLG and Rec.709 gamma 2.4 are **not** sRGB: transform into the
supported primaries/transfer before the node, and back afterward as appropriate.
The plugin does not infer Resolve color management settings or metadata.

Float RGBA transport and float EXR intermediates preserve negative and HDR RGB.
Only alpha is clamped. Native signed asinh encoding/decoding stays in the v2
runtime. Enable Source Is Premultiplied only when the incoming RGB actually is.

| Output | RGB | Alpha |
|---|---|---|
| Processed | Despilled foreground, multiplied once after output transfer | Predicted matte |
| Matte | Matte repeated in RGB | 1 |
| Foreground | Despilled straight foreground | Predicted matte |
| Checker Composite | Linear-light composite, then output transfer | 1 |
| Raw Foreground | Model foreground without despill or reconstruction mix | Predicted matte |

Despill, despeckle, output view, and color reconstruction mix reuse cached raw
predictions. Despeckle removes connected foreground regions smaller than the
chosen pixel area. Despill 0–1 is the v2 range; up to 10 allows the old plugin's
overdrive behavior. Color Reconstruction Mix blends source RGB (0) and v2's
reconstructed foreground (1), with extrapolation up to 5. **It is not the v1 CNN
refiner**, which does not exist in the v2 architecture.

### Optional v1 automatic hint models

The old BiRefNet General, Portrait, and Matting choices remain available as
optional conditioning inputs to v2. Native v2 No Hint needs none of them.
Install the variants you want explicitly; no render downloads models:

```powershell
.venv-ofx/Scripts/python.exe ofx/download_birefnet.py general
.venv-ofx/Scripts/python.exe ofx/download_birefnet.py portrait
.venv-ofx/Scripts/python.exe ofx/download_birefnet.py matting
```

The downloader records the exact Hugging Face revision. These models include
their publisher's custom Python code, loaded locally by Transformers. Auto hints
are generated before loading v2 and then supplied to the full-hint video path.
See the [BiRefNet publisher's model card](https://huggingface.co/ZhengPeng7/BiRefNet).

### Advanced v2 features

Set Advanced Settings JSON to a file like `ofx/settings.example.json`. Its contents
are included in the cache key, so editing it invalidates neural predictions.
An empty path uses v2 defaults with automatic VRAM selection enabled.

Available settings mirror `corridorkey.runtime.InferenceConfig`: Hann chunk and
stride, carry_hint, carry_hint_quality, model_scale, low_vram and every low_vram
chunking control, stage2_batch, shape_quantum, brightness/contrast/pivot and seed.
Hint quality is controlled by the OFX panel. With auto_vram true, the runtime
chooses Hann settings and low_vram from currently available GPU memory. Set it
false to use the manual Hann/low_vram values in the example file.

OFX owns output format, frame count/range, input transfer, temporary directory,
and output views. Container encoding, EXR file export, partial-clip selection,
and batch/drag-and-drop launchers remain available through the unmodified
upstream CLI. For example, use `infer.py --output_format exr` to write final EXRs
outside Resolve. The plugin itself returns float images to the host.

## Performance and limitations

This is an **offline inference integration**, not a real-time keyer. It avoids
loading PyTorch into Resolve, supports canceling waits, reports backend failures
as failed OFX renders, and keeps each instance's files separate. A named Windows
mutex serializes worker inference across instances to avoid loading several
copies of the model into GPU memory. CUDA models are released after each neural
job so Resolve and other instances can use the memory; revisiting a different
block reloads the model. The current cache retains one block per instance.

To detect upstream image changes reliably, every render reads and hashes the
selected temporal range, even on prediction cache hits. Native SHA-256 hashes
avoid rewriting unchanged float frames; the worker also hashes the stored inputs.
Float files and EXRs add disk overhead; use a fast SSD with ample space in the
Windows temp directory. A single 3840x2160 source RGBA float frame is about 127 MB,
before EXR and prediction files. Whole Shot can require substantial storage.
This design prioritizes compatibility and full v2 temporal behavior; it does not
provide direct Resolve CUDA-buffer interop or v1's torch.compile acceleration.
Current limits are Windows x64, finite source ranges, fixed bounds per range,
float RGB/RGBA source, and matching alpha-hint dimensions. Square pixels are
recommended; anamorphic clips should be resized to square pixels upstream.

Errors include a diagnostic message in Resolve. Logs and active cache live in
`%TEMP%/CorridorKey2-<process>-<instance>-<time>/worker.log`; normal instance
destruction removes the folder. Crash leftovers can be removed after Resolve
and the relevant worker have stopped. Backend startup failure before imports
complete may require manually running the worker from a terminal to see stderr.

## Validation

```powershell
python -m unittest discover -s ofx/tests -v
python ofx/tests/check_binary.py ofx/build-zig/CorridorKey2.ofx.bundle
ctest --test-dir ofx/build-zig --output-on-failure
.venv-ofx/Scripts/python.exe ofx/tests/smoke_real.py
```

The first suite uses a mock model solely to test IPC, recovery, invalidation, and
numerical output contracts; the plugin cannot enable mock mode. The last script
runs the actual downloaded models with no hint, first-frame hint, and full hints
on a tiny synthetic sequence and writes `ofx/build-smoke/report.json`. This is an
execution check, not a real-shot quality or 4K performance benchmark.

See [VALIDATION.md](VALIDATION.md) for the checks completed on this machine and
the remaining Resolve/footage validation.

## Attribution and license

CorridorKey 2 by Corridor Digital, Zachary Harris and contributors: see the
repository's `LICENSE` and `THIRD_PARTY_NOTICES.md`. They remain authoritative for
the model and foundation assets. This adapter follows that repository license.
The v1 OFX reference was
[gitcapoom/corridorkey_ofx](https://github.com/gitcapoom/corridorkey_ofx), inspected
at commit `ef2bdd0c619a34cead9929d49541eb753d3d2df0`; its license is preserved in
`V1-LICENSE.txt`. The vendored OpenFX SDK came from that snapshot; its BSD-3-Clause
license is in `plugin/openfx/LICENSE.md`. Optional BiRefNet code/models retain
their publisher's MIT license. No model weights are bundled with the plugin.
