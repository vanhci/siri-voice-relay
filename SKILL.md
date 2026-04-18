---
name: siri-voice-relay
description: "Siri 语音呼叫 Hermes — 通过 Cloudflare Named Tunnel 实现永久固定 URL 的 Siri 语音助手方案。"
category: devops
---

# Siri 语音呼叫 Hermes

**链路**：iPhone「嘿Siri」→ 快捷指令 → HTTPS POST → Cloudflare Named Tunnel → Mac relay → Hermes Gateway API → Siri 朗读回复

```
┌──────────┐    HTTPS    ┌──────────────────┐    HTTP    ┌──────────────┐    API    ┌─────────────┐
│ iPhone  │ ──────────→ │ siri.yourdom.com  │ ─────────→ │ relay :18901 │ ────────→ │ Hermes :8642│
│ 快捷指令  │   POST /siri│  (Cloudflare)    │            │ (Python)     │           │ (Gateway)   │
└──────────┘             └──────────────────┘            └──────────────┘           └─────────────┘
```

## 组件清单

| 组件 | 位置 / 端口 | 说明 |
|------|------------|------|
| relay 脚本 | `~/.openclaw/siri-relay-simple.py`，监听 `:18901` | HTTP 服务，接受 `{text}`，转发到 Hermes API，返回纯文本 |
| daemon 脚本 | `~/.openclaw/siri-daemon.sh` | 守护进程，自动重启 relay + cloudflared |
| Cloudflare Named Tunnel | `cloudflared tunnel run <tunnel-name>` | 固定 URL，不会空闲断连 |
| Hermes Gateway API | `localhost:8642` | Agent 的 OpenAI-compatible chat API |
| 域名 | `siri.your-domain.com` | CNAME 指向 `<TUNNEL_ID>.cfargotunnel.com` |

## 部署步骤

### 1. 准备域名

- 购买域名（推荐国内注册商，支付宝付款方便）
- 在域名管理面板将 nameserver 改为 Cloudflare 的
- 在 Cloudflare Dashboard 添加域名，等待 DNS 生效

### 2. 创建 Cloudflare Named Tunnel

```bash
# 登录（浏览器交互，会弹出 Cloudflare 授权页）
cloudflared tunnel login

# 创建 tunnel（记住输出的 TUNNEL_ID）
cloudflared tunnel create <tunnel-name>

# credentials 文件在 ~/.cloudflared/<TUNNEL_ID>.json
```

### 3. 配置 `~/.cloudflared/config.yml`

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /Users/<your-username>/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: siri.your-domain.com
    service: http://localhost:18901
  - service: http_status:404
```

### 4. 添加 DNS CNAME

```bash
# 命令行自动添加
cloudflared tunnel route dns <tunnel-name> siri.your-domain.com
```

或在 Cloudflare Dashboard 手动添加：
- 类型：`CNAME`
- 名称：`siri`
- 目标：`<TUNNEL_ID>.cfargotunnel.com`
- 代理（Proxy）：开启（橙色云朵）

### 5. 编写 relay 脚本

创建 `~/.openclaw/siri-relay-simple.py`：

```python
#!/usr/bin/env python3
"""Siri Relay — 接受 {text}，调 Hermes API，返回纯文本给 Siri 朗读"""
import json
import http.server
import urllib.request
import re
import time

LISTEN_PORT = 18901
API_URL = "http://127.0.0.1:8642/v1/chat/completions"

def clean_for_tts(text):
    """清理 Markdown 标记，让 Siri 朗读更自然"""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # 粗体
    text = re.sub(r'\*(.*?)\*', r'\1', text)        # 斜体
    text = re.sub(r'`(.*?)`', r'\1', text)           # 行内代码
    text = re.sub(r'#{1,6}\s*', '', text)            # 标题
    text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)  # 列表
    text = re.sub(r'\n{3,}', '\n\n', text)           # 多余空行
    return text.strip()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/siri":
            self.send_error(404); return
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = body.get("text", "")
            if not text:
                self.send_error(400); return

            payload = json.dumps({
                "model": "hermes-agent",
                "messages": [{"role": "user", "content": text}],
                "max_tokens": 500
            }).encode()

            req = urllib.request.Request(API_URL, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                api = json.loads(resp.read())

            reply = api["choices"][0]["message"]["content"]
            reply = clean_for_tts(reply)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(reply.encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode())

    def do_GET(self):
        self._json(200, {"status": "ok"})

    def _json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]}", flush=True)

