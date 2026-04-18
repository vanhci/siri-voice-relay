# 🎤 Siri Voice Relay

**用 Siri 呼叫你的 AI 助手** — iPhone 快捷指令 → Cloudflare Tunnel → Mac → LLM API → Siri 朗读

```
iPhone "嘿Siri, 问问赫妹" → 快捷指令 → HTTPS POST → siri.yourdomain.com/siri
  → Mac relay (:18901) → LLM API (:8642) → 清理文本 → Siri 朗读回复
```

## ✨ 特性

- 🌐 **永久固定 URL** — Cloudflare Named Tunnel，不会断连
- 🔄 **自动守护重启** — daemon 监控 relay + tunnel，挂了自动拉起
- 🎯 **纯文本返回** — 清理 Markdown/emoji，Siri 直接朗读
- 🚀 **零依赖部署** — 只需 Python 3 + cloudflared
- 📱 **开机自启** — LaunchAgent 支持

## 🏗️ 架构

```
┌─────────┐     HTTPS      ┌──────────────────┐     HTTP      ┌───────────┐
│  iPhone  │ ────────────── │ Cloudflare Edge  │ ───────────── │ Mac :18901 │
│  快捷指令 │  siri.domain   │  Named Tunnel    │               │  relay    │
└─────────┘                └──────────────────┘               └─────┬─────┘
                                                                     │
                                                                     ▼
                                                              ┌───────────┐
                                                              │ LLM API   │
                                                              │ :8642     │
                                                              └───────────┘
```

## 📦 文件说明

| 文件 | 说明 |
|------|------|
| `siri-relay.py` | Relay 服务，接收 `{text}`，调用 LLM API，返回纯文本 |
| `siri-daemon.sh` | 守护进程，管理 relay + cloudflared 生命周期 |
| `com.siri.voice-relay.plist.template` | macOS LaunchAgent 模板，开机自启 |
| `cloudflared-config.yml.template` | Cloudflare Tunnel 配置模板 |

## 🚀 快速开始

### 前置条件

- macOS（本项目为 Mac 设计，Linux 亦可）
- Python 3.8+
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
- 一个域名（推荐阿里云购买，nameserver 指向 Cloudflare）
- 一个 LLM API 服务（如 Hermes Agent、OpenClaw 等）

### 1. 域名准备

```bash
# 阿里云购买域名 → 域名管理 → 修改 DNS 服务器为 Cloudflare 提供的
# 在 Cloudflare Dashboard 添加域名，等待 DNS 生效
```

### 2. 创建 Cloudflare Named Tunnel

```bash
# 登录（浏览器交互）
cloudflared tunnel login

# 创建 tunnel
cloudflared tunnel create siri-relay
# 记下输出的 TUNNEL_ID，credentials 文件在 ~/.cloudflared/<TUNNEL_ID>.json

# 配置 ingress
cp cloudflared-config.yml.template ~/.cloudflared/config.yml
# 编辑 config.yml：替换 <TUNNEL_ID> 和 <YOUR_DOMAIN>

# 添加 DNS CNAME
cloudflared tunnel route dns siri-relay siri.<YOUR_DOMAIN>
```

### 3. 部署 Relay

```bash
# 复制 relay 脚本
cp siri-relay.py ~/.openclaw/

# 安装可选依赖（清理 emoji）
pip3 install emoji

# 测试启动
python3 ~/.openclaw/siri-relay.py --port 18901
```

### 4. 部署 Daemon

```bash
# 复制 daemon 脚本
cp siri-daemon.sh ~/.openclaw/

# 编辑脚本开头的配置（TUNNEL_NAME、RELAY_PORT、LLM_API_URL）

# 测试运行
bash ~/.openclaw/siri-daemon.sh
```

### 5. 开机自启（可选）

