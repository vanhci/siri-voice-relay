#!/bin/bash
# =============================================================================
# Siri Voice Relay Daemon — 守护 relay + cloudflared，自动重启
# =============================================================================
#
# 用法:
#   bash siri-daemon.sh                    # 前台运行
#   bash siri-daemon.sh >> siri-daemon.log 2>&1 &  # 后台运行
#
# 环境变量（可在下方修改或通过 export 设置）:
#   TUNNEL_NAME        Cloudflare tunnel 名称（默认 <tunnel-name>）
#   RELAY_PORT         Relay 监听端口（默认 18901）
#   LLM_API_URL        LLM API 地址
#   CHECK_INTERVAL     健康检查间隔秒数（默认 30）
# =============================================================================

set -euo pipefail

# ─── 配置 ────────────────────────────────────────────────────────────────────
TUNNEL_NAME="${TUNNEL_NAME:-<tunnel-name>}"
RELAY_PORT="${RELAY_PORT:-18901}"
LLM_API_URL="${LLM_API_URL:-http://127.0.0.1:8642/v1/chat/completions}"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"

# 自动检测路径（按优先级）
PYTHON="${PYTHON:-$(which python3.11 2>/dev/null || which python3 2>/dev/null)}"
CLOUDFLARED="${CLOUDFLARED:-$(which cloudflared 2>/dev/null)}"
RELAY_SCRIPT="${RELAY_SCRIPT:-$(dirname "$0")/siri-relay.py}"

LOG_FILE="${LOG_FILE:-$(dirname "$0")/siri-daemon.log}"

# ─── 日志 ────────────────────────────────────────────────────────────────────
log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

# ─── 前置检查 ─────────────────────────────────────────────────────────────────
check_deps() {
    local missing=()
    [[ -x "$PYTHON" ]]      || missing+=("python3 (not found at $PYTHON)")
    [[ -x "$CLOUDFLARED" ]] || missing+=("cloudflared (not found at $CLOUDFLARED)")
    [[ -f "$RELAY_SCRIPT" ]] || missing+=("siri-relay.py (not found at $RELAY_SCRIPT)")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log "❌ Missing dependencies:"
        for m in "${missing[@]}"; do log "   - $m"; done
        exit 1
    fi
}

# ─── 启动 relay ──────────────────────────────────────────────────────────────
start_relay() {
    LLM_API_URL="$LLM_API_URL" "$PYTHON" "$RELAY_SCRIPT" --port "$RELAY_PORT" >> "$LOG_FILE" 2>&1 &
    log "✅ relay started (PID $!, port $RELAY_PORT)"
}

# ─── 启动 cloudflared ────────────────────────────────────────────────────────
start_cloudflared() {
    "$CLOUDFLARED" tunnel run "$TUNNEL_NAME" >> "$LOG_FILE" 2>&1 &
    log "✅ cloudflared started (PID $!, tunnel=$TUNNEL_NAME)"
}

# ─── 清理 ────────────────────────────────────────────────────────────────────
cleanup() {
    log "🛑 Shutting down daemon..."
    pkill -P $$ 2>/dev/null || true
    # 确保子进程也退出
    pkill -f "siri-relay.py" 2>/dev/null || true
    pkill -f "cloudflared.*$TUNNEL_NAME" 2>/dev/null || true
    exit 0
}

trap cleanup SIGTERM SIGINT

# ─── 主流程 ───────────────────────────────────────────────────────────────────
log "═══════════════════════════════════════════════════════════"
log "🚀 Siri Voice Relay Daemon starting"
log "   Python:     $PYTHON"
log "   cloudflared: $CLOUDFLARED"
log "   Relay:      $RELAY_SCRIPT"
log "   Port:       $RELAY_PORT"
log "   Tunnel:     $TUNNEL_NAME"
log "   API:        $LLM_API_URL"
log "═══════════════════════════════════════════════════════════"

check_deps

# 清理残留进程
pkill -f "siri-relay.py" 2>/dev/null && log "🧹 killed old relay" || true
pkill -f "cloudflared.*$TUNNEL_NAME" 2>/dev/null && log "🧹 killed old cloudflared" || true
sleep 2

# 启动
start_relay
sleep 3
start_cloudflared

# ─── 守护循环 ─────────────────────────────────────────────────────────────────
while true; do
    sleep "$CHECK_INTERVAL"

    if ! pgrep -f "siri-relay.py" > /dev/null 2>&1; then
        log "⚠️  relay died, restarting..."
        start_relay
    fi

    if ! pgrep -f "cloudflared.*$TUNNEL_NAME" > /dev/null 2>&1; then
        log "⚠️  cloudflared died, restarting..."
        start_cloudflared
    fi
done
