# crankgpt

Created for the Morgan State University 2026 Science Fair by Julian Mariano-Graham and Nathan Graham.

`crankgpt` is a DIY Jetson Orin Nano kiosk that runs a local LLM, measures the electrical energy used to generate each answer, and requires a visitor to hand-crank that same amount of energy before the answer unlocks.

The repository name is `crankgpt`. The kiosk UI and working app name in the code remain `The Crankinator`.

## Start Here

If you are opening this repo for the first time:

1. Read this `README.md` for the current tested setup and the shortest path to a working build.
2. Review the hardware overview below so you know the physical pieces involved.
3. Use `jetson/README.md` when you are ready to install the Jetson services.
4. Use the diagrams in `assets/` for a quick visual overview of the system.

## What It Does

1. A visitor types a prompt into a full-screen browser UI.
2. The Flask backend sends the prompt to a local model service running on the Jetson.
3. The Jetson measures answer-generation energy from the onboard INA3221 `VDD_IN` rail.
4. The answer returns hidden behind a lock state.
5. A UM34C USB power meter tracks crank-generated energy over Bluetooth RFCOMM.
6. The UI updates unlock progress until `100%`, then reveals the answer automatically.
7. A manual unlock button remains available as a fallback if the crank meter drops out.

## Current Stack

- Backend: Flask
- Frontend: vanilla HTML, CSS, and JavaScript
- Recommended Jetson inference runtime: `llama.cpp`
- Current tested model path: `Qwen3.5-2B.q4_k_m.gguf`
- Fallback runtime still supported in code: Ollama

## Current Tested Jetson Configuration

- `MODEL_BACKEND=llama_cpp`
- `LLAMA_CPP_BASE_URL=http://127.0.0.1:11439`
- `LLAMA_CPP_MODEL=Qwen3.5-2B.q4_k_m.gguf`
- `llama-server` launched with `--reasoning off`
- Flask bound to `0.0.0.0:8080`
- UM34C expected at `/dev/rfcomm0`

## Repository Contents

- `app.py`: Flask server, model calls, Jetson energy measurement, UM34C integration, session logic
- `templates/index.html`: kiosk HTML template
- `static/styles.css`: kiosk styling
- `static/app.js`: client-side state, session polling, and unlock UI
- `requirements.txt`: Python dependencies
- `crankinator-kiosk.html`: original single-file mockup
- `crankinator-presentation.html`: presentation-style demo page
- `assets/`: public system diagram and mind map
- `jetson/`: publish-safe Jetson service templates, setup notes, and Jetson support shims

## Hardware Overview

- NVIDIA Jetson Orin Nano Developer Kit
- NVMe SSD
- Display connected through DisplayPort
- Hand crank generator with regulated USB output
- RDTech UM34C USB power meter
- Power bank used as the crank-energy buffer
- Optional LED progress hardware

## Important Safety Constraint

Do not power the Jetson directly from the hand-crank system. The Jetson should stay on its own stable supply. The crank loop is a separate measured energy-credit path.

## Local Development

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Run the app:

```bash
./.venv/bin/python app.py
```

Open:

```text
http://127.0.0.1:8080
```

## DIY Jetson Build

These steps are written to be shareable. This repo intentionally uses placeholders instead of personal usernames, hostnames, IPs, or Bluetooth MAC addresses.

### 1. Install Jetson packages

```bash
sudo apt update
sudo apt install -y nvidia-jetpack git curl rsync python3-pip python3-venv build-essential cmake
```

### 2. Build `llama.cpp`

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build-cuda-sm87 -DGGML_CUDA=ON -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc -DCMAKE_CUDA_ARCHITECTURES=87
cmake --build build-cuda-sm87 --config Release -j 6 --target llama-server llama-cli
```

### 3. Download a local model

Create a model directory and download a GGUF that works on your Jetson. The current tested setup for this project used a third-party quantized `Qwen3.5-2B.q4_k_m.gguf`.

```bash
mkdir -p ~/models
curl -L https://huggingface.co/AaryanK/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B.q4_k_m.gguf -o ~/models/Qwen3.5-2B.q4_k_m.gguf
```

### 4. Copy this repo to the Jetson

From the machine where you keep the repo:

```bash
rsync -avz --delete ./ YOUR_JETSON_USER@JETSON_HOSTNAME_OR_IP:~/crankgpt/
```

### 5. Set up Python on the Jetson

```bash
cd ~/crankgpt
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 6. Run `llama-server`