if __name__ == "__main__":
    print(f"Relay on :{LISTEN_PORT}")
    http.server.ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
```

> ⚠️ 必须用 `ThreadingHTTPServer`（不是默认的单线程 HTTPServer），否则 LLM 推理期间会阻塞其他请求。

### 6. 创建 daemon 守护脚本

创建 `~/.openclaw/siri-daemon.sh`：

```bash
#!/bin/bash
# ===== 根据你的环境修改以下路径 =====
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
export HOME="<your-home-dir>"

PYTHON="$(which python3)"                        # 或指定具体路径
RELAY="$HOME/.openclaw/siri-relay-simple.py"
CLOUDFLARED="$(which cloudflared)"
RELAY_LOG="$HOME/.openclaw/siri-daemon.log"
CHECK_INTERVAL=30
# ===== 以上根据环境修改 =====

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$RELAY_LOG"; }

start_relay() {
    $PYTHON "$RELAY" >> "$RELAY_LOG" 2>&1 &
    log "relay started (PID $!)"
}

start_cloudflared() {
    $CLOUDFLARED tunnel run <tunnel-name> >> "$RELAY_LOG" 2>&1 &
    log "cloudflared started (PID $!)"
}

cleanup() {
    log "shutting down"
    pkill -P $$ 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT

log "=== daemon start ==="

# 清理残留进程
pkill -f "siri-relay" 2>/dev/null
pkill -f "cloudflared.*<tunnel-name>" 2>/dev/null
sleep 2

start_relay
sleep 3
start_cloudflared

# 守护循环
while true; do
    sleep $CHECK_INTERVAL
    if ! pgrep -f "siri-relay" > /dev/null 2>&1; then
        log "relay died, restarting"; start_relay
    fi
    if ! pgrep -f "cloudflared.*<tunnel-name>" > /dev/null 2>&1; then
        log "cloudflared died, restarting"; start_cloudflared
    fi
done
```

```bash
chmod +x ~/.openclaw/siri-daemon.sh
```

### 7. LaunchAgent 开机自启（可选）

创建 `~/Library/LaunchAgents/com.openclaw.siri.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.openclaw.siri</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/<your-username>/.openclaw/siri-daemon.sh</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key>
        <string>/Users/<your-username>/.openclaw/launchd-stdout.log</string>
    <key>StandardErrorPath</key>
        <string>/Users/<your-username>/.openclaw/launchd-stderr.log</string>
    <key>WorkingDirectory</key>
        <string>/Users/<your-username>/.openclaw</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.openclaw.siri.plist
```

### 8. 验证

```bash
# 本地测试（必须 POST）
curl -s -m 60 -X POST -H "Content-Type: application/json" \
  -d '{"text":"你好"}' http://localhost:18901/siri

# 公网测试（模型推理约 9 秒，超时设 15s+）
curl -s -m 20 -X POST -H "Content-Type: application/json" \
  -d '{"text":"你好"}' https://siri.your-domain.com/siri

# 检查进程
ps aux | grep -E 'siri-relay|cloudflared' | grep -v grep

# 检查端口
lsof -i :18901
lsof -i :8642
```

### 9. 配置 iPhone 快捷指令

| 步骤 | 动作 | 配置 |
|------|------|------|
| 1 | **听写文本** | 语言：中文 |
| 2 | **获取URL内容** | URL: `https://siri.your-domain.com/siri`<br>方法: POST<br>请求体: `{"text": "听写文本"}`<br>Content-Type: `application/json` |
| 3 | **朗读文本** | 直接朗读「获取URL内容」的输出 |

> ⚠️ 返回的是 `text/plain` 纯文本，不需要额外解析 JSON。
> ⚠️ 快捷指令超时建议设 ≥60 秒（HTTPS 握手 + 模型推理可能较久）。

## 排障

```bash
# 1. 确认进程状态
ps aux | grep -E 'siri-relay|cloudflared' | grep -v grep

# 2. 本地 relay 测试
curl -s -m 10 -X POST -H "Content-Type: application/json" \
  -d '{"text":"test"}' http://localhost:18901/siri

# 3. 端口占用检查
lsof -i :18901

# 4. 公网可达测试
curl -s -m 20 -X POST -H "Content-Type: application/json" \
  -d '{"text":"ping"}' https://siri.your-domain.com/siri

# 5. DNS 解析
dig +short siri.your-domain.com

# 6. daemon 日志
tail -20 ~/.openclaw/siri-daemon.log
```

### 常见故障

**Siri 说"出错请重试"**
1. 检查快捷指令 URL 是否正确（必须是 `https://域名/siri`，注意路径不能少）
2. 公网 `curl` 测试能通吗？
3. 如果短文本正常、长文本出错 → 是 iPhone Shortcuts 内置超时限制（见下方）

### ⚠️ iPhone 快捷指令内置超时限制（2026-04-18 发现）

iOS 快捷指令「获取URL内容」动作有**硬编码的网络请求超时（约 30 秒）**，无法通过任何设置修改（高级设置里没有超时选项）。

**影响**：模型推理超过 30 秒的请求会直接被 iOS 中断，Siri 报"出错请重试"。

**表现**：
- 短文本（5-10字）→ 正常（推理 ~6-10秒）
- 长文本或复杂任务 → 超过 30 秒 → 出错
- `curl` 测试正常但 Siri 报错 → 大概率是这个原因

**应对方案**：
1. **Siri 只问简单问题**（10字以内），复杂任务走微信/飞书
2. relay 脚本中 `max_tokens` 设低（如 200），缩短回复长度以加速推理
3. 如果必须支持长对话，考虑换更快的模型（如降级模型）处理 Siri 请求
4. **不要尝试修改超时** — 这是 iOS 系统限制，快捷指令和 Cloudflare 配置都无法改变

**curl 通但 Siri 不通**
- iPhone 可能在不同网络下或走蜂窝流量 → 确认 URL 公网可达
- 快捷指令超时太短

**Tunnel 断了（curl 超时）**
- `pgrep -fl cloudflared` 确认进程在吗？
- 重启：`kill <pid> && cloudflared tunnel run <tunnel-name> &`

**端口 18901 被占用**
- 可能有残留的 ngrok/localtunnel 进程：`ps aux | grep -E 'lt|ngrok|localtunnel'`
- 杀掉残留进程后重启 daemon

**daemon 重启后隧道起不来**
- 检查 `tail -20 ~/.openclaw/siri-daemon.log`
- 确认 daemon 脚本里是 `cloudflared tunnel run`（不是旧的 ngrok 命令）

**relay 崩溃循环，日志出现 `ModuleNotFoundError`**
- relay 可能依赖 `emoji` 包（用于清理 TTS 不支持的 emoji 字符）
- 修复：`pip3 install emoji`（用 daemon 里指定的同一个 Python 的 pip）
- 验证：`tail -5 ~/.openclaw/siri-daemon.log` 不再出现 traceback

**cloudflared tunnel 连接慢**
- 正常现象，Cloudflare 建立连接可能需要 10-30 秒
- curl 超时设 20 秒以上

## 关键要点

- relay 必须用 `ThreadingHTTPServer`，不要用单线程的
- 返回 `text/plain` 纯文本给 Siri 直接朗读（不要返回 JSON）
- Named Tunnel 不会空闲断连（区别于快速隧道 / ngrok）
- 模型推理约 9 秒，curl 超时设 15 秒以上，快捷指令超时 ≥60 秒
- daemon 脚本只管 relay + cloudflared，不要混用 ngrok/localtunnel（会端口冲突）
- 旧 LaunchAgent（如 `com.hermes.siri-relay` / `com.hermes.siri-tunnel`）必须删除，只保留 `com.openclaw.siri`

## 清理旧配置（迁移时用）

```bash
# 卸载旧 LaunchAgent
launchctl unload ~/Library/LaunchAgents/com.hermes.siri-relay.plist 2>/dev/null
launchctl unload ~/Library/LaunchAgents/com.hermes.siri-tunnel.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.hermes.siri-relay.plist
rm -f ~/Library/LaunchAgents/com.hermes.siri-tunnel.plist
```
