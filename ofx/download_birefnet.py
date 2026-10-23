"""Download an optional v1-compatible auto-hint model; no downloads during render."""
import argparse
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download

parser = argparse.ArgumentParser()
parser.add_argument("variant", choices=("general", "portrait", "matting"))
args = parser.parse_args()
repo = "ZhengPeng7/BiRefNet" + ("" if args.variant == "general" else "-" + args.variant)
revision = HfApi().model_info(repo).sha
folder = Path(__file__).resolve().parents[1] / "vfms/birefnet" / args.variant
snapshot_download(repo, revision=revision, local_dir=folder,
                  allow_patterns=["*.json", "*.py", "*.safetensors", "*.md", "LICENSE*"])
(folder / "ofx-source.json").write_text(json.dumps({"repo": repo, "revision": revision}, indent=2))
print(f"Installed {repo}@{revision} into {folder}")
