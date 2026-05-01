"""Pre-baked provider templates so users don't have to memorize base_urls.

Categories:
  foreign-native, domestic-native, aggregator, subscription, gemini, custom
"""
from __future__ import annotations

TEMPLATES: list[dict] = [
    # ---- Foreign native ----
    {
        "key": "anthropic-api",
        "label": "Anthropic API",
        "category": "foreign-native",
        "kind": "anthropic_api",
        "fields": ["api_key"],
        "default_models": [
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ],
        "hint": "Anthropic 官方 API。Opus 4.7(2026-04-16 发布)、Sonnet 4.6、Haiku 4.5。",
    },
    {
        "key": "openai-api",
        "label": "OpenAI API",
        "category": "foreign-native",
        "kind": "openai_api",
        "fields": ["api_key"],
        "default_models": ["gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini"],
        "hint": "OpenAI 官方 API。GPT-5.5(2026-04-23)、5.5 Pro 高精度、5.4/5.4-mini 低成本。",
    },
    # ---- Gemini ----
    {
        "key": "gemini-api",
        "label": "Google Gemini",
        "category": "gemini",
        "kind": "gemini_api",
        "fields": ["api_key"],
        "default_models": [
            "gemini-3.1-pro-preview",
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        ],
        "hint": "Gemini API(AI Studio)。3.1 Pro Preview 最强、3 Flash Preview / 3.1 Flash-Lite Preview 有免费额度、2.5 系列稳定老款。Key 从 aistudio.google.com 取。",
    },
    # ---- Subscription CLI ----
    {
        "key": "claude-cli",
        "label": "Claude CLI(订阅)",
        "category": "subscription",
        "kind": "claude_cli",
        "fields": [],
        "default_models": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "hint": "用本机已登录的 Claude Code CLI。先在终端运行 `claude` 完成订阅登录。",
    },
    {
        "key": "codex-cli",
        "label": "OpenAI Codex CLI(订阅)",
        "category": "subscription",
        "kind": "codex_cli",
        "fields": [],
        "default_models": ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5.1-codex-mini"],
        "hint": "用本机已登录的 codex CLI(2026-04 起 ChatGPT 订阅可用 GPT-5.5)。先在终端 `codex login` 登录。",
    },
    # ---- Domestic native (OpenAI-compatible) ----
    {
        "key": "deepseek",
        "label": "DeepSeek",
        "category": "domestic-native",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://api.deepseek.com/v1"},
        "default_models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
        "hint": "DeepSeek V4(2026-04-24 发布,1M 上下文)。chat / reasoner 是 V4-Flash 的兼容别名,2026-07-24 后下线。",
    },
    {
        "key": "zhipu",
        "label": "智谱 GLM",
        "category": "domestic-native",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://open.bigmodel.cn/api/paas/v4"},
        "default_models": ["glm-5.1", "glm-5-turbo", "glm-4.7", "glm-4.5-air"],
        "hint": "智谱 BigModel。GLM-5.1(2026-04-07)长程任务,5-Turbo 快,4.7/4.5-Air 省成本。",
    },
    {
        "key": "qwen",
        "label": "通义千问 / DashScope",
        "category": "domestic-native",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        "default_models": [
            "qwen3.6-max-preview",
            "qwen3.6-plus",
            "qwen3.6-flash",
            "qwen3-max",
            "qwen-max-latest",
        ],
        "hint": "阿里云 DashScope。qwen3.6 系列(2026-04 最新,Max 还是 Preview),qwen-max-latest 永远跟最新版。",
    },
    {
        "key": "minimax",
        "label": "MiniMax",
        "category": "domestic-native",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://api.minimax.chat/v1"},
        "default_models": ["MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M1", "MiniMax-Text-01"],
        "hint": "MiniMax M2.7(2026 最新多模态)、M2.5(229B MoE)、M1(100W 上下文推理)。",
    },
    {
        "key": "moonshot",
        "label": "Moonshot Kimi",
        "category": "domestic-native",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://api.moonshot.cn/v1"},
        "default_models": ["kimi-k2.6", "kimi-k2.5", "moonshot-v1-128k", "moonshot-v1-32k"],
        "hint": "月之暗面 Kimi K2.6(2026-04-20,256K 上下文,SWE-Bench Pro 58.6 超越 GPT-5.4)。",
    },
    {
        "key": "ark",
        "label": "火山方舟",
        "category": "domestic-native",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://ark.cn-beijing.volces.com/api/v3"},
        "default_models": ["doubao-seed-1-8-251228", "doubao-seed-1-6", "doubao-seed-1-6-flash"],
        "hint": "字节火山方舟 / 豆包 1.8(最新)、1.6、1.6-flash。也可填你自建的 endpoint id(ep-xxxxx)。",
    },
    {
        "key": "yi",
        "label": "零一万物 01.AI",
        "category": "domestic-native",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://api.lingyiwanwu.com/v1"},
        "default_models": ["yi-lightning", "yi-lightning-lite", "yi-large", "yi-large-turbo"],
        "hint": "零一万物。Yi-Lightning 旗舰、Lightning-Lite 低成本、Yi-Large 经典款。",
    },
    # ---- Aggregators ----
    {
        "key": "siliconflow",
        "label": "硅基流动 SiliconFlow",
        "category": "aggregator",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://api.siliconflow.cn/v1"},
        "default_models": [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen3-235B-A22B",
            "meta-llama/Llama-3.3-70B-Instruct",
            "zai-org/GLM-5",
        ],
        "hint": "硅基流动聚合站。DeepSeek-V3/R1、Qwen3-235B、Llama 3.3、GLM-5。",
    },
    {
        "key": "openrouter",
        "label": "OpenRouter",
        "category": "aggregator",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://openrouter.ai/api/v1"},
        "default_models": [
            "anthropic/claude-opus-4.7",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.5",
            "google/gemini-3.1-pro",
            "deepseek/deepseek-v4-pro",
            "moonshotai/kimi-k2.6",
        ],
        "hint": "OpenRouter 海外聚合,统一接 Anthropic/OpenAI/Google/DeepSeek/Kimi 等。",
    },
    {
        "key": "together",
        "label": "Together AI",
        "category": "aggregator",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://api.together.xyz/v1"},
        "default_models": [
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ],
        "hint": "Together AI 海外开源模型聚合,Llama/DeepSeek/Qwen 都有 Turbo 版。",
    },
    {
        "key": "groq",
        "label": "Groq",
        "category": "aggregator",
        "kind": "openai_compat",
        "fields": ["api_key"],
        "config": {"base_url": "https://api.groq.com/openai/v1"},
        "default_models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "deepseek-r1-distill-llama-70b",
        ],
        "hint": "Groq 极速推理。llama-3.3-70b-versatile 通用,8b-instant 最快,distill-70b 推理任务。",
    },
    {
        "key": "oneapi",
        "label": "OneAPI / NewAPI 自建",
        "category": "aggregator",
        "kind": "openai_compat",
        "fields": ["api_key", "base_url"],
        "config": {"base_url": "http://127.0.0.1:3000/v1"},
        "default_models": ["gpt-5.5", "claude-opus-4-7", "deepseek-v4-pro"],
        "hint": "自建聚合网关。模型 id 取决于你网关里 enable 了哪些渠道。",
    },
    # ---- Custom ----
    {
        "key": "custom",
        "label": "自定义 OpenAI 兼容",
        "category": "custom",
        "kind": "openai_compat",
        "fields": ["api_key", "base_url"],
        "config": {"base_url": ""},
        "default_models": [],
        "hint": "任何 OpenAI 兼容的 base_url + 模型 id。",
    },
]


def get_template(key: str) -> dict | None:
    return next((t for t in TEMPLATES if t["key"] == key), None)


CATEGORY_LABELS = {
    "foreign-native": "国外原生 API",
    "domestic-native": "国内原生 API",
    "aggregator": "聚合 / 网关",
    "subscription": "订阅 CLI",
    "gemini": "Google Gemini",
    "custom": "自定义",
}
