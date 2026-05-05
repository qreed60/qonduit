#!/bin/bash
set -euo pipefail

CONTAINER_NAME="llama_server"
IMAGE_NAME="llama_cpp_cuda"
MODEL_DIR="/mnt/models/llm"
HOST_PORT="8080"
DOCKER_NETWORK="qonduit-ai-net"
BATCH_SIZE="8192"
UBATCH_SIZE="2048"

# NOTE:
# This server may include a Quadro K620 display GPU.
# Do not include non-Tesla display GPUs in llama.cpp tensor split.
# This script only includes Tesla P100 / Tesla P40 cards for inference.

# ---- INPUT FROM ROUTER / FLASK ----
MODEL_NAME="${1:-}"
CONTEXT_SIZE="${2:-}"

# ---- Defaults if not passed ----
if [ -z "$MODEL_NAME" ]; then
    echo "No model passed, defaulting to first model..."
    MODEL_NAME=$(ls "$MODEL_DIR"/*.gguf | head -n 1 | xargs -n1 basename)
fi

MODEL_PATH="$MODEL_DIR/$MODEL_NAME"

if [ -z "$CONTEXT_SIZE" ]; then
    CONTEXT_SIZE=65536
fi

echo "Model: $MODEL_PATH"
echo "Context: $CONTEXT_SIZE"

# ---- Validate model exists ----
if [ ! -f "$MODEL_PATH" ]; then
    echo "❌ Model not found: $MODEL_PATH"
    exit 1
fi

# ---- Ensure Docker network exists ----
echo "Ensuring Docker network exists: $DOCKER_NETWORK"
sudo docker network create "$DOCKER_NETWORK" 2>/dev/null || true

# ---- Detect compute GPUs and free VRAM ----
# Only include Tesla compute GPUs. Exclude display cards like Quadro K620.
echo "Detecting available compute GPUs..."

GPU_INFO=$(nvidia-smi --query-gpu=index,name,memory.free --format=csv,noheader,nounits)

if [ -z "$GPU_INFO" ]; then
    echo "❌ No NVIDIA GPUs detected."
    exit 1
fi

readarray -t GPU_LINES <<< "$GPU_INFO"

GPU_DEVICES=""
VRAMS=()

for line in "${GPU_LINES[@]}"; do
    # line format: "0, Tesla P100-PCIE-16GB, 16020"
    GPU_INDEX=$(echo "$line" | awk -F',' '{gsub(/ /,"",$1); print $1}')
    GPU_NAME=$(echo "$line" | awk -F',' '{gsub(/^ +| +$/,"",$2); print $2}')
    GPU_FREE=$(echo "$line" | awk -F',' '{gsub(/ /,"",$3); print $3}')

    if [[ "$GPU_NAME" == *"Tesla P100"* ]] || [[ "$GPU_NAME" == *"Tesla P40"* ]]; then
        GPU_DEVICES+="${GPU_INDEX},"
        VRAMS+=("$GPU_FREE")
        echo "✅ Including GPU $GPU_INDEX: $GPU_NAME (${GPU_FREE} MiB free)"
    else
        echo "🚫 Excluding GPU $GPU_INDEX: $GPU_NAME"
    fi
done

GPU_DEVICES="${GPU_DEVICES%,}"

GPU_COUNT="${#VRAMS[@]}"

if [ "$GPU_COUNT" -lt 1 ]; then
    echo "❌ No usable compute GPUs found."
    exit 1
fi

echo "Detected $GPU_COUNT compute GPU(s): $GPU_DEVICES"
echo "Free VRAM per compute GPU: ${VRAMS[*]} MiB"

# ---- Total VRAM ----
TOTAL_VRAM=0
for v in "${VRAMS[@]}"; do
    TOTAL_VRAM=$((TOTAL_VRAM + v))
done

echo "Total free compute VRAM: ${TOTAL_VRAM} MiB"

# ---- Tensor split auto ----
# Tensor split is built only from included compute GPUs.
echo "Calculating tensor split..."
SPLIT=""
for v in "${VRAMS[@]}"; do
    SPLIT+="${v},"
done
TENSOR_SPLIT="${SPLIT%,}"

echo "Tensor split: $TENSOR_SPLIT"

# ---- Stop old container ----
if [ "$(sudo docker ps -a -q -f name=^/${CONTAINER_NAME}$)" ]; then
    echo "Stopping old container..."
    sudo docker stop "$CONTAINER_NAME" || true
    sudo docker rm "$CONTAINER_NAME" || true
fi

# ---- Launch ----
echo "Launching llama-server..."

sudo docker run -d \
  --name "$CONTAINER_NAME" \
  --gpus "\"device=${GPU_DEVICES}\"" \
  --restart unless-stopped \
  --network "$DOCKER_NETWORK" \
  -p "$HOST_PORT:$HOST_PORT" \
  -v /mnt/models:/mnt/models \
  --env PYTHONUNBUFFERED=1 \
  --env QONDUIT_MODEL_NAME="$MODEL_NAME" \
  --env QONDUIT_CONTEXT_SIZE="$CONTEXT_SIZE" \
  --log-opt mode=non-blocking \
  --log-opt max-buffer-size=4m \
  --label qonduit.model="$MODEL_NAME" \
  --label qonduit.context_size="$CONTEXT_SIZE" \
  "$IMAGE_NAME" \
  ./build/bin/llama-server \
  --model "$MODEL_PATH" \
  --n-gpu-layers -1 \
  --ctx-size "$CONTEXT_SIZE" \
  --parallel 2 \
  --batch-size "$BATCH_SIZE" \
  --ubatch-size "$UBATCH_SIZE" \
  --tensor-split "$TENSOR_SPLIT" \
  --host 0.0.0.0 \
  --port "$HOST_PORT" \

echo ""
echo "✅ Server launch requested:"
echo "Model: $MODEL_PATH"
echo "Context: $CONTEXT_SIZE"
echo "Compute GPUs: $GPU_DEVICES"
echo "Tensor split: $TENSOR_SPLIT"
echo "Docker network: $DOCKER_NETWORK"
echo "URL: http://localhost:$HOST_PORT"
echo ""
echo "To watch startup logs:"
echo "sudo docker logs -f $CONTAINER_NAME"
