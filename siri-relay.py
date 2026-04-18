#!/usr/bin/env python3
"""
Siri Relay — 接受 {text}，调用 LLM API，返回纯文本供 Siri 朗读。

用法:
  python3 siri-relay.py [--port 18901] [--api-url http://127.0.0.1:8642/v1/chat/completions]

环境变量:
  RELAY_PORT       监听端口（默认 18901）
  LLM_API_URL      LLM API 地址（默认 http://127.0.0.1:8642/v1/chat/completions）
  LLM_MODEL        模型名称（默认 hermes-agent）
  MAX_TOKENS       最大 token 数（默认 500）
  API_TIMEOUT      API 请求超时秒数（默认 120）
"""

import argparse
import json
import os
import re
import sys
import time
import http.server
import urllib.request


def clean_for_tts(text: str) -> str:
    """清理 Markdown 格式，让 Siri 朗读更自然"""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)          # bold
    text = re.sub(r'\*(.*?)\*', r'\1', text)               # italic
    text = re.sub(r'`(.*?)`', r'\1', text)                  # inline code
    text = re.sub(r'```[\s\S]*?```', '[代码块]', text)      # code block
    text = re.sub(r'#{1,6}\s*', '', text)                   # headings
    text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)  # list items
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)    # links → text only
    text = re.sub(r'\n{3,}', '\n\n', text)                  # collapse blank lines
    # 清理 emoji（Siri 会念出 emoji 名称，体验很差）
    try:
        import emoji
        text = emoji.replace_emoji(text, replace='')
    except ImportError:
        pass
    return text.strip()


class RelayHandler(http.server.BaseHTTPRequestHandler):
    """HTTP 请求处理"""

    def do_POST(self):
        if self.path != "/siri":
            self.send_error(404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))
            user_text = body.get("text", "").strip()

            if not user_text:
                self._json_response(400, {"error": "text field is required"})
                return

            # 调用 LLM API
            reply = self._call_llm(user_text)
            reply = clean_for_tts(reply)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(reply.encode("utf-8"))

        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr, flush=True)
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode())

    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {
                "status": "ok",
                "api_url": self.server.api_url,
                "model": self.server.model,
            })
        else:
            self._json_response(200, {"status": "ok"})

    def _call_llm(self, text: str) -> str:
        payload = json.dumps({
            "model": self.server.model,
            "messages": [{"role": "user", "content": text}],
            "max_tokens": self.server.max_tokens,
        }).encode()

        req = urllib.request.Request(
            self.server.api_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        timeout = self.server.api_timeout
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())

        return data["choices"][0]["message"]["content"]

    def _json_response(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, fmt, *args):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] {args[0]}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Siri Relay Server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("RELAY_PORT", 18901)))
    parser.add_argument("--api-url", default=os.environ.get("LLM_API_URL", "http://127.0.0.1:8642/v1/chat/completions"))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL", "hermes-agent"))
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("MAX_TOKENS", 500)))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("API_TIMEOUT", 120)))
    args = parser.parse_args()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), RelayHandler)
    server.api_url = args.api_url
    server.model = args.model
    server.max_tokens = args.max_tokens
    server.api_timeout = args.timeout

    print(f"🎤 Siri Relay listening on :{args.port}")
    print(f"📡 API: {args.api_url} (model={args.model})")
    print(f"🔗 POST http://localhost:{args.port}/siri  body={{\"text\": \"...\"}}")
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
