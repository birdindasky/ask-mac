<div align="center">

# Ask

**一个 macOS 上的本地多模型聊天 / 对比 / 辩论 / 求共识桌面 App**

*One Mac app. Every LLM. Chat with one, compare two, watch them debate, or make them reach consensus.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Platform](https://img.shields.io/badge/macOS-13%2B-blue.svg)](https://www.apple.com/macos)
[![Version](https://img.shields.io/badge/version-0.2.0-green.svg)](./CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Made for vibecoders](https://img.shields.io/badge/made%20for-vibecoders-purple.svg)](#)

[English](./README.md) | **中文**

</div>

---

> 不再为"问哪个模型好"纠结。**一个窗口里同时召唤所有家** — Claude、GPT、Gemini、DeepSeek、GLM、通义、Moonshot、自建网关…让它们独立回答、并排对比、互相辩论,甚至**逼它们达成共识**。

---

## 📸 看一眼

<table>
<tr>
<td width="50%" align="center">
<img src="assets/screenshots/01-chat.png" alt="单聊"/>
<br/><b>单聊</b> · ⌘1 · 选 1 个模型,正常多轮对话
</td>
<td width="50%" align="center">
<img src="assets/screenshots/02-compare.png" alt="对比"/>
<br/><b>对比</b> · ⌘2 · 同一问题,两家并排流式
</td>
</tr>
<tr>
<td width="50%" align="center">
<img src="assets/screenshots/03-debate.png" alt="辩论"/>
<br/><b>辩论</b> · ⌘3 · 主辩 + 反方,1–4 轮可暂停
</td>
<td width="50%" align="center">
<img src="assets/screenshots/04-discuss.png" alt="求共识"/>
<br/><b>求共识</b> ✨ · ⌘4 · 协议互相修正,自动判收敛
</td>
</tr>
<tr>
<td colspan="2" align="center">
<img src="assets/screenshots/05-settings.png" alt="设置" width="80%"/>
<br/><b>一处接所有模型</b> · 国内国外 LLM + 6 家联网搜索后端
</td>
</tr>
</table>

---

## ✨ 为什么选 Ask

- 🪟 **真的是 .app**。原生菜单栏、Dock 徽章、托盘、⌘N/W/Q 快捷键、关窗不退出 — macOS 体验完整。
- 💬 **四种对话模式**,一键切:
  - **单聊** — 像 ChatGPT 一样和单个模型聊
  - **对比** — 同一问题,两家模型并排流式输出
  - **辩论** — 主辩 vs 反方,1–4 轮,中途可暂停可采纳
  - **求共识** ✨v0.2 新增 — 双方按协议互相质询,**把握度都 ≥ 8 且判断一致就提前收敛**
- 🌐 **谁都能接**。Anthropic API + OpenAI API + Gemini + Claude CLI(订阅)+ Codex CLI(订阅)+ 任何 OpenAI 兼容地址
- 🔍 **联网搜索内置**,6 家任选(Tavily / Exa / Brave / Serper / Jina / 博查),与所有 LLM 解耦
- 🔐 **API key 进 macOS Keychain**,JSON 配置只存模板和元数据
- 📦 **本地优先**。SQLite + FTS5 全文搜索(中文 trigram),所有会话留在你机器上
- 🌍 **中英双语**,319 条文案 100% 覆盖
- 🚀 **零构建前端**。DaisyUI v5 + Tailwind v4 + Alpine.js,全部 CDN,改完刷新就行

---

## 🚀 30 秒上手

```bash
git clone https://github.com/birdindasky/ask-mac.git
cd ask-mac
make build && make dmg
open dist/Ask-0.2.0.dmg
```

把 Ask 拖进 Applications,Spotlight 搜 `Ask` 启动 — 设置里填 1 个模型 key 就能用。

> 不想构建?直接去 [Releases](https://github.com/birdindasky/ask-mac/releases) 下载现成的 `.dmg`。

数据落在 `~/Library/Application Support/Ask/`,日志在 `~/Library/Logs/Ask/ask.log`,
API key 存在系统钥匙串(service `com.birdindasky.ask`)。

---

## 🛡️ 首次启动:绕过 Gatekeeper(必看)

Ask 还没买 Apple Developer 证书做代码签名 + 公证(那是 $99/年的 club fee 😅),所以**首次双击打开会被 macOS 拦下**,弹窗写:

> "无法打开 Ask,因为 Apple 无法检查其是否包含恶意软件。"

别慌,这**不**代表 Ask 有毒,只是没付苹果税而已。三种方法绕过(任选其一,**只需做一次**):

### 方法 A — 右键打开(最快)

1. 在 Finder 里找到 `Ask.app`(/Applications 或你拖进去的位置)
2. **按住 Control 点击**(或右键)Ask.app → 在菜单选 **"打开"**
3. 弹窗里再点一次 **"打开"**

往后双击启动就正常了。

### 方法 B — 系统设置里放行

如果你已经双击过被拦了:

1. 打开 **系统设置 → 隐私与安全性**
2. 滚到底,会看到一条 **"已阻止使用 Ask,因为来自身份不明的开发者"**
3. 点旁边的 **"仍要打开"**,输入 Mac 密码
4. 这之后双击就能正常启动

### 方法 C — 命令行(给程序员)

```bash
xattr -dr com.apple.quarantine /Applications/Ask.app
```

清掉隔离属性,从此双击直接通,不再被拦。

> ⚠️ 这套流程对**所有非签名 / 非 Mac App Store 的 Mac 软件**都适用,不是 Ask 独有的限制。

---

## 🧪 开发 / 调试模式

```bash
git clone https://github.com/birdindasky/ask-mac.git
cd ask-mac
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run.py
# 浏览器打开 http://127.0.0.1:8870
```

开发模式下数据落在 `~/.ask-dev/`,key 走 JSON 兜底而不是钥匙串(方便 reset)。

---

## 🎭 四种模式

| 模式 | 快捷键 | 说明 |
|---|---|---|
| **单聊** | ⌘1 | 选 1 个模型,正常多轮对话 |
| **对比** | ⌘2 | 选 2 个模型,同一问题两边并行流式输出 |
| **辩论** | ⌘3 | 主辩 + 反方(或对称),1–4 轮,可暂停可采纳 |
| **求共识** ✨ | ⌘4 | 协议式 6 字段,双方互相修正,**自动判共识** |

---

## 🌐 联网搜索(可选)

进 ⚙️ 设置 → "联网搜索",挑一家填 key 即可。

| 后端 | 注册 | 特点 |
|---|---|---|
| **Tavily** | [tavily.com](https://tavily.com) | RAG 专用,带摘要,免费 1000/月 |
| **Exa** | [exa.ai](https://exa.ai) | 神经搜索,擅长找深度内容 |
| **Brave Search** | [api.search.brave.com](https://api.search.brave.com) | 独立索引,免费 2000/月 |
| **Serper.dev** | [serper.dev](https://serper.dev) | Google 结果,极快,免费 2500 |
| **Jina Search** | [jina.ai/reader](https://jina.ai/reader) | 返回 markdown,LLM 友好 |
| **博查 Bocha** | [bochaai.com](https://bochaai.com) | 国内 AI 搜索,中文优化 |

只配一家就够用。任意一家都和所有 LLM provider 兼容,流程是:**问题 → 搜索后端 → 拿前 N 条 → 拼 system 上下文 → 喂 LLM**。模型自己不需要懂 search tool 协议。

---

## 🔌 订阅 CLI 模式(可选)

如果你已经在终端登录了 Claude Code 或 OpenAI Codex 订阅,可以直接用 CLI 跑:

- 设置页选 **Claude CLI(订阅)** 或 **OpenAI Codex CLI(订阅)** 模板,**不需要填 key**
- 程序会以子进程方式调用 `claude` / `codex`,**已自动 scrub** `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `*_BASE_URL` 等所有可能让 CLI 静默走 API 计费的环境变量

> 前提:本机已能直接在终端运行 `claude` 或 `codex` 并完成订阅登录。

---

## 🔧 自定义网关 / 自建 OneAPI

设置页选"OneAPI / NewAPI 自建"或"自定义 OpenAI 兼容"模板,填:

- `base_url`:例如 `http://127.0.0.1:3000/v1`
- `api_key`:你网关签发的 key
- 模型列表:每行一个,网关里 enable 的 model id

---

## ⚙️ 端口 / 数据目录定制

环境变量(在源码模式下生效;.app 模式直接走默认):

| 变量 | 默认 | 说明 |
|---|---|---|
| `MLC_HOST` | `127.0.0.1` | uvicorn 监听地址 |
| `MLC_PORT` | `8870` | 端口 |
| `MLC_DATA_DIR` | dev `~/.ask-dev/` <br/> .app `~/Library/Application Support/Ask/` | 配置 + 数据库 |
| `MLC_LOG_DIR` | dev `~/.ask-dev/logs/` <br/> .app `~/Library/Logs/Ask/` | 日志目录 |
| `MLC_CLI_TIMEOUT` | `120` | CLI 单次请求超时(秒) |
| `MLC_PACKAGED` | `0` | `1` 强制走 .app 路径(测试用) |
| `MLC_FORCE_KEYCHAIN` | `0` | `1` 在 dev 模式也走钥匙串 |
| `MLC_RELOAD` | `0` | `1` 启用 uvicorn 热重启 |

---

## ✅ 跑测试

```bash
source venv/bin/activate
python -m pytest -q
```

涵盖:adapter 注册、env scrub、config 迁移、session/message CRUD、四种模式的 SSE 全流程。**42 个用例**。

---

## 📁 目录结构

```
mac_launcher.py       .app 入口:uvicorn-in-thread + PyWebView + AppKit chrome
run.py                开发模式入口
setup.py              py2app 配置
Makefile              dev / test / icon / build / dmg / install
scripts/
├── build_icon.py     生成 assets/Ask.icns
└── build_dmg.py      把 dist/Ask.app 打成 .dmg
assets/
├── Ask.icns          应用图标
└── Ask.iconset/      图标多分辨率素材(自动生成)
app/
├── main.py           FastAPI 装配 / 静态文件路径解析
├── settings.py       路径与常量(dev vs .app 切换)
├── db.py             SQLite + FTS5 messages_fts
├── config_store.py   config.json 持久化(API key 不进 JSON)
├── api/              REST + SSE 路由
├── modes/            chat / compare / debate / discuss
├── providers/        各家 adapter + cli_detect 寻 PATH
├── security/         Keychain 包装 + secrets helpers
├── search/           联网搜索 6 家 + 引用注入
└── utils/            token_budget / attachments / autostart / notifier / dock_badge
static/               前端(单页 HTML + Alpine + DaisyUI + Tailwind)
tests/                pytest 42 个用例
```

---

## 🎁 v0.2 新增能力

- **求共识模式** — 第四种 tab,两个模型按"协议式"6 字段格式(`【当前判断】/【把握度】/【支撑】/【被对方修正】/【仍坚持】/【需要对方回应】`)交替发言,后端实时扫【把握度】数字,**双方都 ≥8 + 判断一致 → 提前收敛**;否则跑满 N 轮(默认 3,可改 1–5)。结束后由 A 方追加一条带 `📌 共识` 徽章 + "📋 复制共识"按钮的总结。
- **上下文进度条** — 每轮回答后估算 token 占用,接近 90% 弹"压缩历史"按钮。
- **重新生成** — 在最后一条 assistant 气泡上点 ↻ 重新生成,或在下拉里换模型一键重答。
- **⌘F 全局搜索** — 跨所有会话的 SQLite FTS5 搜索,trigram tokenizer 中文也能命中。
- **附件** — 聊天框支持挂图片或文本文件,4 个模式都能用。
- **欢迎向导** — 首次启动 4 屏引导,帮你 30 秒配好第一个 key。
- **i18n 100% 覆盖** — 中英双语,319 条文案,设置里一键切换。
- **桌面 chrome 完整** — NSMenu / NSStatusItem 托盘 / Dock 徽章 / 系统通知 / 开机启动 / About 面板。
- **关窗不退出** — 关闭主窗口只是隐藏,⌘Tab 可重新唤起;Dock Quit / `killall` / Activity Monitor 都能干净退。

---

## 🍴 Forking 注意事项

如果你 fork 自己用,**强烈建议**先把以下三处的 `birdindasky` / `Ask` 改成你自己的标识,
否则两个 app 会争抢同一个 macOS Keychain 条目和数据目录:

- `app/settings.py` 里 `APP_NAME` 和 `BUNDLE_ID`
- `setup.py` 里 plist 的 `CFBundleName` / `CFBundleDisplayName` / `CFBundleIdentifier`
- `Makefile` 里 `APP_NAME`(以及随之而来的 `DMG_NAME`)

改完 `make build && make dmg` 出来的就是你自己的独立 .app,数据落在 `~/Library/Application Support/<你的 APP_NAME>/`,与原作者环境互不干扰。

---

## ⚠️ 已知限制

- 图片附件目前以 base64 暂存,LLM 端只看到 `[附件图片: name]` 占位符;真正的视觉理解会在 v0.3 里接入 Anthropic / OpenAI / Gemini 的多模态 API。
- 没接代码签名 / 公证(本地自用,不走 Apple Notary)。要正经分发请配 Developer ID 后改 `setup.py`。
- v0.2 暂未做内置自动更新,版本升级靠重新 `make dmg`。

---

## 📋 验收清单

详细的人肉验收清单见 [`ACCEPTANCE.md`](./ACCEPTANCE.md)。

---

## 📜 License

[MIT](./LICENSE) © 2026 — 你随便用、改、商用、再分发都行,只是别拿来告我。

---

<div align="center">

**作者 [birdindasky](https://github.com/birdindasky) · 给懒得到处切标签的 vibecoder 准备的**

如果这个项目帮到你,star 一下 🌟 让作者高兴一会儿。

</div>
