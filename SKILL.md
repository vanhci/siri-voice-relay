---
name: siri-voice-relay
description: "Siri 语音呼叫 Hermes — 通过 Cloudflare Named Tunnel 实现公网固定 URL，让 iPhone 快捷指令调用 Hermes API。"
category: devops
---

# Siri 语音呼叫 Hermes

iPhone 快捷指令 → Cloudflare Named Tunnel → Mac relay → Hermes API → Siri 朗读

## 架构

```
iPhone "嘿Siri" → 快捷指令 → HTTPS POST → siri.<你的域名>/siri → localhost:<relay端口> → Hermes API (<hermes端口>)
```

## 组件

| 组件 | 说明 |
|------|------|
| relay 脚本 | HTTP 服务，接受 `{text}`，转发到 Hermes API |
| Cloudflare Named Tunnel | 固定 URL，不会空闲断连（区别于快速隧道） |
| Hermes API | Gateway API server，默认 `localhost:<hermes端口>` |
| 域名 | CNAME 指向 `<tunnel-id>.cfargotunnel.com` |

## 部署步骤

### 1. 准备域名

- 购买域名（国内推荐阿里云，支付宝付款免国际身份验证）
- 在域名管理面板将 nameserver 改为 Cloudflare 的
- 在 Cloudflare Dashboard 添加域名，等待 DNS 生效

### 2. 创建 Named Tunnel

```bash
# 登录（浏览器交互）
cloudflared tunnel login

# 创建 tunnel
cloudflared tunnel create <tunnel-name>

# 记下 tunnel ID，credentials 文件在 ~/.cloudflared/<TUNNEL_ID>.json
```

### 3. 配置 ~/.cloudflared/config.yml

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /Users/<用户名>/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: siri.<你的域名>
    service: http://localhost:<relay端口>
  - service: http_status:404
```

### 4. 添加 DNS CNAME

```bash
cloudflared tunnel route dns <tunnel-name> siri.<你的域名>
```

或在 Cloudflare Dashboard 手动添加：
- 类型：CNAME
- 名称：siri
- 目标：`<TUNNEL_ID>.cfargotunnel.com`
- 代理：开启

### 5. 写 relay 脚本

```python
#!/usr/bin/env python3
"""Siri Relay — 接受 {text}，调 Hermes API，返回纯文本"""
import json, http.server, urllib.request, re, time

LISTEN_PORT = <relay端口>
API_URL = "http://127.0.0.1:<hermes端口>/v1/chat/completions"

def clean_for_tts(text):
    """清理 Markdown 格式，让 Siri 能顺畅朗读"""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'`(.*?)`', r'\1', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
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

**关键点**：
- 必须用 `ThreadingHTTPServer`（单线程会阻塞 LLM 推理期间的其他请求）
- 返回 `text/plain` 纯文本，让 Siri 直接朗读（不要返回 JSON）
- `clean_for_tts()` 清理 Markdown 格式符号

### 6. 启动 relay + tunnel（daemon 脚本统一管理）

```bash
# 直接运行 daemon 脚本（推荐方式）
bash ~/.openclaw/siri-daemon.sh &

# 或者用 launchd 开机自启
launchctl load ~/Library/LaunchAgents/com.<你的标签>.siri.plist
```

#### daemon 脚本模板 `~/.openclaw/siri-daemon.sh`

```bash
#!/bin/bash
export PATH="<你的PATH>"
export HOME="/Users/<用户名>"

PYTHON="<python路径>"
RELAY="$HOME/.openclaw/siri-relay-simple.py"
CLOUDFLARED="/opt/homebrew/bin/cloudflared"
RELAY_LOG="$HOME/.openclaw/siri-daemon.log"
CHECK_INTERVAL=30

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

# 杀残留进程
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

#### LaunchAgent 模板

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.<你的标签>.siri</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/<用户名>/.openclaw/siri-daemon.sh</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardOutPath</key>
        <string>/Users/<用户名>/.openclaw/launchd-stdout.log</string>
    <key>StandardErrorPath</key>
        <string>/Users/<用户名>/.openclaw/launchd-stderr.log</string>
    <key>WorkingDirectory</key><string>/Users/<用户名>/.openclaw</string>
</dict>
</plist>
```