```bash
# 从模板生成 LaunchAgent
sed "s/YOUR_USERNAME/$(whoami)/g" com.siri.voice-relay.plist.template \
  > ~/Library/LaunchAgents/com.siri.voice-relay.plist

# 加载
launchctl load ~/Library/LaunchAgents/com.siri.voice-relay.plist

# 管理命令
launchctl list | grep siri                          # 查看状态
launchctl unload ~/Library/LaunchAgents/com.siri.voice-relay.plist  # 卸载
```

### 6. 验证

```bash
# 本地测试
curl -s -m 10 -X POST -H "Content-Type: application/json" \
  -d '{"text":"你好"}' http://localhost:18901/siri

# 公网测试
curl -s -m 20 -X POST -H "Content-Type: application/json" \
  -d '{"text":"你好"}' https://siri.<YOUR_DOMAIN>/siri

# 健康检查
curl -s http://localhost:18901/health
```

### 7. 配置 iPhone 快捷指令

1. 打开「快捷指令」App → 新建快捷指令
2. 添加动作：

| 步骤 | 动作 | 配置 |
|------|------|------|
| 1 | **听写文本** | 语言：中文 |
| 2 | **获取URL内容** | URL: `https://siri.<你的域名>/siri`<br>方法: POST<br>请求体: `{"text": "听写文本"}`<br>Content-Type: `application/json` |
| 3 | **朗读文本** | 直接朗读「获取URL内容」的输出 |

3. 给快捷指令命名为「问问赫妹」（或你喜欢的名字）
4. 对 iPhone 说「嘿 Siri，问问赫妹」即可

> ⚠️ 返回的是 `text/plain` 纯文本，不需要额外解析 JSON。

## 🔧 进阶配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RELAY_PORT` | `18901` | Relay 监听端口 |
| `LLM_API_URL` | `http://127.0.0.1:8642/v1/chat/completions` | LLM API 地址 |
| `LLM_MODEL` | `hermes-agent` | 模型名称 |
| `MAX_TOKENS` | `500` | 最大回复 token 数 |
| `API_TIMEOUT` | `120` | API 请求超时（秒） |
| `TUNNEL_NAME` | `<tunnel-name>` | Cloudflare tunnel 名称 |
| `CHECK_INTERVAL` | `30` | 健康检查间隔（秒） |

### Relay 命令行参数

```bash
python3 siri-relay.py \
  --port 18901 \
  --api-url http://127.0.0.1:8642/v1/chat/completions \
  --model hermes-agent \
  --max-tokens 500 \
  --timeout 120
```

### 对接不同的 LLM 后端

只要兼容 OpenAI Chat Completions API 格式即可：

```bash
# OpenAI
LLM_API_URL="https://api.openai.com/v1/chat/completions"

# 本地 Ollama
LLM_API_URL="http://localhost:11434/v1/chat/completions"

# OpenClaw / Hermes
LLM_API_URL="http://127.0.0.1:8642/v1/chat/completions"
```

## 🐛 排障

```bash
# 检查进程
ps aux | grep -E 'siri-relay|cloudflared' | grep -v grep

# 端口占用
lsof -i :18901

# DNS 解析
dig +short siri.<YOUR_DOMAIN>

# daemon 日志
tail -50 ~/.openclaw/siri-daemon.log
```

### 常见问题

| 症状 | 原因 | 解决 |
|------|------|------|
| Siri 说"出错请重试" | URL 错误或超时 | 检查快捷指令 URL，超时设 ≥60s |
| curl 通但 Siri 不通 | iPhone 网络不同 | 确认 URL 公网可达 |
| Tunnel 连不上 | cloudflared 挂了 | `pgrep -fl cloudflared`，重启 |
| 端口被占用 | 残留进程 | `lsof -i :18901` 找到并 kill |
| `ModuleNotFoundError: emoji` | Python 包丢失 | `pip3 install emoji` |

## 📄 License

MIT

## 🙏 致谢

- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/) — 免费的内网穿透
- OpenAI Chat Completions API — 通用 LLM 接口标准
