#!/usr/bin/env bash
#
# deploy_router_api.sh — Deploy host router API files to /opt and restart service.
#
# This script can be run independently or called by update.sh.
# It deploys the router companion modules, ensures .env defaults,
# compiles Python files, restarts the service, and validates health.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_FILE="${SCRIPT_DIR}/router_api_files.txt"

DEST_DIR="/opt"
ENV_FILE="/opt/qonduit-router-api/.env"
DATA_DIR="/opt/qonduit-router-api/data"
SERVICE_NAME="qonduit-router-api.service"
BASE_URL="http://127.0.0.1:5001"

# ── Functions ────────────────────────────────────────────────────────────────

deploy_files() {
    local repo_dir="$1"
    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    local deployed=0
    local failed=0

    # Load file list from manifest
    local router_files=()
    if [[ -f "$MANIFEST_FILE" ]]; then
        while IFS= read -r f || [[ -n "$f" ]]; do
            # Skip empty lines and comments
            [[ -z "$f" || "$f" == \#* ]] && continue
            router_files+=("$f")
        done < "$MANIFEST_FILE"
    else
        echo "  [ERROR] Manifest file not found: $MANIFEST_FILE"
        return 1
    fi

    for f in "${router_files[@]}"; do
        local src="${repo_dir}/${f}"
        local dst="${DEST_DIR}/${f}"

        if [[ ! -f "$src" ]]; then
            echo "  [SKIP] $f — not found in repo"
            continue
        fi

        # Stop service before deploying
        echo "  [DEPLOY] $f → $dst"
        if command -v sudo &>/dev/null; then
            sudo cp "$src" "$dst"
            sudo chmod 644 "$dst"
        else
            cp "$src" "$dst"
            chmod 644 "$dst"
        fi
        deployed=$((deployed + 1))
    done

    # Check if any files were actually deployed
    if [[ $deployed -eq 0 ]]; then
        echo "  [WARN] No router files were deployed."
        return 1
    fi

    echo "  [OK] Deployed $deployed file(s) to $DEST_DIR"
    return 0
}

ensure_env() {
    echo "[ENV] Ensuring $ENV_FILE with required defaults..."

    local env_dir
    env_dir=$(dirname "$ENV_FILE")
    mkdir -p "$env_dir"
    mkdir -p "$DATA_DIR"

    # Create or update .env with required keys
    if [[ ! -f "$ENV_FILE" ]]; then
        cat > "$ENV_FILE" <<'ENVEOF'
QONDUIT_ROUTER_DATA_DIR=/opt/qonduit-router-api/data
PYTHONPATH=/opt
PYTHONDONTWRITEBYTECODE=1
QONDUIT_GPU_MIN_TOTAL_MIB=8192
QONDUIT_GPU_EXCLUDE_NAME_REGEX=K620|Quadro K620
QONDUIT_DEFAULT_GPU_DEVICES=auto
QONDUIT_ROUTER_ALLOW_LAN=true
ENVEOF
        echo "  [OK] Created $ENV_FILE"
    else
        # Ensure each key exists (add if missing, preserve user overrides)
        local -a keys=(
            "QONDUIT_ROUTER_DATA_DIR=/opt/qonduit-router-api/data"
            "PYTHONPATH=/opt"
            "PYTHONDONTWRITEBYTECODE=1"
            "QONDUIT_GPU_MIN_TOTAL_MIB=8192"
            "QONDUIT_GPU_EXCLUDE_NAME_REGEX=K620|Quadro K620"
            "QONDUIT_DEFAULT_GPU_DEVICES=auto"
            "QONDUIT_ROUTER_ALLOW_LAN=true"
        )
        local added=0
        for kv in "${keys[@]}"; do
            local key="${kv%%=*}"
            if ! grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
                echo "${kv}" >> "$ENV_FILE"
                added=$((added + 1))
            fi
        done
        if [[ $added -gt 0 ]]; then
            echo "  [OK] Added $added missing key(s) to $ENV_FILE"
        else
            echo "  [OK] $ENV_FILE already has all required keys"
        fi
    fi
}

compile_files() {
    echo "[COMPILE] Compiling deployed Python files..."
    local compiled=0
    local errors=0

    for f in "${ROUTER_FILES[@]}"; do
        local dst="${DEST_DIR}/${f}"
        if [[ ! -f "$dst" ]]; then
            echo "  [SKIP] $dst — not deployed, skipping compile"
            continue
        fi
        if PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile "$dst" 2>&1; then
            compiled=$((compiled + 1))
            echo "  [OK] $dst compiled"
        else
            echo "  [FAIL] $dst compilation failed"
            errors=$((errors + 1))
        fi
    done

    if [[ $errors -gt 0 ]]; then
        echo "  [ERROR] $errors file(s) failed compilation"
        return 1
    fi

    echo "  [OK] Compiled $compiled file(s)"
    return 0
}

restart_service() {
    echo "[SERVICE] Restarting $SERVICE_NAME..."

    if ! systemctl list-unit-files "${SERVICE_NAME}" &>/dev/null; then
        echo "  [WARN] $SERVICE_NAME not found — skipping restart"
        return 1
    fi

    if command -v sudo &>/dev/null; then
        sudo systemctl daemon-reload
        sudo systemctl restart "$SERVICE_NAME"
        sleep 2
        local status
        status=$(sudo systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)
        echo "  [OK] Service status: $status"
    else
        systemctl daemon-reload
        systemctl restart "$SERVICE_NAME"
        sleep 2
        local status
        status=$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)
        echo "  [OK] Service status: $status"
    fi
    return 0
}

validate_health() {
    echo "[HEALTH] Validating router API health..."
    local ok=true

    # Health check
    local health
    health=$(curl -sf --max-time 5 "${BASE_URL}/api/v1/qonduit-router/health" 2>/dev/null || true)
    if [[ -n "$health" ]]; then
        echo "  [OK] /health returned: $health"
    else
        echo "  [FAIL] /health did not respond"
        ok=false
    fi

    # Slots check
    local slots
    slots=$(curl -sf --max-time 5 "${BASE_URL}/api/v1/qonduit-router/slots" 2>/dev/null || true)
    if [[ -n "$slots" ]]; then
        echo "  [OK] /slots responded"
    else
        echo "  [WARN] /slots did not respond"
        ok=false
    fi

    # GPU check
    local gpu
    gpu=$(curl -sf --max-time 5 "${BASE_URL}/api/v1/qonduit-router/gpu" 2>/dev/null || true)
    if [[ -n "$gpu" ]]; then
        echo "  [OK] /gpu responded"
    else
        echo "  [WARN] /gpu did not respond"
        ok=false
    fi

    if $ok; then
        return 0
    else
        return 1
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    echo "============================================"
    echo "  Qonduit Router API Deployment"
    echo "============================================"
    echo ""

    local repo_dir="${1:-/opt/qonduit-repo}"

    if [[ ! -d "$repo_dir" ]]; then
        echo "[ERROR] Repo directory not found: $repo_dir"
        exit 1
    fi

    local deploy_ok=false
    local compile_ok=false
    local restart_ok=false
    local health_ok=false

    # 1. Deploy files
    echo "[1/5] Deploying router files..."
    if deploy_files "$repo_dir"; then
        deploy_ok=true
    fi
    echo ""

    if ! $deploy_ok; then
        echo "No router files deployed. Skipping compile, restart, and validation."
        echo ""
        echo "Summary:"
        echo "  gateway updated: yes (external)"
        echo "  router files deployed: no"
        echo "  router service restarted: no"
        echo "  router health: N/A"
        exit 0
    fi

    # 2. Ensure .env
    echo "[2/5] Ensuring .env..."
    ensure_env
    echo ""

    # 3. Compile
    echo "[3/5] Compiling..."
    if compile_files; then
        compile_ok=true
    else
        echo "[ERROR] Compilation failed. Not restarting service."
        echo "        Restore backups and fix errors before retrying."
        exit 1
    fi
    echo ""

    # 4. Restart service
    echo "[4/5] Restarting service..."
    if restart_service; then
        restart_ok=true
    fi
    echo ""

    # 5. Validate
    echo "[5/5] Validating health..."
    if validate_health; then
        health_ok=true
    fi
    echo ""

    # ── Summary ──────────────────────────────────────────────────────────
    echo "============================================"
    echo "  Deployment Summary"
    echo "============================================"
    echo "  Router files deployed:     $deploy_ok"
    echo "  Router files compiled:     $compile_ok"
    echo "  Router service restarted:  $restart_ok"
    echo "  Router health check:       $health_ok"
    echo ""

    # GPU summary
    local usable_gpus
    usable_gpus=$(curl -sf --max-time 5 "${BASE_URL}/api/v1/qonduit-router/gpu" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('usable_gpu_devices','N/A'))" 2>/dev/null || echo "N/A")
    echo "  Usable GPUs:               $usable_gpus"

    # Primary slot container name
    local primary_container
    primary_container=$(curl -sf --max-time 5 "${BASE_URL}/api/v1/qonduit-router/slots" 2>/dev/null \
        | python3 -c "
import sys,json
d=json.load(sys.stdin)
for s in d.get('slots',[]):
    if s.get('slot_id')=='primary':
        print(s.get('container_name','N/A'))
        break
else:
    print('N/A')
" 2>/dev/null || echo "N/A")
    echo "  Primary container name:    $primary_container"
    echo "============================================"

    if $health_ok; then
        echo "  [OK] Router API deployment successful"
    else
        echo "  [WARN] Deployment completed but health check failed"
        exit 1
    fi
}

main "$@"
