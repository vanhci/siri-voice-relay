---
name: siri-voice-relay
description: "Siri 语音呼叫 Hermes — 通过 Cloudflare Named Tunnel 实现永久固定 URL 的 Siri 语音助手方案。"
category: devops
---

# Siri 语音呼叫 Hermes

**链路**：iPhone「嘿Siri」→ 快捷指令 → HTTPS POST → Cloudflare Named Tunnel → Mac relay → Hermes Gateway API → Siri 朗读回复

```
┌──────────┐    HTTPS    ┌──────────────────┐    HTTP    ┌──────────────┐    API    ┌─────────────┐
│ iPhone  │ ──────────→ │ siri.yourdomain.com  │ ─────────→ │ relay :18901 │ ────────→ │ Hermes :8642│
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

### 常见故障排查决策树

**Siri 说"出错请重试"**

先做这一步（最关键）：
```bash
tail -f ~/.openclaw/siri-daemon.log
```
然后在 iPhone 上触发 Siri，观察日志：

| 观察结果 | 说明 | 下一步 |
|----------|------|--------|
| 日志出现新 `dest=https://...` 请求 | 请求到达了 | 检查是否超时（见 iOS 超时章节） |
| 日志**完全无变化** | iPhone 请求没到达服务器 | 检查 iPhone 网络/VPN/DNS |
| 日志显示 `context canceled` | iOS 主动断开了 | 模型推理太慢，优化 relay |
| relay 日志有 `TimeoutError` + `BrokenPipeError` | Hermes 未及时响应 | 同上，加 system prompt + 降 max_tokens |

### ⚠️ iPhone 快捷指令内置超时限制（2026-04-18 发现）

iOS 快捷指令「获取URL内容」动作有**硬编码的网络请求超时（约 30 秒）**，无法通过任何设置修改（高级设置里没有超时选项）。

**影响**：模型推理超过 30 秒的请求会直接被 iOS 中断，Siri 报"出错请重试"。

**表现**：
- 短文本（5-10字）→ 正常（推理 ~6-10秒）
- 长文本或复杂任务 → 超过 30 秒 → 出错
- `curl` 测试正常但 Siri 报错 → 大概率是这个原因

**应对方案（已验证有效 — 2026-04-20 实测公网从 19.7s 降到 13s）**：
1. **relay 中加 system prompt 约束回复长度**：
   ```python
   messages = [
       {"role": "system", "content": "你是Siri语音助手。回答必须极简：1-2句话，不超过50字。不要列举，不要格式，纯口语化。"},
       {"role": "user", "content": txt}
   ]
   payload = json.dumps({"model": MODEL, "messages": messages, "max_tokens": 200}).encode()
   ```
2. `max_tokens` 从 500 降到 **200**
3. 如果仍然超时，考虑换更快的模型处理 Siri 请求
4. **不要尝试修改超时** — 这是 iOS 系统限制，快捷指令和 Cloudflare 配置都无法改变

**排障：如何判断是 iOS 超时？**
- daemon 日志出现 `context canceled` + `dest=https://siri.your-domain.com/siri` = iOS 主动断开
- relay 日志出现 `TimeoutError: timed out` + `BrokenPipeError` = Hermes 还没响应 iOS 就断了
- `curl -m 30 https://siri.your-domain.com/siri` 测端到端时间，超过 20s 就有超时风险
### ⚠️ iOS 快捷指令内置超时限制

1. **实时监控 daemon 日志**（最关键）：
   ```bash
   tail -f ~/.openclaw/siri-daemon.log
   ```
   然后让用户在 iPhone 上触发 Siri。
   - **日志出现新条目** → 请求到达了，问题是超时或 relay 处理失败
   - **日志没有任何变化** → iPhone 的请求根本没到达服务器，问题是 iPhone 网络层

2. **如果请求没到达（日志无变化）**：
   - iPhone 上有没有开 VPN？（VPN 可能拦截/重定向 HTTPS 请求）
   - 在 iPhone Safari 打开 `https://siri.your-domain.com`，看能否访问（应返回 501）
   - 切换 WiFi ↔ 蜂窝测试
   - 检查 iPhone DNS 设置

