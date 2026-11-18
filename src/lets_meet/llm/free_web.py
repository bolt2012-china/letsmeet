from __future__ import annotations

import json
import struct
import httpx
from lets_meet.config import RuntimeConfig

_LAST_FAST_CHANNEL_LABEL: str = ""
_BAD_KEY_FINGERPRINTS: set[str] = set()


async def _call_openai_compat(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float = 14.0,
) -> str | None:
    """Call any OpenAI-compatible /chat/completions endpoint (official API, Chat2API, *-free-api, or Pollinations)."""
    fp = f"{base_url}|{api_key[:10]}|{api_key[-4:]}" if api_key else ""
    if fp and fp in _BAD_KEY_FINGERPRINTS:
        return None

    clean_base = base_url.rstrip("/")
    if clean_base.endswith("/openai"):
        url = clean_base
    elif clean_base.endswith("/chat/completions"):
        url = clean_base
    else:
        url = f"{clean_base}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    is_local = "127.0.0.1" in url or "localhost" in url
    eff_timeout = min(timeout, 1.8) if is_local and api_key == "free" else timeout

    try:
        async with httpx.AsyncClient(timeout=eff_timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (401, 402):
                if fp:
                    _BAD_KEY_FINGERPRINTS.add(fp)
                return None
            if resp.status_code != 200:
                return None
            body = resp.json()
            choices = body.get("choices") or []
            if not choices:
                return None
            msg = choices[0].get("message") or {}
            content = msg.get("content") or ""
            return str(content).strip() if content else None
    except Exception:
        return None


async def _call_kimi_web_connect_rpc(
    kimi_jwt: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float = 12.0,
) -> str | None:
    """
    Direct Kimi Web (www.kimi.com) Connect-RPC adapter using browser `kimi-auth` cookie JWT
    (no external Node/Docker container required when `kimi_web_token` starts with `eyJ`).
    """
    if not kimi_jwt or not kimi_jwt.startswith("eyJ"):
        return None

    payload_bytes = json.dumps(
        {
            "scenario": "SCENARIO_K2",
            "message": {
                "role": "user",
                "blocks": [{"message_id": "", "text": {"content": f"{system_prompt}\n\n{user_prompt}"}}],
                "scenario": "SCENARIO_K2",
            },
            "options": {"thinking": False},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    envelope = struct.pack(">BI", 0, len(payload_bytes)) + payload_bytes
    headers = {
        "Authorization": f"Bearer {kimi_jwt}",
        "Content-Type": "application/connect+json",
        "Connect-Protocol-Version": "1",
        "Origin": "https://www.kimi.com",
        "Referer": "https://www.kimi.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/134.0.0.0 Safari/537.36",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://www.kimi.com/apiv2/kimi.gateway.chat.v1.ChatService/Chat",
                headers=headers,
                content=envelope,
            )
            if resp.status_code != 200:
                return None
            raw = resp.content
            idx = 0
            chunks: list[str] = []
            while idx + 5 <= len(raw):
                length = struct.unpack(">I", raw[idx + 1 : idx + 5])[0]
                frame = raw[idx + 5 : idx + 5 + length]
                idx += 5 + length
                try:
                    obj = json.loads(frame.decode("utf-8", errors="ignore"))
                    op = obj.get("op")
                    block = obj.get("block") or {}
                    text_part = (block.get("text") or {}).get("content")
                    if text_part and op in ("append", "set", None):
                        chunks.append(text_part)
                except Exception:
                    continue
            joined = "".join(chunks).strip()
            return joined or None
    except Exception:
        return None


class FreeWebLLMCascade:
    """
    Unified LLM caller that supports:
      1. Explicit commercial API key (if configured & has balance)
      2. Web-Edition Free Ports for 豆包 (Doubao), DeepSeek 网页版, Kimi 网页版, Qwen 网页版:
         - Direct Kimi Web Connect-RPC (when KIMI_WEB_TOKEN `kimi-auth` cookie is pasted)
         - Chat2API / free-llm-hub / *-free-api OpenAI-compatible ports (8010/8011/8012/8014 or custom FREE_WEB_GATEWAY_URL)
      3. Free/Available System Environment Models (Aliyun `qwen-turbo`, Zhipu Official Free `glm-4-flash`)
      4. Zero-Key, Zero-Login Public Free LLM Endpoint (`https://text.pollinations.ai/openai`, `openai-fast`)
    """

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def has_dedicated_channel(self) -> bool:
        """True if any key, web token, web gateway, or system model is configured (not purely rate-limited public Pollinations)."""
        c = self.config
        return bool(
            c.llm_api_key
            or c.free_web_gateway_url
            or c.kimi_web_token
            or c.deepseek_web_token
            or c.qwen_web_token
            or c.doubao_web_token
            or c.qwen_env_key
            or c.zhipu_env_key
            or c.llm_provider in ("kimi_web", "deepseek_web", "qwen_web", "doubao_web")
        )

    def _build_candidate_chain(self) -> list[tuple[str, str, str, str]]:
        cfg = self.config
        mode = cfg.llm_provider
        chain: list[tuple[str, str, str, str]] = []

        def add_web_channel(which: str, force_probe: bool = False) -> None:
            gw = cfg.free_web_gateway_url
            if which == "kimi_web":
                if cfg.kimi_web_token.startswith("eyJ"):
                    chain.append(("Kimi 网页版直连 (Connect-RPC)", "kimi://connect-rpc", cfg.kimi_web_token, "kimi-k2"))
                url = gw or cfg.kimi_web_url
                if url and (cfg.kimi_web_token or gw or force_probe):
                    chain.append(("Kimi 网页版免费端口", url, cfg.kimi_web_token or "free", "kimi"))
            elif which == "deepseek_web":
                url = gw or cfg.deepseek_web_url
                if url and (cfg.deepseek_web_token or gw or force_probe):
                    chain.append(("DeepSeek 网页版免费端口", url, cfg.deepseek_web_token or "free", "deepseek-chat"))
            elif which == "qwen_web":
                url = gw or cfg.qwen_web_url
                if url and (cfg.qwen_web_token or gw or force_probe):
                    chain.append(("Qwen 通义网页版免费端口", url, cfg.qwen_web_token or "free", "qwen-plus"))
            elif which == "doubao_web":
                url = gw or cfg.doubao_web_url
                if url and (cfg.doubao_web_token or gw or force_probe):
                    chain.append(("豆包网页版免费端口", url, cfg.doubao_web_token or "free", "doubao"))

        # 1. Explicit web channel selected by user
        if mode in ("kimi_web", "deepseek_web", "qwen_web", "doubao_web"):
            add_web_channel(mode, force_probe=True)

        # 2. Explicit custom LLM_API_KEY
        if mode in ("auto", "official") and cfg.llm_api_key:
            chain.append((f"自定义大模型 ({cfg.llm_model})", cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model))

        # 3. Configured web tokens or unified Chat2API gateway in auto mode
        if mode == "auto":
            for w in ("deepseek_web", "qwen_web", "kimi_web", "doubao_web"):
                add_web_channel(w, force_probe=False)

        # 4. Fast system-available Qwen (`qwen-plus`) / Zhipu Free (`glm-4-flash`)
        if mode in ("auto", "qwen_web"):
            if cfg.qwen_env_key:
                chain.append(
                    ("通义千问 Qwen (qwen-plus)", "https://dashscope.aliyuncs.com/compatible-mode/v1", cfg.qwen_env_key, "qwen-plus")
                )
            if cfg.zhipu_env_key:
                chain.append(
                    ("智谱官方免费模型 (GLM-4-Flash)", "https://open.bigmodel.cn/api/paas/v4", cfg.zhipu_env_key, "glm-4-flash")
                )

        # 5. Zero-key, zero-login public free OpenAI-compatible gateway
        if mode != "official":
            chain.append(("免Key公共免费大模型 (Pollinations)", "https://text.pollinations.ai/openai", "", "openai-fast"))

        # Promote previously verified fast channel ONLY when no explicit user LLM_API_KEY is configured
        if mode == "auto" and _LAST_FAST_CHANNEL_LABEL and not cfg.llm_api_key:
            chain.sort(key=lambda item: 0 if item[0] == _LAST_FAST_CHANNEL_LABEL else 1)

        return chain

    async def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout: float = 10.0,
    ) -> tuple[str | None, str]:
        global _LAST_FAST_CHANNEL_LABEL
        for label, base_url, api_key, model in self._build_candidate_chain():
            if base_url == "kimi://connect-rpc":
                out = await _call_kimi_web_connect_rpc(api_key, system_prompt, user_prompt, timeout=timeout)
            else:
                out = await _call_openai_compat(base_url, api_key, model, system_prompt, user_prompt, timeout=timeout)
            if out:
                _LAST_FAST_CHANNEL_LABEL = label
                return out, label
        return None, ""
