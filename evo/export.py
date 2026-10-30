"""Merge a LoRA adapter into Gemma 4 E2B and register it with Ollama, e.g. `podcast-exec:r1`.

Steps: load the bf16 base on CPU -> merge the adapter -> save safetensors -> `ollama create` with the stock
model's renderer, parser and sampling parameters.

Ollama 0.40 can import safetensors only where its MLX runtime exists (macOS), and it no longer accepts LoRA
adapters. Elsewhere, convert the merged model to GGUF with llama.cpp and pass --gguf:
  python llama.cpp/convert_hf_to_gguf.py evo/merged/podcast-exec-r1 --outtype q8_0 --outfile evo/gguf/podcast-exec-r1.gguf
  .venv-train/Scripts/python -m evo.export --adapter evo/checkpoints/executor-r1 --tag podcast-exec:r1 \
      --gguf evo/gguf/podcast-exec-r1.gguf --skip-merge
Compare against the un-tuned base converted the same way (q8_0), not against the stock gemma4:e2b quantization.

Usage (training venv):
  .venv-train/Scripts/python -m evo.export --adapter evo/checkpoints/executor-r1 --tag podcast-exec:r1
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from peft import PeftModel
from transformers import AutoModelForImageTextToText

from evo.lora import BASE_MODEL

TOKENIZER_FILES = ["tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "processor_config.json",
                   "generation_config.json"]


def merge(adapter: Path, out_dir: Path) -> None:
    model = AutoModelForImageTextToText.from_pretrained(BASE_MODEL, dtype=torch.bfloat16, device_map="cpu")
    model = PeftModel.from_pretrained(model, str(adapter)).merge_and_unload()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    model.save_pretrained(out_dir, safe_serialization=True)
    # Tokenizer, chat template and processor config come straight from the base snapshot
    # (AutoProcessor would need torchvision just to re-save the image-processor settings).
    snapshot = Path(snapshot_download(BASE_MODEL, allow_patterns=TOKENIZER_FILES))
    for name in TOKENIZER_FILES:
        if (snapshot / name).exists():
            shutil.copy(snapshot / name, out_dir / name)
    print(f"merged model saved to {out_dir}")


def modelfile_for(source: Path, base_tag: str) -> str:
    """Reuse the stock model's TEMPLATE / PARAMETER / RENDERER lines so prompts are formatted identically."""
    base = subprocess.run(["ollama", "show", base_tag, "--modelfile"], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout
    base = re.sub(r'(?s)LICENSE\s+""".*?"""', "", base)
    base = re.sub(r"(?m)^(#|FROM |DRAFT |PARAMETER draft_).*\n?", "", base)  # GGUF blobs of the stock model
    return f"FROM {source.resolve().as_posix()}\n{base.strip()}\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--tag", required=True, help="Ollama tag, e.g. podcast-exec:r1")
    ap.add_argument("--base-tag", default="gemma4:e2b")
    ap.add_argument("--quantize", default="int4", help="Ollama safetensors import (needs MLX): int4 | int8 | ...")
    ap.add_argument("--merged", type=Path, default=None)
    ap.add_argument("--gguf", type=Path, default=None, help="Register this GGUF (converted from --merged) instead")
    ap.add_argument("--skip-merge", action="store_true")
    args = ap.parse_args()

    merged = args.merged or Path("evo/merged") / args.tag.replace(":", "-")
    if not args.skip_merge:
        merge(args.adapter, merged)
    source = args.gguf or merged
    modelfile = merged / "Modelfile"
    modelfile.write_text(modelfile_for(source, args.base_tag), encoding="utf-8")
    cmd = ["ollama", "create", args.tag, "-f", str(modelfile)] + ([] if args.gguf else ["--quantize", args.quantize])
    subprocess.run(cmd, check=True)
    print(f"created ollama model {args.tag}; use it with: podcast-agent run ... --model ollama_chat/{args.tag}")


if __name__ == "__main__":
    main()