3. **如果请求到达但超时**：
   - `curl -m 30 https://siri.your-domain.com/siri` 测端到端时间
   - 超过 20s → relay 的 max_tokens 还太高或模型太慢
>>>>>>> 90a4155 (添加SKILL.md技能文档，清理真实地址信息，使用通用占位符)

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

## 异步 Relay 架构（2026-04-20 验证）

当 o3 等慢模型推理超过 30 秒（iOS 硬超时），无论怎么调 `max_tokens` 都不够用。解决方案：**秒回 + 后台处理 + 异步推送**。

### 方案 B：文件桥接 + cron 轮询（推荐，当前使用）

```
iPhone Siri → POST /siri → relay 秒回"收到"（<2s）
                                ↓ 后台线程
                          调 Hermes 模型 → 写入 siri-result.txt
                                ↓ 每 10 秒
                          Cron 轮询文件 → deliver: origin → 推回原渠道（微信/飞书）
```

**优势：** 无 emoji 依赖、无 ilinkai token 管理、无事件循环 bug、结果自动推回消息来源。

relay 核心代码：

```python
import threading
from pathlib import Path

RESULT_FILE = Path("/Users/<your-username>/.openclaw/siri-result.txt")

def write_result(text):
    RESULT_FILE.write_text(text, encoding="utf-8")

def background_process(text):
    try:
        reply = call_model(text)
        write_result(reply)
    except Exception as e:
        write_result(f"❌ Siri 处理出错：{e}")

class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/siri":
            self.send_error(404); return
        ln = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(ln))
        txt = body.get("text", "")
        self._r(200, "收到，正在处理中")
        threading.Thread(target=background_process, args=(txt,), daemon=True).start()
```

Cron 任务配置：

```json
{
  "job_id": "siri-feishu-bridge",
  "name": "Siri Result Bridge",
  "schedule": "*/10 * * * * *",
  "prompt": "检查 /Users/<your-username>/.openclaw/siri-result.txt，有内容就读取、清空、输出内容；为空输出空字符串。",
  "deliver": "origin"
}
```

### 方案 A：ilinkai 直推微信（已弃用）

旧方案，通过 ilinkai API 直接调用微信发送接口。问题：依赖 emoji 包、context_token 易过期、Hermes 异步事件循环冲突。见下方历史文档保留备查。

### ilinkai 微信发送 API（2026-04-20 发现）

relay 可以直接调 ilinkai API 发微信，无需经过 Hermes gateway。

**关键发现**：payload 顶层 key 是 `msg`（不是 `message`），必须带 `context_token`。

```python
def send_wechat(text):
    message = {
        "from_user_id": "",
        "to_user_id": OPEN_ID,
        "client_id": f"hermes-siri-{uuid.uuid4().hex[:16]}",
        "message_type": 2,                  # MSG_TYPE_BOT
        "message_state": 2,                 # MSG_STATE_FINISH
        "context_token": CONTEXT_TOKEN,     # 从 context-tokens.json 读取，必须！
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }
    body = json.dumps(
        {"msg": message, "base_info": {"channel_version": "2.2.0"}},
        ensure_ascii=False, separators=(",", ":")
    )
    url = "https://ilinkai.weixin.qq.com/ilink/bot/sendmessage"
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {TOKEN}",
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": str((2 << 16) | (2 << 8) | 0),
    }
    req = urllib.request.Request(url, data=body.encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())  # 成功返回 {}
```

**所需凭证**（从 Hermes 配置目录读取）：
- `WEIXIN_TOKEN` — Hermes 环境变量
- 用户 open_id — 从 `WEIXIN_HOME_CHANNEL` 环境变量获取
- `context_token` — 从 `weixin/accounts/<account_id>.context-tokens.json` 文件读取

**⚠️ context_token 会过期**：返回 `{"ret": -2}` 说明 token 失效，需从 gateway token store 重新读取。

### 调试 ilinkai API

| 返回值 | 含义 |
|--------|------|
| `{}` | 成功 |
| `{"ret": -2}` | 认证失败 / context_token 无效 / payload 格式错误 |
| `{"ret": -14}` | 会话过期 |

