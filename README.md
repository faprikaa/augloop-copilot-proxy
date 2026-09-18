# AugLoop Copilot Proxy

[English](README.md) | [中文](README_zh.md)

> A reverse proxy server transforming Microsoft 365 Excel Copilot's AugLoop AI backend into an OpenAI-compatible API.

## Table of Contents

- [Overview](#overview)
- [Preparation Checklist](#preparation-checklist)
- [Prerequisites](#prerequisites)
- [Limitations](#limitations)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Supported Models](#supported-models)
- [API Endpoints](#api-endpoints)
- [Token Management](#token-management)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)
- [Integration Examples](#integration-examples)
- [Custom Tool Development](#custom-tool-development)
- [Security Recommendations](#security-recommendations)
- [FAQ](#faq)
- [Project Structure](#project-structure)
- [Unsupported Features](#unsupported-features)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Overview

This project is a **standalone reverse proxy server** that intercepts and bridges Microsoft Excel Copilot's AugLoop WebSocket protocol, exposing an **OpenAI-compatible REST API interface**.

### Core Capabilities

| Capability | Description |
|------------|-------------|
| **OpenAI-Compatible API** | `POST /v1/chat/completions` — Direct integration with any OpenAI SDK or compatible client |
| **Streaming Responses** | Full Server-Sent Events (SSE) streaming support (`stream: true`) |
| **Function Calling** | Built-in 8 tools (`read_file`, `write_file`, `run_shell`, `run_python`, `http_get`, etc.) |
| **Conversation Management** | SQLite persistent conversation storage supporting multiple sessions |
| **Automated Token Harvesting** | 5 strategies to harvest and refresh tokens automatically without manual packet sniffing |
| **Copilot UI Can Be Disabled** | Operates normally even with the Copilot UI disabled via Windows Registry |
| **Preemptive Refresh** | Proactively refreshes tokens 10 minutes prior to expiration for uninterrupted service |

---

## Preparation Checklist

Before getting started, please ensure you have the following ready:

### Required

- [ ] **Windows 10/11 PC** (Linux/macOS not supported; memory scanning relies on Windows APIs)
- [ ] **Python 3.10+** (3.12 recommended) — [Download Python](https://www.python.org/downloads/)
- [ ] **Microsoft Excel** (installed and functional)
- [ ] **Microsoft 365 Account** (with Copilot license: ConsumerPro or Enterprise)
- [ ] **Logged in to Microsoft 365** inside Excel
- [ ] **Network connectivity** to `augloop.svc.cloud.microsoft`
- [ ] **Opened Copilot at least once in Excel** (initializes AugLoop tokens in process memory)

### Not Required

- ❌ No Frida installation needed (pure Python `ctypes` memory scanner)
- ❌ No packet sniffing tools needed (built-in automated token extraction)
- ❌ No MITM proxy certificates needed
- ❌ No Administrator privileges required (standard user account suffices)
- ❌ No visible Copilot UI needed (can be hidden or disabled via registry)

---

## Prerequisites

| Requirement | Mandatory | Description |
|-------------|:---------:|-------------|
| **Windows 10/11** | ✅ | Memory scanning utilizes Windows APIs (`ctypes` + `OpenProcess`/`ReadProcessMemory`) |
| **Python 3.10+** | ✅ | 3.12 recommended, with `pip` available |
| **Microsoft Excel** | ✅ | Must be installed and running (JWE tokens are harvested from Excel memory) |
| **Microsoft 365 Account** | ✅ | Requires active Copilot subscription (ConsumerPro or Enterprise) |
| **Excel Login State** | ✅ | Active Microsoft 365 account sign-in inside Excel |
| **Initial Copilot Launch** | ✅ | Copilot side-pane must be opened once to generate tokens in memory |
| **Network Access** | ✅ | WebSocket connection to `augloop.svc.cloud.microsoft` |

### Why Must Excel Be Running?

The system requires two distinct tokens:

```
① JWT anonymousToken (auth_token)
   Purpose: Phase 2 WebSocket session authentication
   Validity: 24 hours
   Extraction: Automatically returned by WebSocket Phase 1 handshake
   ← Does NOT require Excel! Connects directly to augloop.svc.cloud.microsoft

② JWE Bearer Token (bearer_token)
   Purpose: Licensing Check + TokenProvision
   Validity: Server-side ~4 minutes (client caches for 150 seconds)
   Extraction: Scanned from Excel process memory (ctypes ReadProcessMemory)
   ← MUST run Excel!
   Also requires two distinct JWE tokens (Token A + Token B)
```

If Excel is not running:
- Step ① still obtains the JWT token via WebSocket Phase 1.
- Step ② fails to obtain JWE tokens → Licensing Check fails → Server silently drops chat requests.

---

## Limitations

### Technical Limitations

| Limitation | Details |
|------------|---------|
| **Short JWE Token Lifespan** | Server-side validity is only ~4 minutes. The system checks every 2 minutes and refreshes 10 minutes before recorded expiry. |
| **Dual JWE Tokens Required** | Licensing check requires two distinct JWE tokens (Token A for primary identity + Token B for secondary identity). |
| **Excel Process Must Stay Alive** | `_refresh_jwe_token_from_memory()` scans Excel memory before each WebSocket connection. |
| **Windows Only** | Memory scanning depends on Win32 APIs; Linux and macOS are unsupported. |
| **Token Source Tied to Excel Memory** | If Excel crashes or exits, JWE tokens cannot be renewed and the proxy will stop responding. |
| **System Prompts Injected Cloud-Side** | AI system prompts are injected by Microsoft cloud servers at inference time and cannot be overridden locally. |

### Compliance & Safety

- This project is intended strictly for **authorized research and educational purposes**.
- A valid Microsoft 365 Copilot license is required.
- Do not use this tool to bypass Microsoft Terms of Service or service quotas.
- Tokens contain personal identity metadata (User ID, Tenant ID, etc.); keep them secure.
- `.augloop_token`, `config.yaml`, and `conversations.db` are excluded by `.gitignore` and are not committed.

---

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/augloop-copilot-proxy.git
cd augloop-copilot-proxy
```

### 2. Configure

```bash
# Copy configuration template
cp config.example.yaml config.yaml

# Most fields in config.yaml will be populated automatically upon first run
# Manual modification is usually not needed
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Start the Proxy

#### Option 1: One-Click Launch (Recommended)

```bat
:: Windows batch script (manages Excel + Token harvesting + Proxy server)
start.bat
```

`start.bat` automatically:
1. Cleans up any existing process listening on port 8080
2. Validates Python environment and dependencies
3. Launches Excel in hidden background mode
4. Initializes Copilot token harvesting
5. Starts the reverse proxy server

#### Option 2: Manual Launch

```bash
# With background Excel token harvester
python run.py --auto-init

# Or start the server directly (if Excel is already running with tokens harvested)
python server.py
```

#### Option 3: Launch with Disabled Copilot UI

```powershell
# 1. Disable Copilot UI via Registry (optional, does not affect the proxy)
reg add "HKCU\Software\Microsoft\Office\16.0\Common\Copilot" /v "CopilotDisabled" /t REG_DWORD /d 1 /f

# 2. Start
python run.py --auto-init
```

### Launch Arguments

```bash
python run.py [options]

Options:
  --mode {hide,minimize,offscreen}   Excel window hide mode (default: hide)
  --interval FLOAT                    Forced refresh interval in seconds (default: 3000 = 50 minutes)
  --no-wait                          Do not wait for user input, hide Excel immediately
  --auto-init                        Automatically open Copilot and send a message to initialize
  --no-validate                      Skip token validation
  --port INT                         Proxy server port (default: 8080)
  --host STR                         Bind address (default: 127.0.0.1)
```

---

## Configuration

Configuration file: `config.yaml` (copied from `config.example.yaml` on first launch)

```yaml
server:
  host: 127.0.0.1        # Server bind address
  port: 8080              # Server listening port
  api_key: ''             # API key (empty means no authentication required)

augloop:
  base_url: https://augloop.svc.cloud.microsoft
  workflow: OfficeCopilotOrchestrationWorkflow
  bearer_token: ''        # JWE Token (auto-filled)
  auth_token: ''         # JWT Token (auto-filled)
  copilot_license_type: ConsumerPro
  strip_prompts: true     # Strip Copilot wrappers from system prompts

token_manager:
  auto_refresh: true
  refresh_interval: 120            # Check interval in seconds
  preemptive_refresh_threshold: 600 # Preemptive refresh threshold in seconds
  strategies:                      # Token acquisition strategy priority
  - auto     # Process memory scanning (requires Excel, recommended)
  - wam      # MSAL.NET broker
  - har      # HAR file extraction
  - mitm     # .augloop_token file
  - frida    # Frida memory scan

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

## Supported Models

### Model Overview

The proxy forwards requests to the Microsoft cloud via the AugLoop WebSocket protocol. The actual AI model invoked is **governed by server-side flight configurations**. The proxy relays the `model` parameter from client requests, but the server retains final routing authority.

#### Recognized Models

| Model | Provider | `model` Parameter | Description |
|-------|----------|-------------------|-------------|
| **GPT-5.5** | OpenAI | `gpt-5.5` | OpenAI GPT-5 series |
| **GPT-5.6** | OpenAI | `gpt-5.6` | OpenAI GPT-5 series |
| **Claude Opus 4.8** | Anthropic | `claude-opus-4.8` | Proxy default model (built-in fallback), Anthropic Claude Opus series |
| **Claude Opus 5** | Anthropic | `claude-opus-5` | Anthropic Claude Opus series |
| **Claude Sonnet 5** | Anthropic | `claude-sonnet-5` | Anthropic Claude Sonnet series |

#### Model Slots in Flight Configurations

Extracted from `flights.txt`, reflecting Microsoft backend model routing slots:

**Claude Series:**

| Slot | Model ID | Description |
|------|----------|-------------|
| Claude Slot 1 | 137 | `EAELlmClaudeSlot1ModelId` |
| Claude Slot 2 | 147 | `EAELlmClaudeSlot2ModelId` (Opus 4.8) |
| Claude Slot 5 | 156 | `EAELlmClaudeSlot5ModelId` |

**GPT Series:**

| Slot | Model ID | Description |
|------|----------|-------------|
| GPT-5 Slot 1 | 135 | `EAELlmGpt5Slot1ModelId` |
| GPT-5 Slot 2 | 148 | `EAELlmGpt5Slot2ModelId` |
| EU Default | 135 | `EAELlmEUModelId` |

#### Agent Mode Variants

| Variant | Description |
|---------|-------------|
| `ClaudeAgentVariant` | Claude general variant |
| `ClaudeOpus46AgentVariant` | Claude Opus 4.6 variant |
| `ClaudeSlot1AgentVariant` | Claude Slot 1 variant |
| `ClaudeSlot2AgentVariant` | Claude Slot 2 variant |
| `Gpt5AgentVariant` | GPT-5 general variant |
| `Gpt54AgentVariant` | GPT-5.4 variant |
| `Gpt5Slot1AgentVariant` | GPT-5 Slot 1 variant |

### Model Selection Flow

```
Client Request (model: "claude-opus-5")
    │
    ▼
Proxy passes model parameter to AugLoop ChatSignal
    │
    ▼
AugLoop server routes request based on flights configuration
    │
    ├─ Model match → Designated model invoked
    └─ Model mismatch → Fallback to default model (GPT-5, ModelId: 135)
```

> **Note**: The proxy cannot strictly enforce model selection. The `model` parameter serves as a routing hint to AugLoop. The actual model is selected server-side based on flights and server load. The proxy defaults to `claude-opus-4.8`.

### Querying Model Availability

```bash
# List available models returned by the proxy
curl http://127.0.0.1:8080/v1/models

# Check proxy health and current model
curl http://127.0.0.1:8080/status
```

---

## API Endpoints

### Chat API (OpenAI Compatible)

```bash
# Standard chat completion
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "copilot",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

```bash
# Streaming chat completion
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "copilot",
    "messages": [{"role": "user", "content": "What is 1+1?"}],
    "stream": true
  }'
```

### Python SDK Example

```python
import openai

client = openai.OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="any-string"  # Any string works if api_key is empty in config.yaml
)

response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "Write a quicksort implementation in Python."}],
)

print(response.choices[0].message.content)
```

### Management Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /status` | GET | Proxy status & token validity |
| `GET /token/status` | GET | Detailed TokenManager status |
| `POST /token/auto` | POST | 🔑 Automated acquisition of dual tokens |
| `POST /token/refresh` | POST | Force token refresh |
| `POST /token/manual` | POST | Manually provide a token |
| `GET /v1/models` | GET | List available models |
| `GET /v1/tools` | GET | List registered tools |
| `GET /v1/conversations` | GET | List conversation sessions |
| `POST /v1/conversations` | POST | Create a new conversation session |
| `GET /` | GET | Built-in Web UI |

---

## Token Management

### Dual Token Roles

| Token | Purpose | Lifespan | Acquisition Method |
|-------|---------|----------|-------------------|
| **JWE Bearer Token** | Licensing Check | ~4 minutes (server) | Scanned from Excel memory |
| **JWT anonymousToken** | WebSocket Authentication | 24 hours | WebSocket Phase 1 handshake |

### Refresh Pipeline

```
Check every 2 minutes (refresh_interval=120)
    │
    ▼
  Token remaining time < 10 minutes? (preemptive_refresh_threshold=600)
    │ Yes
    ▼
  get_token(force_refresh=True) tries strategies in priority:
    │
    ├─ ① auto: ctypes memory scan of EXCEL.EXE → JWE Token
    ├─ ② mitm: Read .augloop_token file
    ├─ ③ frida: Frida memory scanning
    ├─ ④ wam: MSAL.NET broker silent acquisition
    ├─ ⑤ har: Extract from HAR file
    │
    └─ All failed? → HTTP POST /token/auto (WebSocket Phase 1 fallback)
         → Obtains anonymousToken (JWT, 24h) + scans memory for JWE

Forced refresh every 50 minutes (run.py bg_scan_loop):
    │
    ├─ Trigger Excel Copilot refresh (Alt+Y + send message)
    ├─ Scan Excel memory → Extract new token
    ├─ Validate token against Workflow API
    └─ Save valid token to .augloop_token + config.yaml
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Your Client / App                        │
│         (OpenAI API Compatible, any SDK/Language)           │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP POST /v1/chat/completions
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                 Proxy Server (server.py)                    │
│                      127.0.0.1:8080                         │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │FastAPI Routes│  │ ToolRegistry │  │ ConversationStore│   │
│  │(OpenAI spec) │  │  (8 tools)   │  │ (SQLite Storage) │   │
│  └──────┬───────┘  └──────────────┘  └──────────────────┘   │
│         │                                                   │
│  ┌──────▼───────────────────────────────────────────────┐  │
│  │              AugLoopWSClient (WebSocket)              │  │
│  │                                                       │  │
│  │  Phase 1: Connect to wss://augloop.svc.cloud.microsoft│  │
│  │    → Retrieve anonymousToken (JWT, 24h) + sliceUrl   │  │
│  │                                                       │  │
│  │  Phase 2: Connect to sliceUrl                         │  │
│  │    → 26 AnnotationActivation messages                 │  │
│  │    → Licensing Check (JWE Token A + B)               │  │
│  │    → TokenProvision (JWE Token)                      │  │
│  │    → CheckPermissionSignal                           │  │
│  │    → Send Chat Signal → Stream AI response tokens    │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │             TokenManager (Auto-Refresh)               │  │
│  │  Checks every 2 min; proactively refreshes at 10 min  │  │
│  │  auto → mitm → frida → wam → har → HTTP fallback      │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │           memory_token_scanner (ctypes)               │  │
│  │  OpenProcess → VirtualQueryEx → ReadProcessMemory    │  │
│  │  Scans EXCEL.EXE memory for JWE / JWT tokens         │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │                                    │
          │ WebSocket                           │ ctypes
          │                                     │
          ▼                                     ▼
┌──────────────────────────┐     ┌─────────────────────────┐
│ Microsoft AugLoop Cloud  │     │  Excel Process (EXCEL)  │
│ augloop.svc.cloud.       │     │                         │
│ microsoft                │     │  In-Memory JWE Token:   │
│                          │     │  eyJhbGciOiJkaXIi...    │
│ → Injects System Prompt  │     │  (Lifespan: ~4 minutes) │
│ → Calls LLM (GPT-5/Claude│     │                         │
│ → Streams Response Back  │     │                         │
└──────────────────────────┘     └─────────────────────────┘
```

---

## Troubleshooting

### Common Issues

| Issue | Cause | Resolution |
|-------|-------|------------|
| **"Token is empty and cannot be refreshed from memory"** | Excel is not running or Copilot was never opened | Launch Excel, open the Copilot pane, send a short message |
| **"TokenProvision failed (JWE Token expired)"** | JWE token has expired (>4 minutes) | Trigger `POST /token/auto` or send a message in Excel Copilot |
| **Server returns SyncResponse but drops chat request** | Licensing Check failed | Ensure Excel is running and call `POST /token/auto` |
| **"UserAllowedAnnotation not received"** | Authorization check failed | Verify JWE token and ensure account has active Copilot license |
| **Port 8080 already in use** | Lingering server process | `start.bat` cleans this automatically, or run `taskkill /F /PID <pid>` |
| **WebSocket connection failure** | Network issue or AugLoop unreachable | Verify network route to `augloop.svc.cloud.microsoft` |
| **Memory scan finds no tokens** | Excel has not initialized AugLoop session | Ensure Excel is logged in with M365 and Copilot pane was opened |
| **All JWE tokens expired** | Tokens in Excel memory have lapsed past 4 min | The background runner triggers Excel automatically; wait for refresh cycle |

### Diagnostic Commands

```bash
# Inspect Token status
curl http://127.0.0.1:8080/token/status

# Inspect proxy health
curl http://127.0.0.1:8080/status

# Trigger automated token acquisition
curl -X POST http://127.0.0.1:8080/token/auto

# Manually provide a token
curl -X POST http://127.0.0.1:8080/token/manual \
  -H "Content-Type: application/json" \
  -d '{"token": "eyJhbGci..."}'

# Test a completion
curl -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"hello"}]}'
```

---

## Integration Examples

### Node.js

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'http://127.0.0.1:8080/v1',
  apiKey: 'any-string',
});

const response = await client.chat.completions.create({
  model: 'copilot',
  messages: [{ role: 'user', content: 'Write a debounce function in JavaScript.' }],
});

console.log(response.choices[0].message.content);
```

### LangChain (Python)

```python
from langchain.chat_models import ChatOpenAI
from langchain.schema import HumanMessage

llm = ChatOpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="any-string",
    model="copilot",
)

response = llm.invoke([HumanMessage(content="Explain what a closure is in programming.")])
print(response.content)
```

### cURL (Streaming)

```bash
curl -N http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"copilot","messages":[{"role":"user","content":"Tell me a joke."}],"stream":true}'
```

### Function Calling / Tools

```python
import openai

client = openai.OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="any")

response = client.chat.completions.create(
    model="copilot",
    messages=[{"role": "user", "content": "What is the current time?"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get current system time",
            "parameters": {"type": "object", "properties": {}}
        }
    }],
)

print(response.choices[0].message.content)
# AI invokes get_current_time and returns the real-time response
```

### Web UI

Open `http://127.0.0.1:8080/` in any browser to chat via the built-in Web interface without needing external clients.

---

## Custom Tool Development

Register custom tools via `ToolRegistry` to extend the assistant's capabilities:

```python
from tool_registry import ToolRegistry

registry = ToolRegistry()

# Register custom tool
registry.register(
    name="weather",
    description="Check weather for a specified city",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"}
        },
        "required": ["city"]
    },
    handler=lambda city: f"{city} is sunny, 25°C"
)
```

Tools execute on the **proxy server host**, not inside Excel. Available tools can be queried via `GET /v1/tools`.

---

## Security Recommendations

### Localhost Usage (Default)

The default configuration `host: 127.0.0.1` restricts access exclusively to the local machine, requiring no extra security layers.

### Network / Remote Deployment (Not Recommended)

If exposing the proxy across a network:
1. **Set an API Key**: Configure `api_key: "your-strong-key"` in `config.yaml`.
2. **Reverse Proxy**: Use Nginx or Caddy to provide TLS termination and rate limiting.
3. **Private IP Binding**: Bind to `192.168.x.x` or a VPN interface rather than `0.0.0.0`.
4. **Firewall**: Restrict port 8080 ingress to trusted IP addresses only.

### Token Security

- Harvested tokens contain tenant and identity claims; **never share them publicly**.
- `.augloop_token` and `config.yaml` are ignored by git.
- Log outputs may contain truncated token fragments; **do not share unredacted logs**.
- If a token is compromised, closing and re-opening Excel Copilot invalidates old tokens and generates fresh ones.

---

## FAQ

**Q: Can I use this without a Microsoft 365 Copilot subscription?**  
A: No. A valid Copilot license (`ConsumerPro` or Enterprise) is strictly validated during the WebSocket Licensing Check.

**Q: Does the proxy function if the Copilot UI is disabled in Excel?**  
A: Yes. Disabling the UI via `CopilotDisabled=1` in the registry stops the side-pane from rendering, but the underlying AugLoop runtime remains loaded in Excel's memory space.

**Q: How do I re-enable the Copilot UI?**  
A: Remove the registry entry or set it to 0:
```powershell
reg add "HKCU\Software\Microsoft\Office\16.0\Common\Copilot" /v "CopilotDisabled" /t REG_DWORD /d 0 /f
```

**Q: What if Excel crashes?**  
A: Restart Excel, open any workbook, trigger Copilot once, and invoke `POST /token/auto` to harvest fresh tokens.

**Q: Can I run multiple Excel instances?**  
A: `run.py` launches and tracks its own dedicated Excel instance via COM Dispatch. Any other Excel windows you have open remain untouched.

**Q: Does the proxy interfere with normal Excel work?**  
A: No. The proxy maintains independent WebSocket connections. The 50-minute refresh cycle briefly triggers Copilot in the background without disturbing user input.

**Q: Why does the AI always identify as an "Excel Assistant"?**  
A: The system persona is injected by Microsoft's cloud servers at inference time and cannot be overridden by client requests. As empirically confirmed in our [Test Report](TEST_REPORT_2026-08-03.md), even real-time MITM rewriting of WebSocket frames is recognized and declined by the model in favor of the cloud-side prompt.

**Q: Is streaming supported?**  
A: Yes. Specify `"stream": true` to receive SSE streaming chunks.

**Q: Will conversation history persist across restarts?**  
A: Dialogue history persists in `conversations.db` (SQLite) and can be retrieved via `GET /v1/conversations`. However, active WebSocket connections to AugLoop are recreated per session.

---

## Project Structure

```
copilot_proxy/
├── README.md                   ← English documentation (this file)
├── README_zh.md                ← Chinese documentation
├── TEST_REPORT_2026-08-03.md   ← Test report (English)
├── TEST_REPORT_2026-08-03_zh.md← Test report (Chinese)
├── LICENSE                     ← MIT License
├── .gitignore                  ← Git ignore rules (sensitive file exclusions)
├── config.example.yaml         ← Template configuration
├── config.yaml                 ← Active configuration (git-ignored, auto-generated)
├── requirements.txt            ← Python dependencies
├── start.bat                   ← Windows one-click start script
├── run.py                      ← One-click launcher (Excel background + periodic refresh)
├── server.py                   ← FastAPI server (OpenAI-compatible endpoints)
│
├── augloop_ws_client.py        ← AugLoop WebSocket client (core protocol engine)
├── augloop_client.py           ← AugLoop HTTP API client
├── token_manager.py            ← Unified Token Manager (5 strategies + preemptive refresh)
├── memory_token_scanner.py     ← Pure Python memory scanner (ctypes Win32 API)
├── excel_background_runner.py  ← Excel background process lifecycle manager
├── excel_trigger.py            ← Automated Excel Copilot UI trigger
│
├── prompt_stripper.py          ← System prompt wrapper stripper
├── tool_registry.py            ← Tool registry with 8 built-in tools
├── tool_call_parser.py         ← Function calling parser
├── conversation_store.py       ← SQLite conversation persistence
├── desktop_ui.py               ← Web UI interface
│
├── flights.txt                 ← Feature gate configuration dump
├── .augloop_token               ← Cached JWE tokens (git-ignored, auto-generated)
├── conversations.db             ← SQLite database (git-ignored, auto-generated)
└── conversations.db-wal        ← SQLite WAL file (git-ignored, auto-generated)
```

### Sensitive File Protection

The following files are excluded via `.gitignore` and **must never be committed**:

| File | Reason |
|------|--------|
| `config.yaml` | Contains real session tokens and configuration |
| `.augloop_token` | Contains live JWE bearer tokens |
| `conversations.db` | Contains dialogue logs and history |
| `*.log` | May contain raw token fragments in debug output |

---

## Unsupported Features

### Platform Restrictions

| Unsupported | Reason |
|-------------|--------|
| ❌ Linux / macOS | Memory scanning uses Win32 APIs (`ctypes` + `OpenProcess`/`ReadProcessMemory`) |
| ❌ Headless Mode | Excel requires a desktop GUI session; COM Dispatch fails in session 0 |
| ❌ Docker Containers | Windows containers lack COM automation and desktop process memory access |
| ❌ Remote Excel | Memory scanning is restricted to the local host |

### Other Office Applications

| Unsupported | Details |
|-------------|---------|
| ❌ PowerPoint Copilot | Uses different sdxs add-ins and workflows |
| ❌ Word Copilot | Uses `Document` host instead of `Workbook` |
| ❌ OneNote Copilot | Uses `Notebook` host type |
| ❌ Outlook Copilot | Distinct backend service and authentication architecture |
| ❌ Teams Copilot | Does not route through `augloop.svc.cloud.microsoft` |

### Advanced Excel Copilot Features

| Unsupported | Details |
|-------------|---------|
| ❌ Formula Auto-Completion | Uses `CopilotFormulaCompletion` signal rather than chat |
| ❌ Table Lint | Uses separate `TableLint` workflow |
| ❌ Workbook Grounding | Proxy does not transmit workbook grid/cell data to the model |
| ❌ In-Excel Python Sandbox | Excel Python runs within Microsoft's cloud sandbox, separate from proxy tools |
| ❌ Chart Creation / Manipulation | Requires ExcelAgent skills with live workbook context |
| ❌ PivotTable Suggestions | Requires workbook document grounding |

### Model & Parameter Controls

| Unsupported | Details |
|-------------|---------|
| ❌ Strict Model Enforcing | Model routing is decided cloud-side via flights |
| ❌ Temperature Setting | Not supported by AugLoop protocol |
| ❌ max_tokens Setting | Not supported by AugLoop protocol |
| ❌ top_p / frequency_penalty | Not supported by AugLoop protocol |
| ❌ Custom System Persona | Server-side prompt takes absolute precedence over client fields |

### Tool Execution Scope

The proxy's `ToolRegistry` executes tools on the **proxy server host**, not within Excel:

| Tool | Execution Host | Can Access Excel Workbook? |
|------|----------------|:--------------------------:|
| `read_file` | Proxy filesystem | ❌ No |
| `write_file` | Proxy filesystem | ❌ No |
| `list_directory` | Proxy filesystem | ❌ No |
| `run_python` | Proxy Python environment | ❌ No (not Excel Python) |
| `run_shell` | Proxy shell | ❌ No |
| `http_get` | Proxy network stack | — Independent |
| `get_current_time` | System clock | — Independent |
| `json_parse` | Host CPU | — Independent |

---

## License

This project is open-source under the [MIT License](LICENSE).

### Disclaimer

- For **authorized research and personal learning** only.
- Requires a legitimate Microsoft 365 Copilot subscription.
- Do not use to circumvent Microsoft service limits or licensing requirements.
- The authors assume no liability for misuse of this software.

---

## Acknowledgments

- [LinuxDo Community](https://linux.do/)
