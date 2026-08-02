# AugLoop Copilot Proxy

> 将 Microsoft 365 Excel Copilot 的 AugLoop AI 后端转换为 OpenAI 兼容 API 的反向代理服务器。

## 目录

- [功能概述](#功能概述)
- [用户准备清单](#用户准备清单)
- [前提条件](#前提条件)
- [限制说明](#限制说明)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [支持的模型](#支持的模型)
- [API 端点](#api-端点)
- [Token 管理机制](#token-管理机制)
- [架构原理](#架构原理)
- [故障排查](#故障排查)
- [集成示例](#集成示例)
- [自定义工具开发](#自定义工具开发)
- [安全建议](#安全建议)
- [FAQ](#faq)
- [文件结构](#文件结构)
- [不支持的功能清单](#不支持的功能清单)
- [开源协议](#开源协议)

---

## 功能概述

本项目是一个 **独立的反向代理服务器**，通过拦截 Microsoft Excel Copilot 的 AugLoop WebSocket 协议，将其转换为 **OpenAI 兼容的 API 接口**。

### 核心能力

| 能力 | 说明 |
|------|------|
| **OpenAI 兼容 API** | `POST /v1/chat/completions` — 任何支持 OpenAI API 的客户端都可以直接对接 |
| **流式响应** | 支持 SSE 流式输出 (`stream: true`) |
| **Function Calling** | 内置 8 种工具 (read_file, write_file, run_shell, run_python, http_get 等) |
| **对话管理** | SQLite 存储对话历史，支持多会话 |
| **Token 自动获取** | 5 种策略自动获取和刷新 Token，无需手动抓包 |
| **Copilot UI 可禁用** | 通过注册表禁用 Copilot UI 后仍可正常反代 |
| **提前刷新** | 过期前 10 分钟自动刷新 Token，确保不中断 |

---

## 用户准备清单

在开始使用前，请确认你已准备好以下所有条件：

### 必须准备

- [ ] **Windows 10/11 电脑**（不支持 Linux/macOS，内存扫描依赖 Windows API）
- [ ] **Python 3.10+**（推荐 3.12）— [下载地址](https://www.python.org/downloads/)
- [ ] **Microsoft Excel**（已安装并能正常启动）
- [ ] **Microsoft 365 账号**（需要有 Copilot 权限，ConsumerPro 或企业版均可）
- [ ] **Excel 中已登录** Microsoft 365 账号
- [ ] **网络可访问** `augloop.svc.cloud.microsoft`（中国大陆网络通常可直连）
- [ ] **至少在 Excel 中打开过一次 Copilot**（以在内存中初始化 AugLoop Token）

### 不需要准备

- ❌ 不需要 Frida（纯 Python `ctypes` 内存扫描）
- ❌ 不需要抓包工具（内置自动 Token 获取）
- ❌ 不需要 MITM 代理证书
- ❌ 不需要管理员权限（普通用户即可）
- ❌ 不需要 Copilot UI 可见（可通过注册表禁用）

---

## 前提条件

| 条件 | 必须 | 说明 |
|------|:---:|------|
| **Windows 10/11** | ✅ | 内存扫描使用 Windows API (`ctypes` + `OpenProcess`/`ReadProcessMemory`) |
| **Python 3.10+** | ✅ | 推荐 3.12，需要 `pip` 可用 |
| **Microsoft Excel** | ✅ | 必须安装并运行（JWE Token 从 Excel 进程内存获取） |
| **Microsoft 365 账号** | ✅ | 需要有 Copilot 权限（ConsumerPro 或企业版） |
| **Excel 登录状态** | ✅ | Excel 中需登录 Microsoft 365 账号 |
| **首次 Copilot 触发** | ✅ | Excel 需要至少打开过一次 Copilot 面板，以在内存中生成 Token |
| **网络访问** | ✅ | 需能访问 `augloop.svc.cloud.microsoft`（WebSocket 直连） |

### 为什么必须运行 Excel？

系统需要两种 Token：

```
① JWT anonymousToken (auth_token)
   用途: Phase 2 WebSocket 会话认证
   有效期: 24 小时
   获取方式: WebSocket Phase 1 自动返回
   ← 不需要 Excel! 只需连接 augloop.svc.cloud.microsoft

② JWE Bearer Token (bearer_token)
   用途: Licensing Check + TokenProvision
   有效期: 服务端实际仅 ~4 分钟（客户端缓存 150 秒）
   获取方式: 扫描 Excel 进程内存 (ctypes ReadProcessMemory)
   ← 必须运行 Excel!
   还需要两个不同的 JWE Token (Token A + Token B)
```

如果 Excel 未运行：
- 步骤 ① 仍可通过 WebSocket Phase 1 获取 JWT
- 步骤 ② 无法获取 JWE Token → Licensing Check 失效 → 服务器静默忽略聊天请求

---

## 限制说明

### 技术限制

| 限制 | 详情 |
|------|------|
| **JWE Token 有效期极短** | 服务端实际仅约 4 分钟有效，系统每 2 分钟检查一次，过期前 10 分钟主动刷新 |
| **需要两个 JWE Token** | Licensing Check 需要两个不同的 JWE Token（Token A 主身份 + Token B 第二身份） |
| **Excel 进程必须存活** | 每次 WebSocket 连接前都会调用 `_refresh_jwe_token_from_memory()`，扫描 Excel 进程内存 |
| **仅支持 Windows** | 内存扫描依赖 Windows API，不支持 Linux/macOS |
| **Token 来源依赖 Excel 内存** | 如果 Excel 崩溃或被关闭，JWE Token 无法刷新，反代将失效 |
| **系统提示词在云端** | AI 的系统提示词由 Microsoft 云端服务注入，不在本地存储，无法修改 |

### 合规与安全

- 本项目仅供**授权研究和个人学习**使用
- 使用本工具需要有效的 Microsoft 365 Copilot 许可
- 请勿用于绕过 Microsoft 的使用条款或服务限制
- Token 中包含个人身份信息（用户 ID、组织 ID 等），请妥善保管
- `.augloop_token`、`config.yaml`、`conversations.db` 已被 `.gitignore` 排除，不会被上传

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/your-username/augloop-copilot-proxy.git
cd augloop-copilot-proxy
```

### 2. 创建配置文件

```bash
# 从模板复制配置文件
cp config.example.yaml config.yaml

# config.yaml 中的大部分字段会在首次运行时自动填充
# 你通常不需要手动修改任何内容
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 启动

#### 方式一：一键启动（推荐）

```bat
:: Windows 批处理（自动管理 Excel + Token + 服务器）
start.bat
```

`start.bat` 会自动完成：
1. 清理 8080 端口的旧进程
2. 检查 Python 和依赖
3. 启动 Excel（后台隐藏模式）
4. 自动触发 Copilot 初始化 Token
5. 启动反代服务器

#### 方式二：手动启动

```bash
# 含 Excel 后台 Token 收割
python run.py --auto-init

# 或直接启动服务器（Excel 已在运行且已有 Token）
python server.py
```

#### 方式三：禁用 Copilot UI 后启动

```powershell
# 1. 通过注册表禁用 Copilot UI（可选，不影响反代）
reg add "HKCU\Software\Microsoft\Office\16.0\Common\Copilot" /v "CopilotDisabled" /t REG_DWORD /d 1 /f

# 2. 启动
python run.py --auto-init
```

### 启动参数

```bash
python run.py [选项]

选项:
  --mode {hide,minimize,offscreen}   Excel 隐藏模式 (默认 hide)
  --interval FLOAT                    强制刷新间隔秒数 (默认 3000 = 50 分钟)
  --no-wait                          跳过等待用户, 直接隐藏 Excel
  --auto-init                        自动打开 Copilot 并发消息初始化
  --no-validate                      跳过 Token 验证
  --port INT                          服务器端口 (默认 8080)
  --host STR                          绑定地址 (默认 127.0.0.1)
```

---

## 配置说明

配置文件：`config.yaml`（首次运行从 `config.example.yaml` 复制）

```yaml
server:
  host: 127.0.0.1        # 服务器绑定地址
  port: 8080              # 服务器端口
  api_key: ''             # API 密钥（留空表示不需要认证）

augloop:
  base_url: https://augloop.svc.cloud.microsoft
  workflow: OfficeCopilotOrchestrationWorkflow
  bearer_token: ''        # JWE Token (自动填充)
  auth_token: ''         # JWT Token (自动填充)
  copilot_license_type: ConsumerPro
  strip_prompts: true     # 去除系统提示词中的 Copilot 包装

token_manager:
  auto_refresh: true
  refresh_interval: 120            # 检查间隔（秒）
  preemptive_refresh_threshold: 600 # 提前刷新阈值（秒）
  strategies:                      # Token 获取策略优先级
  - auto     # 内存扫描 (需要 Excel, 推荐)
  - wam      # MSAL.NET broker
  - har      # HAR 文件提取
  - mitm     # .augloop_token 文件
  - frida    # Frida 内存扫描

tools:
  enabled: true
  max_iterations: 5
  builtin_tools:
  - get_current_time
  - http_get
  - read_file
  - write_file
  - list_directory
  - run_python
  - run_shell
  - json_parse
```

---

## 支持的模型

### 模型列表

代理通过 AugLoop WebSocket 协议将请求转发给 Microsoft 云端，实际使用的 AI 模型由 **服务端 flights 配置决定**。代理会透传客户端请求中的 `model` 字段，但最终是否使用该模型取决于服务端。

#### 已知支持的模型

| 模型 | 厂商 | model 参数 | 说明 |
|------|------|-----------|------|
| **GPT-5.5** | OpenAI | `gpt-5.5` | OpenAI 最新 GPT 系列 |
| **GPT-5.6** | OpenAI | `gpt-5.6` | OpenAI 最新 GPT 系列 |
| **Claude Opus 4.8** | Anthropic | `claude-opus-4.8` | 代理默认模型（代码内置），Anthropic Claude Opus 系列 |
| **Claude Opus 5** | Anthropic | `claude-opus-5` | Anthropic 最新 Opus 系列 |
| **Claude Sonnet 5** | Anthropic | `claude-sonnet-5` | Anthropic Claude Sonnet 系列 |

#### Flights 配置中的模型 Slot

以下信息从 `flights.txt` 中提取，反映了 Microsoft 后端的模型路由配置：

**Claude 系列：**

| Slot | Model ID | 说明 |
|------|----------|------|
| Claude Slot 1 | 137 | `EAELlmClaudeSlot1ModelId` |
| Claude Slot 2 | 147 | `EAELlmClaudeSlot2ModelId`（Opus 4.8） |
| Claude Slot 5 | 156 | `EAELlmClaudeSlot5ModelId` |

**GPT 系列：**

| Slot | Model ID | 说明 |
|------|----------|------|
| GPT-5 Slot 1 | 135 | `EAELlmGpt5Slot1ModelId` |
| GPT-5 Slot 2 | 148 | `EAELlmGpt5Slot2ModelId` |
| EU 默认 | 135 | `EAELlmEUModelId` |

#### Agent Mode 变体

| 变体 | 说明 |
|------|------|
| `ClaudeAgentVariant` | Claude 通用变体 |
| `ClaudeOpus46AgentVariant` | Claude Opus 4.6 变体 |
| `ClaudeSlot1AgentVariant` | Claude Slot 1 变体 |
| `ClaudeSlot2AgentVariant` | Claude Slot 2 变体 |
| `Gpt5AgentVariant` | GPT-5 通用变体 |
| `Gpt54AgentVariant` | GPT-5.4 变体 |
| `Gpt5Slot1AgentVariant` | GPT-5 Slot 1 变体 |

### 模型选择机制

```
客户端请求 (model: "claude-opus-5")
    │
    ▼
代理透传 model 字段到 AugLoop ChatSignal
    │
    ▼
AugLoop 服务端根据 flights 配置路由到对应模型
    │
    ├─ model 匹配 → 使用指定模型
    └─ model 不匹配 → 回退到默认模型 (GPT-5, ModelId:135)
```

> **注意**：代理无法控制最终使用哪个模型。`model` 参数仅作为提示传递给 AugLoop 服务端，实际模型由 Microsoft 云端根据 flights 配置和负载情况决定。代理默认使用 `claude-opus-4.8`（代码内置）。

### 使用示例

```python
import openai

client = openai.OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="any")

# 使用默认模型 (claude-opus-4.8)
response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "你好"}],
)

# 指定模型
response = client.chat.completions.create(
    model="claude-opus-5",
    messages=[{"role": "user", "content": "你好"}],
)
```

### 模型可用性查询

```bash
# 查看当前服务端返回的模型列表
curl http://127.0.0.1:8080/v1/models

# 查看代理状态 (含当前模型)
curl http://127.0.0.1:8080/status
```

---

## API 端点

### 聊天 API（OpenAI 兼容）

```bash
# 发送聊天请求
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "copilot",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```

```bash
# 流式请求
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "copilot",
    "messages": [{"role": "user", "content": "1+1=?"}],
    "stream": true
  }'
```

### Python 调用示例

```python
import openai

client = openai.OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="any-string"  # config.yaml 中 api_key 为空时任意值即可
)

response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "用 Python 写一个快速排序"}],
)

print(response.choices[0].message.content)
```

### 管理端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /status` | GET | 代理状态 & Token 有效性 |
| `GET /token/status` | GET | Token 管理器详细状态 |
| `POST /token/auto` | POST | 🔑 全自动获取双 Token |
| `POST /token/refresh` | POST | 强制刷新 Token |
| `POST /token/manual` | POST | 手动设置 Token |
| `GET /v1/models` | GET | 模型列表 |
| `GET /v1/tools` | GET | 列出可用工具 |
| `GET /v1/conversations` | GET | 列出所有对话 |
| `POST /v1/conversations` | POST | 创建新对话 |
| `GET /` | GET | 桌面端 Web UI |

---

## Token 管理机制

### 两种 Token

| Token | 用途 | 有效期 | 获取方式 |
|-------|------|--------|---------|
| **JWE Bearer Token** | Licensing Check | ~4 分钟（服务端） | Excel 内存扫描 |
| **JWT anonymousToken** | WebSocket 认证 | 24 小时 | WebSocket Phase 1 |

### 自动刷新链路

```
每 2 分钟检查 (refresh_interval=120)
    │
    ▼
  Token 剩余时间 < 10 分钟? (preemptive_refresh_threshold=600)
    │ Yes
    ▼
  get_token(force_refresh=True) 按优先级尝试:
    │
    ├─ ① auto: ctypes 内存扫描 Excel 进程 → JWE Token
    ├─ ② mitm: 读取 .augloop_token 文件
    ├─ ③ frida: Frida 内存扫描
    ├─ ④ wam: MSAL.NET broker 静默获取
    ├─ ⑤ har: 从 HAR 文件提取
    │
    └─ 全部失败? → HTTP POST /token/auto (WebSocket Phase 1 保底)
         → 自动获取 anonymousToken (JWT, 24h) + 可能获取 JWE

每 50 分钟强制刷新一次 (run.py bg_scan_loop):
    │
    ├─ 触发 Excel Copilot 刷新 (Alt+Y + 发消息)
    ├─ 扫描 Excel 内存 → 找到新 Token
    ├─ 验证 Token 有效性 (Workflow API)
    └─ 保存有效 Token 到 .augloop_token + config.yaml
```

---

## 架构原理

```
┌─────────────────────────────────────────────────────────────┐
│                    你的应用程序 / 客户端                      │
│              (OpenAI API 兼容, 任何 SDK 都可对接)              │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP POST /v1/chat/completions
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  本反代服务器 (server.py)                     │
│                    127.0.0.1:8080                            │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ FastAPI 路由  │  │ ToolRegistry │  │ ConversationStore│   │
│  │ (OpenAI 兼容) │  │ (8 种工具)    │  │ (SQLite 对话存储) │   │
│  └──────┬───────┘  └──────────────┘  └──────────────────┘   │
│         │                                                   │
│  ┌──────▼───────────────────────────────────────────────┐  │
│  │              AugLoopWSClient (WebSocket)              │  │
│  │                                                       │  │
│  │  Phase 1: 连接 wss://augloop.svc.cloud.microsoft/    │  │
│  │    → 获取 anonymousToken (JWT, 24h) + sliceUrl       │  │
│  │                                                       │  │
│  │  Phase 2: 连接 sliceUrl                               │  │
│  │    → 26 个 AnnotationActivation                       │  │
│  │    → Licensing Check (JWE Token A + B)               │  │
│  │    → TokenProvision (JWE Token)                      │  │
│  │    → CheckPermissionSignal                           │  │
│  │    → 发送聊天请求 → 接收流式 AI 响应                   │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              TokenManager (自动刷新)                    │  │
│  │  每 2 分钟检查, 过期前 10 分钟主动刷新                   │  │
│  │  策略: auto → mitm → frida → wam → har → HTTP fallback │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │           memory_token_scanner (ctypes)               │  │
│  │  OpenProcess → VirtualQueryEx → ReadProcessMemory    │  │
│  │  扫描 EXCEL.EXE 内存中的 JWE/JWT Token                │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │                                    │
          │ WebSocket                           │ ctypes
          │                                     │
          ▼                                     ▼
┌──────────────────────────┐     ┌─────────────────────────┐
│  Microsoft AugLoop 云端    │     │   Excel 进程 (EXCEL.EXE)  │
│  augloop.svc.cloud.        │     │                         │
│  microsoft                 │     │  内存中的 JWE Token:     │
│                            │     │  eyJhbGciOiJkaXIi...    │
│  → 注入系统提示词            │     │  (有效期 ~4 分钟)        │
│  → 调用 LLM (GPT-5/Claude)  │     │                         │
│  → 返回流式响应              │     │                         │
└──────────────────────────┘     └─────────────────────────┘
```

---

## 故障排查

### 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| **"Token 为空且无法从内存刷新"** | Excel 未运行或未打开过 Copilot | 启动 Excel，打开 Copilot 面板，发送一条消息 |
| **"TokenProvision 失败 (JWE Token 已过期)"** | JWE Token 已过期（>4 分钟） | 调用 `POST /token/auto` 刷新，或在 Excel Copilot 中发一条消息 |
| **服务器返回 SyncResponse 但不处理聊天** | Licensing Check 未通过 | 确认 Excel 运行中，调用 `POST /token/auto` |
| **"未收到 UserAllowedAnnotation"** | 权限验证未通过 | 检查 JWE Token 是否有效，确认 Microsoft 365 账号有 Copilot 权限 |
| **端口 8080 被占用** | 旧进程未关闭 | `start.bat` 会自动清理，或手动 `taskkill /F /PID <pid>` |
| **WebSocket 连接失败** | 网络问题或 AugLoop 服务不可用 | 检查网络，确认能访问 `augloop.svc.cloud.microsoft` |
| **内存扫描找不到 Token** | Excel 进程中无 AugLoop Token | 确认 Excel 已登录 Microsoft 365 账号，且打开过 Copilot |
| **所有 JWE Token 已过期** | Excel 的 AugLoop Token 超过 4 分钟未刷新 | 系统会自动触发 Excel 刷新，等待 50 分钟强制刷新周期 |

### 调试命令

```bash
# 查看 Token 状态
curl http://127.0.0.1:8080/token/status

# 查看代理状态
curl http://127.0.0.1:8080/status

# 全自动获取 Token
curl -X POST http://127.0.0.1:8080/token/auto

# 手动设置 Token
curl -X POST http://127.0.0.1:8080/token/manual \
  -H "Content-Type: application/json" \
  -d '{"token": "eyJhbGci..."}'

# 测试聊天
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"hello"}]}'
```

---

## 集成示例

### Node.js

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'http://127.0.0.1:8080/v1',
  apiKey: 'any-string',
});

const response = await client.chat.completions.create({
  model: 'copilot',
  messages: [{ role: 'user', content: '用 JavaScript 写一个 debounce 函数' }],
});

console.log(response.choices[0].message.content);
```

### LangChain (Python)

```python
from langchain.chat_models import ChatOpenAI
from langchain.schema import HumanMessage

llm = ChatOpenAI(
    base_url="http://127.0.0.1:8080/v1/v1",
    api_key="any-string",
    model="copilot",
)

response = llm.invoke([HumanMessage(content="解释什么是闭包")])
print(response.content)
```

### cURL (流式)

```bash
curl -N http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"讲个笑话"}],"stream":true}'
```

### 使用 Function Calling

```python
import openai

client = openai.OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="any")

response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "当前时间是多少？"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前系统时间",
            "parameters": {"type": "object", "properties": {}}
        }
    }],
)

print(response.choices[0].message.content)
# AI 会调用 get_current_time 工具并返回实际时间
```

### 对接 Web UI (浏览器)

直接访问 `http://127.0.0.1:8080/` 即可使用内置的 Web UI 进行对话，无需任何客户端。

---

## 自定义工具开发

你可以通过 `ToolRegistry` 注册自定义工具，扩展 AI 的能力：

```python
from tool_registry import ToolRegistry

registry = ToolRegistry()

# 注册自定义工具
registry.register(
    name="weather",
    description="查询指定城市的天气",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名"}
        },
        "required": ["city"]
    },
    handler=lambda city: f"{city} 今天晴, 25°C"
)
```

工具运行在**代理服务器上**，不是 Excel 内。可用工具列表通过 `GET /v1/tools` 查看。

---

## 安全建议

### 本机使用（默认）

默认配置 `host: 127.0.0.1` 只允许本机访问，无需额外安全措施。

### 局域网/远程使用（不推荐）

如果必须暴露到网络：

1. **设置 API Key**：`config.yaml` 中 `api_key: "your-secret-key"`
2. **使用反向代理**：通过 Nginx 添加 TLS 和速率限制
3. **绑定内网 IP**：`host: 192.168.x.x` 而非 `0.0.0.0`
4. **防火墙**：仅允许信任 IP 访问 8080 端口

### Token 安全

- Token 中包含个人身份信息，**请勿公开分享**
- `.augloop_token` 和 `config.yaml` 已被 `.gitignore` 排除
- 日志文件可能包含 Token 片段，**请勿公开日志**
- 如果 Token 泄露，在 Excel 中关闭并重新打开 Copilot 会生成新 Token

---

## FAQ

**Q: 没有 Microsoft 365 Copilot 订阅可以用吗？**

A: 不行。系统需要有效的 Copilot 许可证 (`ConsumerPro` 或企业版)，Licensing Check 会验证 Token 中的许可证信息。

**Q: 禁用 Copilot UI 后还能用吗？**

A: 可以。通过注册表 `CopilotDisabled=1` 禁用 Copilot UI 后，AugLoop 运行时仍在 Excel 进程内存中，反代正常工作。

**Q: 如何重新启用 Copilot UI？**

A: 删除注册表项或设为 0：
```powershell
reg add "HKCU\Software\Microsoft\Office\16.0\Common\Copilot" /v "CopilotDisabled" /t REG_DWORD /d 0 /f
```

**Q: Excel 闪退了怎么办？**

A: 重新启动 Excel，打开任意工作簿，打开 Copilot 面板发送一条消息，然后调用 `POST /token/auto` 获取新 Token。

**Q: 可以同时开多个 Excel 实例吗？**

A: `run.py` 会通过 COM Dispatch 启动自己的 Excel 实例，只管理该实例。你已打开的其他 Excel 不受影响。

**Q: 反代会影响 Excel 的正常使用吗？**

A: 不会。反代通过独立的 WebSocket 连接 AugLoop，不干扰 Excel 的正常运行。50 分钟强制刷新时会短暂触发 Copilot（后台自动完成）。

**Q: 为什么 AI 总说自己是"Excel 助手"？**

A: 系统提示词由 Microsoft 云端注入，代理无法修改。`prompt_stripper.py` 可以删除请求中的部分提示词，但云端注入的系统人格无法改变。**2026-08-03 已实测证实**（见 `TEST_REPORT_2026-08-03.md`）：即使在 WebSocket 帧层做 MITM 改写（注入 system 消息 + systemPrompt 字段 + 前置指令到 query），模型也会看到注入内容并明确拒绝，维持 Excel 人格——服务端系统提示词优先级高于客户端一切字段。

**Q: 支持流式输出吗？**

A: 支持。设置 `"stream": true` 即可使用 SSE 流式输出。

**Q: 对话历史会丢失吗？**

A: 不会。对话历史存储在 `conversations.db` (SQLite) 中，重启后可通过 `GET /v1/conversations` 恢复。但 AugLoop WebSocket 会话每次重启会重建。

---

## 文件结构

```
copilot_proxy/
├── README.md                   ← 本文件
├── LICENSE                     ← MIT 开源协议
├── .gitignore                  ← Git 忽略规则 (排除敏感文件)
├── config.example.yaml         ← 配置模板 (脱敏, 请复制为 config.yaml)
├── config.yaml                 ← 实际配置 (.gitignore 排除, 自动生成)
├── requirements.txt            ← Python 依赖
├── start.bat                   ← Windows 一键启动脚本
├── run.py                      ← 一键启动 (Excel 后台 + 50分钟强制刷新)
├── server.py                   ← FastAPI 主服务器 (OpenAI 兼容 API)
│
├── augloop_ws_client.py        ← AugLoop WebSocket 客户端 (核心协议)
├── augloop_client.py           ← AugLoop HTTP API 客户端
├── token_manager.py            ← 统一 Token 管理 (5种策略 + 提前刷新 + HTTP回退)
├── memory_token_scanner.py     ← 纯 Python 内存扫描 (ctypes, 不依赖 Frida)
├── excel_background_runner.py  ← Excel 后台运行管理 (不保存过期Token)
├── excel_trigger.py            ← Excel Copilot 自动触发
│
├── prompt_stripper.py          ← 系统提示词剥离器
├── tool_registry.py            ← 工具注册表 (8种内置工具)
├── tool_call_parser.py         ← Function Calling 解析器
├── conversation_store.py       ← 对话存储 (SQLite)
├── desktop_ui.py               ← Web UI HTML
│
├── flights.txt                 ← Feature Gate 配置 (从抓包提取)
├── .augloop_token               ← 缓存 JWE Token (.gitignore 排除, 自动生成)
├── conversations.db             ← 对话数据库 (.gitignore 排除, 自动生成)
└── conversations.db-wal       ← SQLite WAL (.gitignore 排除, 自动生成)
```

### 敏感文件保护

以下文件已被 `.gitignore` 排除，**不会上传到 GitHub**：

| 文件 | 原因 |
|------|------|
| `config.yaml` | 含实际 Token 和会话 ID |
| `.augloop_token` | 含实际 JWE Token |
| `conversations.db` | 含聊天历史 |
| `*.log` | 日志可能含 Token 信息 |

上传前请确认 `config.yaml` 不在 Git 跟踪范围内。仓库中只包含 `config.example.yaml` 模板。

---

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。

### 使用声明

- 本项目仅供**授权研究和个人学习**使用
- 使用本工具需要有效的 Microsoft 365 Copilot 许可
- 请勿用于绕过 Microsoft 的使用条款或服务限制
- 作者不对本工具的滥用行为承担责任
- Token 和对话数据中可能包含个人身份信息，请妥善保管

### 贡献

欢迎提交 Issue 和 Pull Request。

---

## 不支持的功能清单

> 以下是本项目**明确不支持**的功能，请在使用前了解。

### 平台限制

| 不支持 | 原因 |
|--------|------|
| ❌ Linux / macOS | 内存扫描使用 Windows API (`ctypes` + `OpenProcess`/`ReadProcessMemory`)，无法跨平台 |
| ❌ 无头模式 (Headless) | Excel 必须以 GUI 进程运行，COM Dispatch 需要桌面会话 |
| ❌ Docker 容器 | Windows 容器不支持 COM 自动化 + 进程内存扫描 |
| ❌ 远程 Excel | 内存扫描只能读本机进程，不支持扫描远程机器的 Excel |

### 其他 Office 应用

| 不支持 | 说明 |
|--------|------|
| ❌ PowerPoint Copilot | 不同的 sdxs 插件和 workflow，本代理只支持 Excel 的 `OfficeCopilotOrchestrationWorkflow` |
| ❌ Word Copilot | 不同的 Host 类型 (`Document` vs `Workbook`) |
| ❌ OneNote Copilot | 不同的 Host 类型 (`Notebook`) |
| ❌ Outlook Copilot | 不同的后端服务和认证体系 |
| ❌ Teams Copilot | 不走 `augloop.svc.cloud.microsoft` |

### Excel Copilot 高级功能

| 不支持 | 说明 |
|--------|------|
| ❌ 公式自动补全 | 独立的 `CopilotFormulaCompletion` 信号类型，不是聊天请求 |
| ❌ 表格智能分析 (Table Lint) | 独立的 `TableLint` workflow |
| ❌ 文档上下文 (单元格/区域读取) | 代理不发送 Excel 文档数据给 AI，AI 不知道你的工作簿内容 |
| ❌ Excel 内 Python 执行 | Excel 的 Python 沙箱，与代理的 `run_python` 工具完全不同 |
| ❌ 图表创建/修改 | 需要 Copilot 的 ExcelAgent 技能和文档上下文 |
| ❌ 数据透视表建议 | 需要 Copilot 的 Agent 技能 |
| ❌ 条件格式建议 | Excel Copilot 特有功能，不在聊天 workflow 中 |
| ❌ 公式转换 | 独立 workflow |
| ❌ 文本分析/标记/分类 | 独立 annotation 类型 |

### AI 模型与参数控制

| 不支持 | 说明 |
|--------|------|
| ❌ 选择 AI 模型 | 模型由服务端根据 flights 决定（GPT-5 / Claude Opus 等），代理无法控制 |
| ❌ 设置 temperature | AugLoop 协议不暴露此参数 |
| ❌ 设置 max_tokens | 同上 |
| ❌ 设置 top_p / frequency_penalty | 同上 |
| ❌ 自定义系统提示词 | 系统提示词由 Microsoft 云端注入，`prompt_stripper.py` 只能删除请求中的提示词，不能添加自定义 |
| ❌ 自定义 AI 人格 | AI 始终认为自己是"Excel 助手"，无法改变 |

### 企业级功能

| 不支持 | 说明 |
|--------|------|
| ❌ 企业搜索 (Enterprise Search) | 需要 SharePoint 连接器 + 企业租户，代理已禁用此功能 |
| ❌ Power BI MCP 查询 | 需要 Power BI 租户 |
| ❌ Fabric MCP | 需要 Microsoft Fabric 租户 |
| ❌ 联合连接器 (Federated Connectors) | 需要企业配置 |
| ❌ 敏感度标签 (Sensitivity Labels) | 需要 Microsoft Purview |
| ❌ DLP 策略检查 | 需要 Microsoft 365 企业版 |

### 多用户与并发

| 不支持 | 说明 |
|--------|------|
| ❌ 多用户 | 单 Excel 实例 = 单 Token 集 = 单用户，无法支持多租户 |
| ❌ 并发请求 | WebSocket 是单连接，不支持同时多个聊天请求 |
| ❌ 水平扩展 | 不能添加更多 Excel 实例（内存扫描绑定到特定 PID） |
| ❌ 会话持久化 | 重启后 AugLoop WebSocket 会话丢失（对话历史在 SQLite 中保留） |

### 内容与媒体

| 不支持 | 说明 |
|--------|------|
| ❌ 图片生成 | DALL-E/图片生成是 PowerPoint 专用功能 |
| ❌ 多模态输入 | 代理只发送文本，不支持图片/文件上传 |
| ❌ 文件上传到 Copilot | Graph API copilotuploads 未完全实现 |
| ❌ 文档接地 (Grounding) | AI 不知道你的 Excel 文件内容，无法基于工作簿数据回答 |

### 安全

| 不支持 | 说明 |
|--------|------|
| ❌ HTTPS/TLS | 代理服务器默认 HTTP（建议仅在本机使用，不暴露到公网） |
| ❌ 默认 API 认证 | `api_key` 留空表示不需要认证（建议设置 api_key 后再使用） |
| ❌ 请求速率限制 | 无 rate limiting 机制 |
| ❌ 日志脱敏 | 日志中可能打印 Token 信息（请勿公开日志） |

### 运维

| 不支持 | 说明 |
|--------|------|
| ❌ Excel 崩溃自动恢复 | 无 watchdog 机制，Excel 崩溃后代理失效 |
| ❌ 零停机刷新 | 50 分钟强制刷新期间会短暂中断活跃对话 |
| ❌ WebSocket 长连接 | 每次聊天请求新建 WebSocket 连接 |
| ❌ 跨重启状态恢复 | 对话历史保留在 SQLite，但 AugLoop session 每次重启重建 |

### 工具能力差异

代理的 `ToolRegistry` 提供 8 种工具，但它们运行在**代理服务器上**，不是 Excel 内：

| 代理工具 | 运行位置 | 能否操作 Excel |
|---------|---------|:---:|
| `read_file` | 代理服务器文件系统 | ❌ 不能读 Excel 工作簿 |
| `write_file` | 代理服务器文件系统 | ❌ 不能写入 Excel 单元格 |
| `list_directory` | 代理服务器文件系统 | ❌ 不能列出 Excel 工作表 |
| `run_python` | 代理服务器 Python | ❌ 不是 Excel 内的 Python |
| `run_shell` | 代理服务器 Shell | ❌ 与 Excel 无关 |
| `http_get` | 代理服务器网络 | — 与 Excel 无关，可正常使用 |
| `get_current_time` | 系统时间 | — 可正常使用 |
| `json_parse` | 纯计算 | — 可正常使用 |

> **关键差异**：Excel Copilot 的原生工具可以直接读写**当前打开的 Excel 工作簿**（单元格、公式、图表），而代理的工具只能操作代理服务器的文件系统。AI 在回复中会提到"Excel 操作"能力，但实际上它无法通过代理工具操作你的 Excel。
