from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = PROJECT_ROOT / ".env"


def parse_dotenv_file(dotenv_path: Path = DOTENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not dotenv_path.exists():
        return values
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def update_dotenv_file(updates: dict[str, str], dotenv_path: Path = DOTENV_PATH) -> None:
    """Safely update specific KEY=VALUE entries in .env while preserving all comments and layout."""
    allowed = {
        "MAP_PROVIDER",
        "AMAP_API_KEY",
        "GOOGLE_MAPS_API_KEY",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "FREE_WEB_GATEWAY_URL",
        "KIMI_WEB_TOKEN",
    }
    clean_updates = {k: str(v).strip().replace("\r", "").replace("\n", "") for k, v in updates.items() if k in allowed}
    if not clean_updates:
        return

    lines = dotenv_path.read_text(encoding="utf-8").splitlines() if dotenv_path.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            k_clean = k.strip()
            if k_clean in clean_updates:
                new_lines.append(f"{k_clean}={clean_updates[k_clean]}")
                seen.add(k_clean)
                continue
        new_lines.append(raw_line)

    for k, v in clean_updates.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    dotenv_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _get_val(file_vals: dict[str, str], key: str, default: str = "") -> str:
    if key in file_vals and file_vals[key] != "":
        return file_vals[key]
    return os.getenv(key, default).strip()


@dataclass
class RuntimeConfig:
    map_provider: str = "auto"  # "auto" | "osm" | "amap" | "google" | "demo"
    google_maps_api_key: str = ""
    amap_api_key: str = ""

    # LLM routing:
    # "auto" | "official" | "free_public" | "qwen_web" | "deepseek_web" | "kimi_web" | "doubao_web"
    llm_provider: str = "auto"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # Free Web-Edition / Chat2API / Reverse-Proxy ports & optional browser tokens
    free_web_gateway_url: str = ""  # e.g. Chat2API http://127.0.0.1:8000/v1 or http://127.0.0.1:3000/v1
    kimi_web_url: str = "http://127.0.0.1:8010/v1"
    kimi_web_token: str = ""  # kimi-auth cookie JWT or refresh_token
    deepseek_web_url: str = "http://127.0.0.1:8011/v1"
    deepseek_web_token: str = ""  # chat.deepseek.com userToken
    qwen_web_url: str = "http://127.0.0.1:8012/v1"
    qwen_web_token: str = ""  # tongyi_sso_ticket
    doubao_web_url: str = "http://127.0.0.1:8014/v1"
    doubao_web_token: str = ""  # sessionid

    # System / environment fallback keys (auto-discovered if present)
    qwen_env_key: str = ""
    zhipu_env_key: str = ""

    @classmethod
    def load_live(cls) -> RuntimeConfig:
        """Reloads .env on every call so editing .env in IDE takes effect immediately without restart."""
        fv = parse_dotenv_file(DOTENV_PATH)
        return cls(
            map_provider=_get_val(fv, "MAP_PROVIDER", "auto").lower(),
            google_maps_api_key=_get_val(fv, "GOOGLE_MAPS_API_KEY", ""),
            amap_api_key=_get_val(fv, "AMAP_API_KEY", ""),
            llm_provider=_get_val(fv, "LLM_PROVIDER", "auto").lower(),
            llm_api_key=_get_val(fv, "LLM_API_KEY", ""),
            llm_base_url=_get_val(fv, "LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            llm_model=_get_val(fv, "LLM_MODEL", "gpt-4o-mini"),
            free_web_gateway_url=_get_val(fv, "FREE_WEB_GATEWAY_URL", "").rstrip("/"),
            kimi_web_url=_get_val(fv, "KIMI_WEB_URL", "http://127.0.0.1:8010/v1").rstrip("/"),
            kimi_web_token=_get_val(fv, "KIMI_WEB_TOKEN", ""),
            deepseek_web_url=_get_val(fv, "DEEPSEEK_WEB_URL", "http://127.0.0.1:8011/v1").rstrip("/"),
            deepseek_web_token=_get_val(fv, "DEEPSEEK_WEB_TOKEN", ""),
            qwen_web_url=_get_val(fv, "QWEN_WEB_URL", "http://127.0.0.1:8012/v1").rstrip("/"),
            qwen_web_token=_get_val(fv, "QWEN_WEB_TOKEN", ""),
            doubao_web_url=_get_val(fv, "DOUBAO_WEB_URL", "http://127.0.0.1:8014/v1").rstrip("/"),
            doubao_web_token=_get_val(fv, "DOUBAO_WEB_TOKEN", ""),
            qwen_env_key=_get_val(fv, "QWEN_API_KEY", ""),
            zhipu_env_key=_get_val(fv, "ZHIPUAI_API_KEY", ""),
        )

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        return cls.load_live()

    def with_overrides(self, overrides: dict[str, str] | None) -> RuntimeConfig:
        if not overrides:
            return self
        return RuntimeConfig(
            map_provider=(overrides.get("map_provider") or self.map_provider).strip().lower(),
            google_maps_api_key=(overrides.get("google_maps_api_key") or self.google_maps_api_key).strip(),
            amap_api_key=(overrides.get("amap_api_key") or self.amap_api_key).strip(),
            llm_provider=(overrides.get("llm_provider") or self.llm_provider).strip().lower(),
            llm_api_key=(overrides.get("llm_api_key") or self.llm_api_key).strip(),
            llm_base_url=(overrides.get("llm_base_url") or self.llm_base_url).strip().rstrip("/"),
            llm_model=(overrides.get("llm_model") or self.llm_model).strip(),
            free_web_gateway_url=(overrides.get("free_web_gateway_url") or self.free_web_gateway_url).strip().rstrip("/"),
            kimi_web_url=(overrides.get("kimi_web_url") or self.kimi_web_url).strip().rstrip("/"),
            kimi_web_token=(overrides.get("kimi_web_token") or self.kimi_web_token).strip(),
            deepseek_web_url=(overrides.get("deepseek_web_url") or self.deepseek_web_url).strip().rstrip("/"),
            deepseek_web_token=(overrides.get("deepseek_web_token") or self.deepseek_web_token).strip(),
            qwen_web_url=(overrides.get("qwen_web_url") or self.qwen_web_url).strip().rstrip("/"),
            qwen_web_token=(overrides.get("qwen_web_token") or self.qwen_web_token).strip(),
            doubao_web_url=(overrides.get("doubao_web_url") or self.doubao_web_url).strip().rstrip("/"),
            doubao_web_token=(overrides.get("doubao_web_token") or self.doubao_web_token).strip(),
            qwen_env_key=self.qwen_env_key,
            zhipu_env_key=self.zhipu_env_key,
        )
