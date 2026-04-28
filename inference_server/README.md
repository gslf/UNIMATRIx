# inference_server

A small, self-contained local LLM host for Unimatrix. It downloads GGUF
model files from HuggingFace and serves them on an OpenAI-compatible HTTP
endpoint (`/v1/chat/completions`) that the simulation talks to.

This folder has its **own venv** — keeps the heavy `llama-cpp-python` build
out of the main project's environment.

## TL;DR

```bash
# from this folder (inference_server/)
py -3.12 -m venv .venv
.venv/Scripts/activate           # Windows
# source .venv/bin/activate      # Linux/macOS

pip install -r requirements.txt  # CPU build (works everywhere)
python download.py qwen2.5-3b    # ~2 GB
python serve.py qwen2.5-3b       # blocks; serves on http://localhost:8000
```

That's it. In another terminal, from the parent folder, run Unimatrix with
`--backend llama_cpp --endpoint http://localhost:8000`.

> Use Python **3.10–3.12**. The CUDA/CPU wheels for `llama-cpp-python` are
> not yet published for 3.13/3.14.

## Available models

```bash
python download.py --list
```

Files land in `./models/`. Re-running `download.py` is a no-op if the file
is already present. You can also point `serve.py` at a `.gguf` file you
already have on disk:

```bash
python serve.py "D:/llms/some-model-Q4_K_M.gguf" --chat-format chatml --n-ctx 8192
```

## Serve options

```bash
python serve.py qwen2.5-3b               # GPU by default (n_gpu_layers=-1)
python serve.py qwen2.5-3b --n-gpu-layers 0     # force CPU
python serve.py qwen2.5-3b --port 8000 --n-ctx 8192
```

Common flags: `--host`, `--port`, `--n-gpu-layers`, `--n-ctx`, `--n-batch`,
`--chat-format`, `--alias`, `--seed`, `--verbose`. Run `python serve.py -h`
for the full list.

### Quick endpoint check

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"qwen2.5-3b","messages":[{"role":"user","content":"Say hi."}]}'
```

---

## GPU install (NVIDIA / CUDA)

Plain `pip install -r requirements.txt` gives you the **CPU wheel**. PyPI
does not host CUDA wheels. To run on GPU, install `llama-cpp-python` from
the matching prebuilt wheel index *before* installing the requirements:


```bash
$env:CMAKE_ARGS = "-DGGML_CUDA=on -DGGML_CUDA_FORCE_CUBLAS=1 -DGGML_CUDA_NO_PINNED=1 -DCMAKE_CUDA_ARCHITECTURES=120"
pip install --no-binary llama-cpp-python --upgrade --force-reinstall "llama-cpp-python[server]"
pip install -r requirements.txt
```


### Vulkan (AMD / Intel)

```bash
$env:CMAKE_ARGS = "-DGGML_VULKAN=on"
pip install --no-binary llama-cpp-python "llama-cpp-python[server]"
```

---

## Troubleshooting

### "Could not find module llama.dll" on Windows

The DLL exists — one of its **CUDA dependencies** can't be loaded
(`cudart64_*.dll`, `cublas64_*.dll`, `cublasLt64_*.dll`). In order of
likelihood:

1. **You installed only the NVIDIA driver, not the CUDA Toolkit.** The
   driver alone has no `cudart`/`cublas` DLLs. Install the toolkit at the
   version your wheel was built for from
   <https://developer.nvidia.com/cuda-downloads>.
2. **Toolkit installed but not on `PATH`.** `serve.py` autodetects common
   locations. If yours is unusual, set `UNIMATRIX_CUDA_BIN` to its `bin`
   folder before running:
   ```powershell
   $env:UNIMATRIX_CUDA_BIN = "D:\cuda\v13.0\bin"
   python serve.py qwen2.5-3b
   ```

### Multi-part GGUFs

Files split like `model-Q4_K_M-00001-of-00002.gguf` are auto-stitched. Pass
the **first** part — the rest load from the same folder. If you point at
part 2 by mistake, `serve.py` warns and auto-corrects. Missing parts cause
a fast-fail at startup.


### Other gotchas

- Unimatrix calls `/v1/chat/completions`. Make sure `chat_format` (in
  `models.toml`, or `--chat-format`) matches the model's training template.
- `--n-parallel` controls llama.cpp continuous batching slots. Above 16 on a
  14B model rarely helps — decode throughput dominates.
- Guided JSON (used by Unimatrix's voting/decision prompts) works out of the
  box: `llama_cpp.server` exposes `response_format={"type":"json_object"}`.