排障：先用独立脚本调 API 验证 → 确认 payload 用 `msg` 不是 `message` → 确认 token 完整。

### ⚠️ Relay Python 环境

- **文件桥接 + cron 轮询**是当前推荐方案 — 无 emoji 依赖、无 ilinkai token 管理、结果自动推回原渠道
- ilinkai 直推方案（方案 A）已弃用 — context_token 易过期、事件循环冲突多
- 异步模式是解决 iOS 30s 超时的终极方案

## 关键要点

- relay 必须用 `ThreadingHTTPServer`，不要用单线程的
- 返回 `text/plain` 纯文本给 Siri 直接朗读（不要返回 JSON）
- Named Tunnel 不会空闲断连（区别于快速隧道 / ngrok）
- 模型推理约 9 秒，curl 超时设 15 秒以上，快捷指令超时 ≥60 秒
- daemon 脚本只管 relay + cloudflared，不要混用 ngrok/localtunnel（会端口冲突）
- 旧 LaunchAgent（如 `com.hermes.siri-relay` / `com.hermes.siri-tunnel`）必须删除，只保留 `com.openclaw.siri`
- ilinkai sendmessage API 的 payload key 是 `msg` 不是 `message`，这是最常见的坑

## 清理旧配置（迁移时用）

```bash
# 卸载旧 LaunchAgent
launchctl unload ~/Library/LaunchAgents/com.hermes.siri-relay.plist 2>/dev/null
launchctl unload ~/Library/LaunchAgents/com.hermes.siri-tunnel.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.hermes.siri-relay.plist
rm -f ~/Library/LaunchAgents/com.hermes.siri-tunnel.plist
```

## 项目清理与分享

将项目公开分享前，需要清理个人信息和配置占位符：

### 1. 搜索个人信息模式

```bash
# 搜索用户名、真实域名、真实路径
grep -r "vanhci" .
grep -r "wanghanchao" .
grep -r "siri.vanhci" .
grep -r "your-domain.com" .  # 检查是否有需要替换的域名

# 或使用 find + grep 更精确
find . -type f -not -path "./.git/*" -exec grep -l "vanhci\|wanghanchao" {} \;
```

### 2. 替换为通用占位符

| 原始内容 | 占位符 |
|----------|--------|
| 真实用户名（如 `vanhci`） | `<your-username>` |
| 真实域名（如 `siri.vanhci.top`） | `siri.your-domain.com` |
| 真实 Tunnel 名称 | `<tunnel-name>` |
| 真实路径（如 `/Users/vanhci/`） | `/Users/<your-username>/` |

```bash
# 批量替换示例（谨慎使用，先备份）
sed -i '' 's/vanhci/<your-username>/g' README.md SKILL.md
sed -i '' 's/siri\.vanhci\.top/siri.your-domain.com/g' README.md SKILL.md
sed -i '' 's/siri-vanhci/<tunnel-name>/g' siri-daemon.sh
```

### 3. 需保留的本地地址（不需要替换）

- `127.0.0.1`、`localhost` — 标准本地地址
- `:18901`、`:8642` — 本地端口
- `/Users/<your-username>/` — 已使用占位符

### 4. 处理 Git 冲突

如果从分支合并或 rebase 时出现冲突：

```bash
# 查看冲突文件
git diff --name-only --diff-filter=U

# 手动解决冲突后
git add <resolved-files>
git rebase --continue  # 或 git commit
```

常见冲突场景：
- 原始版本有真实地址，清理版本有占位符
- 选择占位符版本（保留通用性）

### 5. LICENSE 文件

- `Copyright (c) 2026 vanhci` — 保留，这是标准版权声明，不是配置

### 6. 推送清理后的版本

```bash
git add .
git commit -m "清理个人信息，使用通用占位符"
git push origin main
```

### 7. 验证清理效果

```bash
# 确认无真实用户名残留
grep -r "vanhci" . --exclude-dir=.git

# 确认占位符已替换
grep -r "<your-username>" .
grep -r "siri.your-domain.com" .
```
