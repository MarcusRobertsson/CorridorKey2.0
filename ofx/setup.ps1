param([string]$Python = "python", [switch]$SkipWeights)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$venv = Join-Path $repo ".venv-ofx"
if (!(Test-Path "$venv/Scripts/python.exe")) {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Creating Python environment failed (use Python 3.11-3.13)" }
}
$py = Join-Path $venv "Scripts/python.exe"
& $py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
# CUDA 12.8 supports the RTX 50 series; do not install the old v1 CUDA 12.4 stack.
& $py -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw "CUDA PyTorch installation failed" }
& $py -m pip install -r "$repo/requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Runtime dependency installation failed" }
if (!$SkipWeights) {
    & $py "$repo/scripts/download_weights.py"
    if ($LASTEXITCODE -ne 0) { throw "Model download failed" }
}
& $py -c "import torch; print('PyTorch:', torch.__version__); assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.cuda.get_device_name()); assert torch.cuda.is_bf16_supported(), 'bfloat16 unavailable'"
if ($LASTEXITCODE -ne 0) { throw "CUDA verification failed" }
Write-Host "Runtime ready at $repo. Run install.ps1 after building."