The tested `Qwen3.5-2B` setup uses `--reasoning off` for clean responses.

```bash
~/llama.cpp/build-cuda-sm87/bin/llama-server \
  -m ~/models/Qwen3.5-2B.q4_k_m.gguf \
  -ngl 99 -c 512 -b 64 -ub 64 \
  --host 127.0.0.1 --port 11439 --reasoning off
```

Quick health check:

```bash
curl http://127.0.0.1:11439/health
```

### 7. Run the Flask app

```bash
export MODEL_BACKEND=llama_cpp
export LLAMA_CPP_BASE_URL=http://127.0.0.1:11439
export LLAMA_CPP_MODEL=Qwen3.5-2B.q4_k_m.gguf
export UM34C_SERIAL_PORT=/dev/rfcomm0
export UM34C_DATA_GROUP=0
export HOST=0.0.0.0
export PORT=8080
python app.py
```

Open the kiosk from another machine on the same network:

```text
http://JETSON_HOSTNAME_OR_IP:8080
```

### 8. Pair the UM34C

Pair the Bluetooth meter on the Jetson, then bind it to `/dev/rfcomm0` using the example service in `jetson/`.

```bash
bluetoothctl scan on
bluetoothctl devices
bluetoothctl pair YOUR_UM34C_BLUETOOTH_MAC
bluetoothctl trust YOUR_UM34C_BLUETOOTH_MAC
bluetoothctl connect YOUR_UM34C_BLUETOOTH_MAC
```

If your service user cannot open `/dev/rfcomm0`, add it to `dialout`.

### 9. Verify the full kiosk path

Check that the Flask app serves the kiosk page:

```bash
curl http://127.0.0.1:8080/
```

Then send a test generation request:

```bash
curl http://127.0.0.1:8080/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "What is static electricity?",
    "answer_length": "small"
  }'
```

Then open the kiosk UI in a browser and run a short prompt.

## Jetson Service Templates

The `jetson/` folder contains example systemd unit files you can adapt:

- `jetson/llama-cpp.service.example`
- `jetson/crankgpt.service.example`
- `jetson/um34c-rfcomm.service.example`

Each template uses placeholders that you replace with your own username, model path, and UM34C Bluetooth MAC address. See `jetson/README.md` for setup notes.

## Environment Variables

Primary `llama.cpp` settings:

```bash
export MODEL_BACKEND=llama_cpp
export LLAMA_CPP_BASE_URL=http://127.0.0.1:11439
export LLAMA_CPP_MODEL=Qwen3.5-2B.q4_k_m.gguf
export HOST=0.0.0.0
export PORT=8080
```

Optional Ollama fallback:

```bash
export MODEL_BACKEND=ollama
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_MODEL=llama3.2:3b
export OLLAMA_TIMEOUT_SECONDS=180
```

Jetson power measurement:

```bash
export JETSON_POWER_SENSOR_LABEL=VDD_IN
export JETSON_POWER_SAMPLE_INTERVAL_SECONDS=0.1
export JETSON_IDLE_SAMPLE_SECONDS=1.5
export JETSON_IDLE_CACHE_SECONDS=300
export JETSON_ENERGY_DEMO_MULTIPLIER=1.0
```

UM34C measurement:

```bash
export UM34C_SERIAL_PORT=/dev/rfcomm0
export UM34C_BAUDRATE=9600
export UM34C_DATA_GROUP=0
export UM34C_READ_TIMEOUT_SECONDS=2.0
export UM34C_COMMAND_SETTLE_SECONDS=0.2
```

## Session Logging

At runtime the app writes session events to:

```text
logs/session-events.jsonl
```

This file is ignored by Git and should not be committed.

## Publish-Safe Notes

- No personal IP addresses are included in this repo.
- No credentials or secrets are included in this repo.
- The UM34C service file in `jetson/` is a template and does not contain a real Bluetooth MAC address.
- Replace placeholders with your own local values when deploying.
- Private planning docs are intentionally excluded from the public repo.

## Project Visuals

- `assets/crankinator_system_diagram.png`
- `assets/crankinator_mind_map.png`