### 7. 验证

```bash
# 本地测试
curl -s -m 60 -X POST -H "Content-Type: application/json" \
  -d '{"text":"你好"}' http://localhost:<relay端口>/siri

# 公网测试
curl -s -m 60 -X POST -H "Content-Type: application/json" \
  -d '{"text":"你好"}' https://siri.<你的域名>/siri
```

### 8. 配置 iPhone 快捷指令

| 步骤 | 动作 | 配置 |
|------|------|------|
| 1 | **听写文本** | 语言：中文 |
| 2 | **获取URL内容** | URL: `https://siri.<你的域名>/siri`<br>方法: POST<br>请求体: `{}`，把听写文本设到 `text` 字段<br>Content-Type: `application/json` |
| 3 | **朗读文本** | 直接朗读「获取URL内容」的输出 |

**注意**：
- 返回的是 `text/plain` 纯文本，不需要额外解析 JSON
- iOS 快捷指令超时默认较短，需手动加长（≥60 秒）

## 排障

```bash
# 1. 确认进程状态
ps aux | grep -E 'siri-relay|cloudflared' | grep -v grep

# 2. 本地 relay 测试
curl -s -m 10 -X POST -H "Content-Type: application/json" \
  -d '{"text":"test"}' http://localhost:<relay端口>/siri

# 3. 端口占用检查
lsof -i :<relay端口>

# 4. 公网可达测试
curl -s -m 20 -X POST -H "Content-Type: application/json" \
  -d '{"text":"ping"}' https://siri.<你的域名>/siri

# 5. DNS 解析
dig +short siri.<你的域名>

# 6. Hermes API
lsof -i :<hermes端口>

# 7. daemon 日志
tail -20 ~/.openclaw/siri-daemon.log
```

### 常见故障

| 症状 | 排查 |
|------|------|
| Siri 说"出错请重试" | 检查快捷指令 URL 是否为公网地址；检查超时是否 ≥60 秒 |
| curl 通但 Siri 不通 | iPhone 可能在不同网络下；确认 URL 公网可达 |
| Tunnel 断了 | `pgrep -fl cloudflared` 检查进程；重启 `cloudflared tunnel run <tunnel-name>` |
| 端口被占用 | 检查残留 ngrok/localtunnel 进程 `ps aux \| grep -E 'lt\|ngrok'` |
| daemon 重启后隧道起不来 | 检查 daemon 日志；确认用的是 `cloudflared tunnel run` 不是 ngrok |
| relay 崩溃循环 | 检查依赖包：`pip3 install emoji`（如果 clean_for_tts 用了 emoji 清理） |

## 关键要点

- **Named Tunnel** 不会空闲断连，优于快速隧道/localtunnel/ngrok
- 返回 **`text/plain`** 纯文本让 Siri 直接朗读
- relay 必须用 **`ThreadingHTTPServer`**
- 模型推理约 9 秒，curl 超时设 15 秒以上
- daemon 只管 relay + cloudflared，**不要混用** ngrok/localtunnel（端口冲突）
- 旧 LaunchAgent（hermes.siri-relay/hermes.siri-tunnel）必须删除，只保留一个

## 占位符清单

部署时需替换以下占位符：

| 占位符 | 说明 |
|--------|------|
| `<你的域名>` | 你的域名，如 `siri.example.com` |
| `<tunnel-name>` | Cloudflare tunnel 名称 |
| `<TUNNEL_ID>` | 创建 tunnel 后获得的 ID |
| `<用户名>` | Mac 用户名 |
| `<relay端口>` | relay 脚本监听端口（如 18901） |
| `<hermes端口>` | Hermes API 端口（如 8642） |
| `<你的标签>` | LaunchAgent 标签前缀 |
| `<python路径>` | Python 可执行文件路径 |
| `<你的PATH>` | 环境 PATH 变量 |
