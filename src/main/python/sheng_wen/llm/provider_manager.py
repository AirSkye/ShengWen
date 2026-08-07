from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any

from ..utils.logger import logger
from .llm import LLMConfig


@dataclass(frozen=True)
class LLMProvider:
    """预置的 LLM 供应商定义。"""

    id: str
    label: str
    default_base_url: str
    default_model_id: str
    description: str


def _normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def _mask_api_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


class LLMProviderManager:
    """运行时 LLM 配置管理器，支持多 Profile 持久配置。"""

    def __init__(self, initial_config: LLMConfig, initial_provider_id: str | None = None):
        self._lock = Lock()
        self._providers = self._build_providers()
        inferred_provider = self._infer_provider_id(initial_config.base_url)
        initial_provider_id = (
            initial_provider_id
            if initial_provider_id in self._providers
            else inferred_provider
        )
        # Profile runtime configs: profile_id → LLMConfig
        self._profiles: dict[str, LLMConfig] = {}
        # Profile metadata: profile_id → {name, provider}
        self._profile_meta: dict[str, dict[str, str]] = {}
        # Create initial profile from the provided config
        initial_profile_id = uuid.uuid4().hex[:8]
        provider_def = self._providers.get(initial_provider_id)
        self._profiles[initial_profile_id] = LLMConfig(
            base_url=initial_config.base_url,
            api_key=initial_config.api_key,
            model_id=initial_config.model_id,
            temperature=initial_config.temperature,
            context_window_size=initial_config.context_window_size,
            provider=initial_provider_id,
        )
        self._profile_meta[initial_profile_id] = {
            "name": provider_def.label if provider_def else initial_provider_id,
            "provider": initial_provider_id,
        }
        self._active_profile_id = initial_profile_id
        self._llm_worker: Any = None

    def _build_providers(self) -> dict[str, LLMProvider]:
        providers = [
            LLMProvider(
                id="openai_compatible",
                label="OpenAI 兼容接口",
                default_base_url="https://api.openai.com/v1",
                default_model_id="gpt-4o-mini",
                description="支持任意 OpenAI 兼容网关/供应商",
            ),
            LLMProvider(
                id="openai",
                label="OpenAI",
                default_base_url="https://api.openai.com/v1",
                default_model_id="gpt-4.1-mini",
                description="OpenAI 官方 API",
            ),
            LLMProvider(
                id="openrouter",
                label="OpenRouter",
                default_base_url="https://openrouter.ai/api/v1",
                default_model_id="openai/gpt-4.1-mini",
                description="通过 OpenRouter 路由多模型",
            ),
            LLMProvider(
                id="ollama",
                label="Ollama (本地)",
                default_base_url="http://localhost:11434/v1",
                default_model_id="qwen3:14b",
                description="本地 Ollama OpenAI 兼容接口",
            ),
            LLMProvider(
                id="deepseek",
                label="DeepSeek",
                default_base_url="https://api.deepseek.com",
                default_model_id="deepseek-v4-flash",
                description="DeepSeek OpenAI 兼容接口",
            ),
        ]
        return {provider.id: provider for provider in providers}

    def _infer_provider_id(self, base_url: str) -> str:
        normalized = _normalize_base_url(base_url)
        for provider in self._providers.values():
            if _normalize_base_url(provider.default_base_url) == normalized:
                return provider.id
        if "openrouter.ai" in normalized:
            return "openrouter"
        if "localhost:11434" in normalized:
            return "ollama"
        if "deepseek.com" in normalized:
            return "deepseek"
        if "api.openai.com" in normalized:
            return "openai"
        return "openai_compatible"

    def bind_llm_worker(self, llm_worker: Any) -> None:
        with self._lock:
            self._llm_worker = llm_worker
            self._apply_config_to_worker_locked()

    def _apply_config_to_worker_locked(self) -> None:
        if self._llm_worker is None:
            return
        llm_client = getattr(self._llm_worker, "_llm_client", None)
        if llm_client is None:
            logger.warning("[LLMProviderManager] 未找到 llm_worker._llm_client，跳过运行时配置应用。")
            return
        active_config = self._profiles.get(self._active_profile_id)
        if active_config is None:
            return
        if hasattr(llm_client, "update_runtime_config"):
            llm_client.update_runtime_config(active_config)
        else:
            llm_client.config = active_config

    def list_providers(self) -> list[dict[str, str]]:
        with self._lock:
            providers = [asdict(provider) for provider in self._providers.values()]
        return providers

    def list_profiles(self) -> list[dict[str, Any]]:
        """Return all profiles with masked API keys."""
        with self._lock:
            result = []
            for pid, cfg in self._profiles.items():
                meta = self._profile_meta.get(pid, {})
                result.append({
                    "id": pid,
                    "name": meta.get("name", ""),
                    "provider": meta.get("provider", cfg.provider),
                    "base_url": cfg.base_url,
                    "model_id": cfg.model_id,
                    "temperature": cfg.temperature,
                    "context_window_size": cfg.context_window_size,
                    "has_api_key": bool(cfg.api_key),
                    "api_key_hint": _mask_api_key(cfg.api_key),
                })
        return result

    def get_settings(self) -> dict[str, Any]:
        """Return full settings view with profiles list and active_profile_id."""
        return {
            "active_profile_id": self._active_profile_id,
            "profiles": self.list_profiles(),
        }

    def get_runtime_config(self) -> LLMConfig:
        """Return the active profile's config (backward compat for llm.py)."""
        with self._lock:
            cfg = self._profiles.get(self._active_profile_id)
            if cfg is None:
                # Fallback to first profile
                if self._profiles:
                    cfg = next(iter(self._profiles.values()))
                else:
                    cfg = LLMConfig()
            return LLMConfig(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                model_id=cfg.model_id,
                temperature=cfg.temperature,
                context_window_size=cfg.context_window_size,
                provider=cfg.provider,
            )

    def add_profile(
        self,
        name: str,
        provider: str,
        base_url: str | None = None,
        api_key: str | None = None,
        model_id: str | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Create a new profile and set it as active."""
        provider_def = self._providers.get(provider)
        if provider_def is None:
            raise ValueError(f"不支持的供应商: {provider}")

        profile_id = uuid.uuid4().hex[:8]
        new_config = LLMConfig(
            base_url=(base_url or "").strip() or provider_def.default_base_url,
            api_key=(api_key or "").strip(),
            model_id=(model_id or "").strip() or provider_def.default_model_id,
            temperature=temperature if temperature is not None else 0.7,
            context_window_size=1000000,
            provider=provider,
        )
        with self._lock:
            self._profiles[profile_id] = new_config
            self._profile_meta[profile_id] = {
                "name": name or provider_def.label,
                "provider": provider,
            }
            self._active_profile_id = profile_id
            self._apply_config_to_worker_locked()
            logger.info(f"[LLMProviderManager] 创建新 Profile: id={profile_id}, name={name}, provider={provider}")

        settings = self.get_settings()
        settings["new_profile_id"] = profile_id
        return settings

    def update_profile(
        self,
        profile_id: str,
        name: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model_id: str | None = None,
        temperature: float | None = None,
        context_window_size: int | None = None,
    ) -> dict[str, Any]:
        """Update a specific profile's config and set it as active."""
        with self._lock:
            current = self._profiles.get(profile_id)
            if current is None:
                raise ValueError(f"Profile '{profile_id}' not found")
            meta = self._profile_meta.get(profile_id, {})

            # Determine provider change
            new_provider = provider if provider is not None else current.provider
            if new_provider not in self._providers:
                raise ValueError(f"不支持的供应商: {new_provider}")
            provider_def = self._providers[new_provider]

            # Build updated config
            next_base_url = (base_url or "").strip() if base_url is not None else current.base_url
            if not next_base_url and new_provider != current.provider:
                next_base_url = provider_def.default_base_url

            next_model_id = (model_id or "").strip() if model_id is not None else current.model_id
            if not next_model_id and new_provider != current.provider:
                next_model_id = provider_def.default_model_id

            next_api_key = current.api_key if api_key is None or not api_key.strip() else api_key.strip()
            next_temperature = current.temperature if temperature is None else temperature
            next_context_window_size = current.context_window_size if context_window_size is None else context_window_size

            self._profiles[profile_id] = LLMConfig(
                base_url=next_base_url,
                api_key=next_api_key,
                model_id=next_model_id,
                temperature=next_temperature,
                context_window_size=next_context_window_size,
                provider=new_provider,
            )
            if name is not None:
                meta["name"] = name
            if provider is not None:
                meta["provider"] = provider
            self._profile_meta[profile_id] = meta

            self._active_profile_id = profile_id
            self._apply_config_to_worker_locked()
            logger.info(
                f"[LLMProviderManager] 已更新 Profile: id={profile_id}, name={meta.get('name')}, provider={new_provider}"
            )

        return self.get_settings()

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        """Delete a profile. Cannot delete the last one."""
        with self._lock:
            if len(self._profiles) <= 1:
                raise ValueError("Cannot delete the last profile")
            if profile_id not in self._profiles:
                raise ValueError(f"Profile '{profile_id}' not found")
            self._profiles.pop(profile_id)
            self._profile_meta.pop(profile_id, None)
            # Switch to first remaining profile if deleted the active one
            if self._active_profile_id == profile_id:
                self._active_profile_id = next(iter(self._profiles.keys()))
                self._apply_config_to_worker_locked()
            logger.info(f"[LLMProviderManager] 已删除 Profile: id={profile_id}")

        return self.get_settings()

    def switch_profile(self, profile_id: str) -> dict[str, Any]:
        """Switch active profile without modifying any config."""
        with self._lock:
            if profile_id not in self._profiles:
                raise ValueError(f"Profile '{profile_id}' not found")
            self._active_profile_id = profile_id
            self._apply_config_to_worker_locked()
            logger.info(f"[LLMProviderManager] 切换活跃 Profile: id={profile_id}")
        return self.get_settings()

    def load_profiles(self, profiles_data: list[dict[str, Any]], active_profile_id: str):
        """Load profiles from settings.json (called on init)."""
        with self._lock:
            self._profiles.clear()
            self._profile_meta.clear()
            for p in profiles_data:
                pid = p.get("id", "")
                if not pid:
                    continue
                provider = p.get("provider", "openai_compatible")
                self._profiles[pid] = LLMConfig(
                    base_url=str(p.get("base_url") or ""),
                    api_key=str(p.get("api_key") or ""),
                    model_id=str(p.get("model_id") or ""),
                    temperature=float(p.get("temperature", 0.7)),
                    context_window_size=int(p.get("context_window_size", 1000000)),
                    provider=provider,
                )
                self._profile_meta[pid] = {
                    "name": str(p.get("name") or ""),
                    "provider": provider,
                }
            if active_profile_id in self._profiles:
                self._active_profile_id = active_profile_id
            elif self._profiles:
                self._active_profile_id = next(iter(self._profiles.keys()))
            self._apply_config_to_worker_locked()