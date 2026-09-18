#!/usr/bin/env python3
"""
desktop_ui.py - AugLoop Copilot Desktop UI

Provides a full desktop Web UI, including:
  1. Dashboard - System status overview
  2. Chat - Streaming AI chat (with tool execution visualization)
  3. Token Management - Auto/Frida/WAM/HAR token acquisition
  4. Tools Explorer - View and execute tools
  5. Conversation History - Manage historical conversations

Usage:
  from desktop_ui import DESKTOP_UI_HTML, launch_desktop
  # Or run directly:
  python desktop_ui.py
"""

import webbrowser
import threading
import time
import sys
from pathlib import Path

DESKTOP_UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AugLoop Copilot - Desktop</title>
<style>
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --surface2: #1c2333;
  --border: #30363d;
  --primary: #58a6ff;
  --primary-hover: #79b8ff;
  --success: #3fb950;
  --warning: #d29922;
  --error: #f85149;
  --text: #c9d1d9;
  --muted: #8b949e;
  --sidebar-w: 220px;
  --radius: 10px;
  --radius-sm: 6px;
  --transition: 0.2s ease;
}
* { margin:0; padding:0; box-sizing:border-box; }
html,body { height:100%; overflow:hidden; }
body {
  font-family:'Segoe UI',system-ui,-apple-system,sans-serif;
  background:var(--bg); color:var(--text); font-size:14px;
  display:flex; flex-direction:column;
}

/* ── Top Bar ── */
.topbar {
  height:48px; background:var(--surface); border-bottom:1px solid var(--border);
  display:flex; align-items:center; padding:0 20px; gap:16px; flex-shrink:0;
}
.topbar .logo { font-size:16px; font-weight:700; color:var(--primary); white-space:nowrap; }
.topbar .spacer { flex:1; }
.topbar .indicator {
  display:flex; align-items:center; gap:6px; font-size:12px; color:var(--muted);
  padding:4px 12px; background:var(--surface2); border-radius:var(--radius-sm);
}
.topbar .dot { width:8px; height:8px; border-radius:50%; }
.topbar .dot.ok { background:var(--success); }
.topbar .dot.err { background:var(--error); }
.topbar .dot.warn { background:var(--warning); }

/* ── Layout ── */
.main { display:flex; flex:1; overflow:hidden; }
.sidebar {
  width:var(--sidebar-w); background:var(--surface); border-right:1px solid var(--border);
  display:flex; flex-direction:column; padding:12px 8px; gap:4px; flex-shrink:0;
}
.nav-item {
  display:flex; align-items:center; gap:10px; padding:10px 14px; border-radius:var(--radius-sm);
  cursor:pointer; color:var(--muted); transition:var(--transition); font-size:13px; font-weight:500;
}
.nav-item:hover { background:var(--surface2); color:var(--text); }
.nav-item.active { background:var(--primary); color:#fff; }
.nav-item .icon { font-size:16px; width:20px; text-align:center; }
.sidebar .sep { height:1px; background:var(--border); margin:8px 4px; }
.sidebar .version { margin-top:auto; padding:8px 14px; font-size:11px; color:var(--muted); }

.content { flex:1; overflow-y:auto; padding:24px; }
.panel { display:none; max-width:960px; margin:0 auto; }
.panel.active { display:block; }
.panel-title { font-size:20px; font-weight:700; margin-bottom:20px; color:var(--text); }

/* ── Cards ── */
.card {
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:20px; margin-bottom:16px;
}
.card-title { font-size:14px; font-weight:600; color:var(--primary); margin-bottom:14px; }

/* ── Status Grid ── */
.status-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.status-item {
  background:var(--surface2); border-radius:var(--radius-sm); padding:14px;
  display:flex; flex-direction:column; gap:4px;
}
.status-item .label { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; }
.status-item .value { font-size:15px; font-weight:600; }
.status-item .value.ok { color:var(--success); }
.status-item .value.err { color:var(--error); }
.status-item .value.warn { color:var(--warning); }

/* ── Buttons ── */
.btn {
  background:var(--primary); color:#0d1117; border:none; border-radius:var(--radius-sm);
  padding:10px 20px; font-size:13px; font-weight:600; cursor:pointer; transition:var(--transition);
  display:inline-flex; align-items:center; gap:6px;
}
.btn:hover { background:var(--primary-hover); }
.btn:disabled { opacity:0.5; cursor:not-allowed; }
.btn.secondary { background:var(--surface2); color:var(--text); border:1px solid var(--border); }
.btn.secondary:hover { border-color:var(--primary); }
.btn.danger { background:var(--error); color:#fff; }
.btn.success { background:var(--success); color:#0d1117; }
.btn.sm { padding:6px 14px; font-size:12px; }
.btn-row { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }

/* ── Inputs ── */
input[type=text], textarea, select {
  width:100%; background:var(--bg); color:var(--text); border:1px solid var(--border);
  border-radius:var(--radius-sm); padding:10px 14px; font-size:13px; outline:none;
  font-family:inherit; transition:var(--transition);
}
input:focus, textarea:focus, select:focus { border-color:var(--primary); }
textarea { resize:vertical; min-height:60px; }
label { font-size:12px; color:var(--muted); display:block; margin-bottom:6px; }
.field { margin-bottom:14px; }
.checkbox-row { display:flex; align-items:center; gap:16px; margin:10px 0; font-size:13px; }
.checkbox-row label { display:flex; align-items:center; gap:6px; cursor:pointer; margin:0; color:var(--text); }
.checkbox-row input[type=checkbox] { width:16px; height:16px; accent-color:var(--primary); }

/* ── Chat ── */
.chat-container { display:flex; flex-direction:column; height:calc(100vh - 48px - 48px); max-width:960px; margin:0 auto; }
.chat-messages { flex:1; overflow-y:auto; padding:8px 0; display:flex; flex-direction:column; gap:12px; }
.chat-msg {
  max-width:85%; padding:12px 16px; border-radius:var(--radius); font-size:13px; line-height:1.6;
  white-space:pre-wrap; word-break:break-word;
}
.chat-msg.user { align-self:flex-end; background:var(--primary); color:#0d1117; }
.chat-msg.assistant { align-self:flex-start; background:var(--surface); border:1px solid var(--border); }
.chat-msg .role { font-size:11px; font-weight:700; text-transform:uppercase; margin-bottom:4px; opacity:0.7; }
.chat-msg.user .role { color:#0d1117; }
.chat-msg.assistant .role { color:var(--primary); }
.chat-msg.error { align-self:center; background:rgba(248,81,73,0.15); border:1px solid var(--error); color:var(--error); }
.tool-call-box {
  margin:8px 0; padding:10px 14px; background:var(--surface2); border-left:3px solid var(--warning);
  border-radius:var(--radius-sm); font-size:12px;
}
.tool-call-box .tc-header { display:flex; align-items:center; gap:6px; font-weight:600; color:var(--warning); cursor:pointer; }
.tool-call-box .tc-result { margin-top:8px; padding:8px; background:var(--bg); border-radius:4px; font-family:'Cascadia Code',monospace; font-size:11px; max-height:200px; overflow-y:auto; white-space:pre-wrap; }
.tool-call-box .tc-result.collapsed { display:none; }
.chat-input-area { padding:12px 0; border-top:1px solid var(--border); display:flex; gap:10px; align-items:flex-end; }
.chat-input-area textarea { flex:1; min-height:44px; max-height:120px; }
.chat-options { display:flex; gap:12px; align-items:center; margin-bottom:8px; font-size:12px; color:var(--muted); flex-wrap:wrap; }
.chat-options select { font-size:12px; padding:3px 8px; max-width:200px; background:var(--surface2); color:var(--text); border:1px solid var(--border); border-radius:var(--radius-sm); outline:none; }
.chat-options select:focus { border-color:var(--primary); }
.typing-indicator { display:flex; gap:4px; padding:8px; }
.typing-indicator span { width:8px; height:8px; background:var(--muted); border-radius:50%; animation:blink 1.4s infinite both; }
.typing-indicator span:nth-child(2) { animation-delay:0.2s; }
.typing-indicator span:nth-child(3) { animation-delay:0.4s; }
@keyframes blink { 0%,80%,100%{opacity:0.3;} 40%{opacity:1;} }

/* ── Token Panel ── */
.token-method {
  background:var(--surface2); border-radius:var(--radius-sm); padding:16px; margin-bottom:12px;
  border:1px solid var(--border);
}
.token-method .tm-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
.token-method .tm-title { font-weight:600; font-size:14px; }
.token-method .tm-desc { font-size:12px; color:var(--muted); margin-bottom:8px; }
.token-method .tm-status { font-size:12px; padding:2px 8px; border-radius:4px; }
.token-method .tm-status.idle { color:var(--muted); background:var(--bg); }
.token-method .tm-status.running { color:var(--warning); background:rgba(210,153,34,0.15); }
.token-method .tm-status.success { color:var(--success); background:rgba(63,185,80,0.15); }
.token-method .tm-status.error { color:var(--error); background:rgba(248,81,73,0.15); }
.token-method .tm-log {
  margin-top:8px; padding:8px; background:var(--bg); border-radius:4px;
  font-family:'Cascadia Code',monospace; font-size:11px; max-height:120px; overflow-y:auto;
  white-space:pre-wrap; color:var(--muted);
}
.progress-bar { height:4px; background:var(--bg); border-radius:2px; overflow:hidden; margin-top:8px; }
.progress-bar .fill { height:100%; background:var(--primary); transition:width 0.3s; }

/* ── Tools ── */
.tool-card {
  background:var(--surface2); border:1px solid var(--border); border-radius:var(--radius-sm);
  padding:14px; margin-bottom:10px; cursor:pointer; transition:var(--transition);
}
.tool-card:hover { border-color:var(--primary); }
.tool-card .tc-name { font-weight:600; color:var(--primary); font-size:14px; }
.tool-card .tc-cat { font-size:11px; color:var(--muted); margin-left:8px; }
.tool-card .tc-desc { font-size:12px; color:var(--muted); margin-top:6px; }
.tool-card .tc-params { font-size:11px; color:var(--muted); margin-top:6px; font-family:monospace; }

/* ── Conversations ── */
.convo-item {
  background:var(--surface2); border:1px solid var(--border); border-radius:var(--radius-sm);
  padding:14px; margin-bottom:10px; cursor:pointer; transition:var(--transition);
  display:flex; justify-content:space-between; align-items:center;
}
.convo-item:hover { border-color:var(--primary); }
.convo-item .ci-info { flex:1; }
.convo-item .ci-title { font-weight:600; font-size:14px; }
.convo-item .ci-meta { font-size:11px; color:var(--muted); margin-top:4px; }
.convo-item .ci-actions { display:flex; gap:6px; }

/* ── Modal ── */
.modal-overlay {
  position:fixed; inset:0; background:rgba(0,0,0,0.6); display:none;
  align-items:center; justify-content:center; z-index:1000;
}
.modal-overlay.show { display:flex; }
.modal {
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:24px; width:90%; max-width:560px; max-height:80vh; overflow-y:auto;
}
.modal-title { font-size:16px; font-weight:700; margin-bottom:16px; }

/* ── Toast ── */
.toast-container { position:fixed; top:60px; right:20px; z-index:2000; display:flex; flex-direction:column; gap:8px; }
.toast {
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-sm);
  padding:12px 18px; font-size:13px; min-width:260px; animation:slideIn 0.3s ease;
  border-left:3px solid var(--primary);
}
.toast.success { border-left-color:var(--success); }
.toast.error { border-left-color:var(--error); }
.toast.warn { border-left-color:var(--warning); }
@keyframes slideIn { from{transform:translateX(100%);opacity:0;} to{transform:translateX(0);opacity:1;} }

/* ── Markdown ── */
.md h1,.md h2,.md h3 { margin:12px 0 6px; color:var(--text); }
.md h1 { font-size:18px; } .md h2 { font-size:16px; } .md h3 { font-size:14px; }
.md p { margin:6px 0; }
.md code { background:var(--bg); padding:2px 6px; border-radius:3px; font-family:'Cascadia Code',monospace; font-size:12px; }
.md pre { background:var(--bg); padding:10px; border-radius:var(--radius-sm); overflow-x:auto; margin:8px 0; }
.md pre code { background:none; padding:0; }
.md ul,.md ol { margin:6px 0; padding-left:20px; }
.md a { color:var(--primary); }
.md table { border-collapse:collapse; margin:8px 0; }
.md th,.md td { border:1px solid var(--border); padding:6px 12px; font-size:12px; }
.md blockquote { border-left:3px solid var(--border); padding-left:12px; color:var(--muted); margin:8px 0; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width:8px; height:8px; }
::-webkit-scrollbar-track { background:var(--bg); }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:4px; }
::-webkit-scrollbar-thumb:hover { background:var(--muted); }
</style>
</head>
<body>

<!-- Top Bar -->
<div class="topbar">
  <div class="logo">⚡ AugLoop Copilot</div>
  <div class="spacer"></div>
  <div class="indicator" id="topTokenStatus">
    <div class="dot warn"></div>
    <span>Token: ...</span>
  </div>
  <div class="indicator" id="topServerStatus">
    <div class="dot ok"></div>
    <span>Server: Running</span>
  </div>
</div>

<div class="main">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="nav-item active" data-panel="dashboard" onclick="nav('dashboard')">
      <span class="icon">📊</span> Dashboard
    </div>
    <div class="nav-item" data-panel="chat" onclick="nav('chat')">
      <span class="icon">💬</span> Chat
    </div>
    <div class="nav-item" data-panel="token" onclick="nav('token')">
      <span class="icon">🔑</span> Token
    </div>
    <div class="nav-item" data-panel="tools" onclick="nav('tools')">
      <span class="icon">🔧</span> Tools
    </div>
    <div class="nav-item" data-panel="conversations" onclick="nav('conversations')">
      <span class="icon">📝</span> History
    </div>
    <div class="sep"></div>
    <div class="nav-item" onclick="window.open('/docs','_blank')">
      <span class="icon">📚</span> API Docs
    </div>
    <div class="version">v2.0.0</div>
  </div>

  <!-- Content -->
  <div class="content">

    <!-- Dashboard Panel -->
    <div class="panel active" id="panel-dashboard">
      <div class="panel-title">Dashboard</div>

      <div class="card">
        <div class="card-title">System Status</div>
        <div class="status-grid" id="statusGrid">
          <div class="status-item"><span class="label">Token</span><span class="value" id="sToken">Loading...</span></div>
          <div class="status-item"><span class="label">Token Source</span><span class="value" id="sSource">-</span></div>
          <div class="status-item"><span class="label">Expires In</span><span class="value" id="sExpires">-</span></div>
          <div class="status-item"><span class="label">Health Check</span><span class="value" id="sHealth">-</span></div>
          <div class="status-item"><span class="label">Tools</span><span class="value" id="sTools">-</span></div>
          <div class="status-item"><span class="label">Conversations</span><span class="value" id="sConvos">-</span></div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Token Strategies</div>
        <div class="status-grid" id="strategyGrid">
          <div class="status-item"><span class="label">MITM Proxy</span><span class="value" id="stratMitm">-</span></div>
<div class="status-item"><span class="label">Frida Hook</span><span class="value" id="stratFrida">-</span></div>
<div class="status-item"><span class="label">WAM (MSAL)</span><span class="value" id="stratWam">-</span></div>
<div class="status-item"><span class="label">Auto (WebSocket)</span><span class="value ok" id="stratAuto">Ready</span></div>
          <div class="status-item"><span class="label">HAR File</span><span class="value" id="stratHar">-</span></div>
        </div>
        <div class="btn-row">
          <button class="btn sm" onclick="nav('token')">Go to Token Manager →</button>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Quick Actions</div>
        <div class="btn-row">
          <button class="btn" onclick="nav('chat')">💬 Start Chat</button>
          <button class="btn secondary" onclick="doTokenRefresh()">🔄 Refresh Token</button>
          <button class="btn secondary" onclick="nav('tools')">🔧 Browse Tools</button>
        </div>
      </div>
    </div>

    <!-- Chat Panel -->
    <div class="panel" id="panel-chat">
      <div class="chat-container">
        <div class="panel-title" style="margin-bottom:0;">AI Chat</div>
        <div class="chat-options">
          <select id="modelSelect" onchange="saveModelChoice()"></select>
          <label><input type="checkbox" id="useTools" checked> Enable Tools</label>
          <label><input type="checkbox" id="useStream" checked> Stream Response</label>
          <label><input type="checkbox" id="useMarkdown" checked> Markdown</label>
          <button class="btn sm secondary" onclick="clearChat()">Clear</button>
        </div>
        <div class="chat-messages" id="chatMessages">
          <div class="chat-msg assistant">
            <div class="role">Assistant</div>
            Hello! I'm AugLoop Copilot. Ask me anything — I can use tools to help you.
          </div>
        </div>
        <div class="chat-input-area">
          <textarea id="chatInput" rows="1" placeholder="Type your message..." onkeydown="handleChatKey(event)" oninput="autoResize(this)"></textarea>
          <button class="btn" id="sendBtn" onclick="sendChat()">Send</button>
        </div>
      </div>
    </div>

    <!-- Token Panel -->
    <div class="panel" id="panel-token">
      <div class="panel-title">Token Management</div>

      <div class="card">
        <div class="card-title">Current Token Status</div>
        <div class="status-grid" id="tokenStatusGrid">Loading...</div>
        <div class="btn-row">
          <button class="btn sm" onclick="loadTokenStatus()">Refresh Status</button>
          <button class="btn sm secondary" onclick="doTokenRefresh()">Force Refresh</button>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Token Acquisition</div>

        <!-- Auto Token (Recommended!) -->
        <div class="token-method" id="autoMethod" style="border:2px solid #00d4ff;">
          <div class="tm-header">
            <span class="tm-title">🔑 Auto Token (Recommended! No packet sniffing needed)</span>
            <span class="tm-status idle" id="autoStatus">Ready</span>
          </div>
          <div class="tm-desc">Automatically acquire dual tokens (JWE + JWT). Scans Excel memory first, falls back to WebSocket Phase 1. If JWE expires, automatically validates and prompts.</div>
          <div class="btn-row">
            <button class="btn sm" id="autoBtn" onclick="autoAcquireToken()" style="background:#00d4ff;">🔑 Auto Acquire</button>
            <button class="btn sm" id="msalBtn" onclick="msalAcquireToken()" style="background:#7c3aed;">🔐 MSAL Auth</button>
          </div>
          <div class="tm-log" id="autoLog" style="display:none;"></div>
        </div>

        <!-- Frida -->
        <div class="token-method" id="fridaMethod">
          <div class="tm-header">
            <span class="tm-title">🔧 Frida Hook</span>
            <span class="tm-status idle" id="fridaStatus">Idle</span>
          </div>
          <div class="tm-desc">Hook Excel process to intercept AugLoop JWE + JWT tokens. Requires Excel running with Copilot.</div>
          <div class="btn-row">
            <button class="btn sm" id="fridaBtn" onclick="startFridaHunt()">Start Hunt</button>
            <input type="text" id="fridaTimeout" value="120" style="width:80px;" placeholder="Timeout (s)">
          </div>
          <div class="progress-bar" id="fridaProgress" style="display:none;"><div class="fill" style="width:0%;"></div></div>
          <div class="tm-log" id="fridaLog" style="display:none;"></div>
        </div>

        <!-- WAM -->
        <div class="token-method" id="wamMethod">
          <div class="tm-header">
            <span class="tm-title">🪪 WAM (Web Account Manager)</span>
            <span class="tm-status idle" id="wamStatus">Idle</span>
          </div>
          <div class="tm-desc">Silently acquire token via Windows WAM (MSAL.NET broker). Requires .NET SDK and MSA login.</div>
          <div class="btn-row">
            <button class="btn sm" id="wamBtn" onclick="startWamAcquire()">Acquire Token</button>
          </div>
          <div class="tm-log" id="wamLog" style="display:none;"></div>
        </div>

        <!-- HAR -->
        <div class="token-method" id="harMethod">
          <div class="tm-header">
            <span class="tm-title">📄 HAR Extraction</span>
            <span class="tm-status idle" id="harStatus">Idle</span>
          </div>
          <div class="tm-desc">Extract token from a HAR (HTTP Archive) file captured from browser DevTools.</div>
          <div class="field" style="margin-top:8px;">
            <input type="text" id="harPath" placeholder="Path to .har file...">
          </div>
          <div class="btn-row">
            <button class="btn sm" onclick="extractHar()">Extract</button>
          </div>
          <div class="tm-log" id="harLog" style="display:none;"></div>
        </div>

        <!-- Manual -->
        <div class="token-method" id="manualMethod">
          <div class="tm-header">
            <span class="tm-title">✏️ Manual Input</span>
            <span class="tm-status idle" id="manualStatus">Idle</span>
          </div>
          <div class="tm-desc">Manually paste JWE bearer token and/or JWT auth token.</div>
          <div class="field" style="margin-top:8px;">
            <label>JWE Bearer Token</label>
            <textarea id="manualBearer" rows="3" placeholder="eyJhbGciOiJkaXIi..."></textarea>
          </div>
          <div class="field">
            <label>JWT Auth Token (optional)</label>
            <textarea id="manualJwt" rows="3" placeholder="eyJhbGciOiJSUzI1Ni..."></textarea>
          </div>
          <div class="btn-row">
            <button class="btn sm" onclick="setManualToken()">Save Token</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Tools Panel -->
    <div class="panel" id="panel-tools">
      <div class="panel-title">Available Tools</div>
      <div class="card">
        <div class="btn-row" style="margin-top:0;">
          <button class="btn sm" onclick="loadTools()">Load Tools</button>
        </div>
        <div id="toolsList" style="margin-top:12px;"></div>
      </div>
    </div>

    <!-- Conversations Panel -->
    <div class="panel" id="panel-conversations">
      <div class="panel-title">Conversation History</div>
      <div class="card">
        <div class="btn-row" style="margin-top:0;">
          <button class="btn sm" onclick="loadConvos()">Load Conversations</button>
          <button class="btn sm secondary" onclick="nav('chat')">New Chat</button>
        </div>
        <div id="convosList" style="margin-top:12px;"></div>
      </div>
    </div>

  </div>
</div>

<!-- Modal -->
<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal" id="modalContent"></div>
</div>

<!-- Toast -->
<div class="toast-container" id="toastContainer"></div>

<script>
// ═══════════════════════════════════════════════════════════════════
// API Client
// ═══════════════════════════════════════════════════════════════════

async function api(method, path, body, timeoutMs) {
const opts = { method, headers: {} };
if (body) {
opts.headers['Content-Type'] = 'application/json';
opts.body = JSON.stringify(body);
}
if (timeoutMs) {
opts.signal = AbortSignal.timeout(timeoutMs);
}
const r = await fetch(path, opts);
let data;
try { data = await r.json(); } catch(e) { data = { raw: await r.text() }; }
if (!r.ok) throw new Error(data.detail || data.raw || 'HTTP ' + r.status);
return data;
}

// ═══════════════════════════════════════════════════════════════════
// Navigation
// ═══════════════════════════════════════════════════════════════════

function nav(panel) {
  document.querySelectorAll('.nav-item').forEach(function(n) {
    n.classList.toggle('active', n.dataset.panel === panel);
  });
  document.querySelectorAll('.panel').forEach(function(p) {
    p.classList.toggle('active', p.id === 'panel-' + panel);
  });
  if (panel === 'dashboard') loadStatus();
  if (panel === 'chat') loadModels();
  if (panel === 'token') loadTokenStatus();
  if (panel === 'tools') loadTools();
  if (panel === 'conversations') loadConvos();
}

// ═══════════════════════════════════════════════════════════════════
// Toast
// ═══════════════════════════════════════════════════════════════════

function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  document.getElementById('toastContainer').appendChild(el);
  setTimeout(function() { el.remove(); }, 4000);
}

// ═══════════════════════════════════════════════════════════════════
// Dashboard Status
// ═══════════════════════════════════════════════════════════════════

async function loadStatus() {
  try {
    const d = await api('GET', '/status');
    const expired = d.token_expired;
    const tokClass = !d.has_token ? 'err' : (expired ? 'warn' : 'ok');
    const tokText = d.has_token ? (expired ? 'EXPIRED' : 'Valid') : 'NOT SET';
    const hcClass = d.health_check === 'ok' ? 'ok' : 'err';

    document.getElementById('sToken').textContent = tokText;
    document.getElementById('sToken').className = 'value ' + tokClass;
    document.getElementById('sSource').textContent = d.token_source || '-';
    document.getElementById('sExpires').textContent = d.token_expires_in > 0 ? d.token_expires_in + 's' : (expired ? 'EXPIRED' : 'Unknown');
    document.getElementById('sExpires').className = 'value ' + tokClass;
    document.getElementById('sHealth').textContent = d.health_check;
    document.getElementById('sHealth').className = 'value ' + hcClass;
    document.getElementById('sTools').textContent = d.tools_count;
    document.getElementById('sConvos').textContent = d.conversations_count;

    // Top bar
    const topEl = document.getElementById('topTokenStatus');
    topEl.innerHTML = '<div class="dot ' + tokClass + '"></div><span>Token: ' + tokText + '</span>';

    // Token strategies
    try {
      const ts = await api('GET', '/token/status');
      const strats = ts.strategies || {};
      const sc = function(v) { return v ? 'ok' : 'err'; };
      const sv = function(v) { return v ? 'Available' : 'N/A'; };
      document.getElementById('stratMitm').textContent = sv(strats.mitm);
      document.getElementById('stratMitm').className = 'value ' + sc(strats.mitm);
      document.getElementById('stratFrida').textContent = sv(strats.frida);
      document.getElementById('stratFrida').className = 'value ' + sc(strats.frida);
      document.getElementById('stratWam').textContent = sv(strats.wam);
      document.getElementById('stratWam').className = 'value ' + sc(strats.wam);
      document.getElementById('stratHar').textContent = sv(strats.har);
      document.getElementById('stratHar').className = 'value ' + sc(strats.har);
    } catch(e) {}
  } catch(e) {
    toast('Failed to load status: ' + e, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════
// Token Management
// ═══════════════════════════════════════════════════════════════════

async function loadTokenStatus() {
  try {
    const d = await api('GET', '/token/status');
    const expired = d.is_expired;
    const tokClass = !d.has_token ? 'err' : (expired ? 'warn' : 'ok');
    const grid = document.getElementById('tokenStatusGrid');
    grid.innerHTML =
      '<div class="status-item"><span class="label">Has Token</span><span class="value ' + tokClass + '">' + (d.has_token ? 'Yes' : 'No') + '</span></div>' +
      '<div class="status-item"><span class="label">Source</span><span class="value">' + (d.source || '-') + '</span></div>' +
      '<div class="status-item"><span class="label">Token Preview</span><span class="value" style="font-size:11px;font-family:monospace;word-break:break-all;">' + (d.token_preview || '-') + '</span></div>' +
      '<div class="status-item"><span class="label">Token Length</span><span class="value">' + d.token_length + '</span></div>' +
      '<div class="status-item"><span class="label">Obtained At</span><span class="value" style="font-size:12px;">' + (d.obtained_at || '-') + '</span></div>' +
      '<div class="status-item"><span class="label">Expires At</span><span class="value" style="font-size:12px;">' + (d.expires_at || '-') + '</span></div>' +
      '<div class="status-item"><span class="label">Expires In</span><span class="value ' + tokClass + '">' + (d.expires_in_seconds > 0 ? d.expires_in_seconds + 's' : (expired ? 'EXPIRED' : 'Unknown')) + '</span></div>' +
      '<div class="status-item"><span class="label">Auto Refresh</span><span class="value">' + (d.auto_refresh ? 'Active' : 'Off') + '</span></div>';
  } catch(e) {
    toast('Failed: ' + e, 'error');
  }
}

async function doTokenRefresh() {
  try {
    toast('Refreshing token...');
    const d = await api('POST', '/token/refresh', { force: true });
    if (d.status === 'ok') {
      toast('Token refreshed via ' + d.source, 'success');
    } else {
      toast('Refresh failed', 'error');
    }
    loadStatus();
    loadTokenStatus();
  } catch(e) {
    toast('Refresh error: ' + e, 'error');
  }
}

// ── Auto Token (Recommended!) ──
async function autoAcquireToken() {
  const btn = document.getElementById('autoBtn');
  const logEl = document.getElementById('autoLog');
  const statusEl = document.getElementById('autoStatus');

  btn.disabled = true;
  btn.textContent = 'Acquiring...';
  statusEl.textContent = 'Working';
  statusEl.className = 'tm-status running';
  logEl.style.display = 'block';
  logEl.textContent = 'Acquiring Token...\n';

  try {
    const d = await api('POST', '/token/auto', {});

    if (d.status === 'ok') {
      logEl.textContent += '[OK] ' + (d.message || 'Acquisition successful') + '\n';
      if (d.jwe_validated) {
        logEl.textContent += '  ✅ JWE Token: Verified\n';
      } else if (d.jwe_validated === false) {
        logEl.textContent += '  ⚠️ JWE Token: Unverified (may be expired)\n';
      }
      if (d.auth_token) {
        logEl.textContent += '  ✅ JWT authToken: ' + (d.auth_token_length || '?') + ' chars\n';
      }
      if (d.expires_in_hours) {
        logEl.textContent += '  Validity: ' + d.expires_in_hours + ' hours\n';
      }
      statusEl.textContent = 'Success';
      statusEl.className = 'tm-status success';
      btn.textContent = '✅ Acquired';
      toast(d.message || 'Token acquired successfully', 'success');

      loadTokenStatus();
      loadStatus();

      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = '🔑 Auto Acquire';
      }, 3000);
    } else if (d.status === 'partial') {
      logEl.textContent += '[⚠️] ' + (d.message || 'Partial success') + '\n';
      logEl.textContent += '  JWT authToken acquired, but JWE is expired.\n';
      logEl.textContent += '  Please start Excel and reacquire, or click "MSAL Auth".\n';
      statusEl.textContent = 'Partial';
      statusEl.className = 'tm-status error';
      btn.textContent = '🔑 Auto Acquire';
      toast('JWE Token expired, please start Excel or use MSAL', 'warn');
    } else {
      logEl.textContent += '[ERROR] ' + (d.error || d.message || 'Unknown error') + '\n';
      statusEl.textContent = 'Failed';
      statusEl.className = 'tm-status error';
      btn.textContent = '🔑 Auto Acquire';
      toast('Auto acquire failed: ' + (d.error || ''), 'error');
    }
  } catch (e) {
    logEl.textContent += '[ERROR] ' + e + '\n';
    statusEl.textContent = 'Error';
    statusEl.className = 'tm-status error';
    btn.textContent = '🔑 Auto Acquire';
    toast('Auto acquire error: ' + e, 'error');
  }
  btn.disabled = false;
}

// ── MSAL Auth ──
async function msalAcquireToken() {
  const btn = document.getElementById('msalBtn');
  const logEl = document.getElementById('autoLog');
  const statusEl = document.getElementById('autoStatus');

  btn.disabled = true;
  btn.textContent = 'Authenticating...';
  statusEl.textContent = 'Working';
  statusEl.className = 'tm-status running';
  logEl.style.display = 'block';
  logEl.textContent = 'MSAL Authenticating...\n';
  logEl.textContent += '⚠️ For first-time use, check the server terminal to complete device authentication\n';

  try {
    const d = await api('POST', '/token/msal', {}, 120000);

    if (d.status === 'ok') {
      logEl.textContent += '[OK] ' + (d.message || 'MSAL authentication successful') + '\n';
      if (d.jwe_validated) {
        logEl.textContent += '  ✅ JWE Token: Verified\n';
      }
      logEl.textContent += '  JWE length: ' + (d.jwe_token_length || '?') + ' chars\n';
      statusEl.textContent = 'Success';
      statusEl.className = 'tm-status success';
      btn.textContent = '✅ MSAL Success';
      toast('MSAL authentication successful!', 'success');
      loadTokenStatus();
      loadStatus();
    } else {
      logEl.textContent += '[ERROR] ' + (d.error || d.message || 'Unknown error') + '\n';
      if (d.message) logEl.textContent += '  ' + d.message + '\n';
      statusEl.textContent = 'Failed';
      statusEl.className = 'tm-status error';
      toast('MSAL authentication failed: ' + (d.error || ''), 'error');
    }
  } catch (e) {
    logEl.textContent += '[ERROR] ' + e + '\n';
    statusEl.textContent = 'Error';
    statusEl.className = 'tm-status error';
    toast('MSAL error: ' + e, 'error');
  }
  btn.disabled = false;
  btn.textContent = '🔐 MSAL Auth';
}

// ── Frida ──
let fridaPolling = false;

async function startFridaHunt() {
  const timeout = parseInt(document.getElementById('fridaTimeout').value) || 120;
  const btn = document.getElementById('fridaBtn');
  btn.disabled = true;
  btn.textContent = 'Running...';

  setTmStatus('frida', 'running', 'Hunting...');
  const logEl = document.getElementById('fridaLog');
  const progEl = document.getElementById('fridaProgress');
  logEl.style.display = 'block';
  progEl.style.display = 'block';
  logEl.textContent = 'Starting Frida hook...\n';

  try {
    const d = await api('POST', '/token/frida-hunt', { timeout: timeout });
    logEl.textContent += 'Frida task started (task_id=' + d.task_id + ')\n';

    // Poll for status
    fridaPolling = true;
    const startTime = Date.now();
    const poll = async function() {
      if (!fridaPolling) return;
      try {
        const s = await api('GET', '/token/frida-status');
        const elapsed = (Date.now() - startTime) / 1000;
        const pct = Math.min(100, (elapsed / timeout) * 100);
        progEl.querySelector('.fill').style.width = pct + '%';

        if (s.logs && s.logs.length > 0) {
          logEl.textContent = s.logs.join('\n') + '\n';
          logEl.scrollTop = logEl.scrollHeight;
        }

        if (s.status === 'success') {
          fridaPolling = false;
          btn.disabled = false;
          btn.textContent = 'Start Hunt';
          setTmStatus('frida', 'success', 'Success!');
          toast('Token captured via Frida!', 'success');
          loadStatus();
          loadTokenStatus();
          return;
        }
        if (s.status === 'error' || s.status === 'timeout') {
          fridaPolling = false;
          btn.disabled = false;
          btn.textContent = 'Start Hunt';
          setTmStatus('frida', 'error', s.status === 'timeout' ? 'Timeout' : 'Error');
          toast('Frida hunt failed: ' + (s.error || s.status), 'error');
          return;
        }
        setTimeout(poll, 2000);
      } catch(e) {
        fridaPolling = false;
        btn.disabled = false;
        btn.textContent = 'Start Hunt';
        setTmStatus('frida', 'error', 'Error');
        toast('Frida poll error: ' + e, 'error');
      }
    };
    setTimeout(poll, 1000);
  } catch(e) {
    btn.disabled = false;
    btn.textContent = 'Start Hunt';
    setTmStatus('frida', 'error', 'Error');
    logEl.textContent += 'Error: ' + e + '\n';
    toast('Frida error: ' + e, 'error');
  }
}

// ── WAM ──
let wamPolling = false;

async function startWamAcquire() {
  const btn = document.getElementById('wamBtn');
  btn.disabled = true;
  btn.textContent = 'Running...';

  setTmStatus('wam', 'running', 'Acquiring...');
  const logEl = document.getElementById('wamLog');
  logEl.style.display = 'block';
  logEl.textContent = 'Starting WAM token acquisition...\n';

  try {
    const d = await api('POST', '/token/wam-acquire', {});
    logEl.textContent += 'WAM task started (task_id=' + d.task_id + ')\n';

    wamPolling = true;
    const poll = async function() {
      if (!wamPolling) return;
      try {
        const s = await api('GET', '/token/wam-status');
        if (s.logs && s.logs.length > 0) {
          logEl.textContent = s.logs.join('\n') + '\n';
          logEl.scrollTop = logEl.scrollHeight;
        }
        if (s.status === 'success') {
          wamPolling = false;
          btn.disabled = false;
          btn.textContent = 'Acquire Token';
          setTmStatus('wam', 'success', 'Success!');
          toast('Token acquired via WAM!', 'success');
          loadStatus();
          loadTokenStatus();
          return;
        }
        if (s.status === 'error') {
          wamPolling = false;
          btn.disabled = false;
          btn.textContent = 'Acquire Token';
          setTmStatus('wam', 'error', 'Error');
          toast('WAM failed: ' + (s.error || ''), 'error');
          return;
        }
        setTimeout(poll, 2000);
      } catch(e) {
        wamPolling = false;
        btn.disabled = false;
        btn.textContent = 'Acquire Token';
        setTmStatus('wam', 'error', 'Error');
        toast('WAM poll error: ' + e, 'error');
      }
    };
    setTimeout(poll, 1000);
  } catch(e) {
    btn.disabled = false;
    btn.textContent = 'Acquire Token';
    setTmStatus('wam', 'error', 'Error');
    logEl.textContent += 'Error: ' + e + '\n';
    toast('WAM error: ' + e, 'error');
  }
}

// ── HAR ──
async function extractHar() {
  const path = document.getElementById('harPath').value.trim();
  if (!path) { toast('Please enter HAR file path', 'warn'); return; }

  setTmStatus('har', 'running', 'Extracting...');
  const logEl = document.getElementById('harLog');
  logEl.style.display = 'block';
  logEl.textContent = 'Extracting from: ' + path + '\n';

  try {
    const d = await api('POST', '/token/extract-har', { har_file: path });
    logEl.textContent += 'Result: ' + JSON.stringify(d, null, 2) + '\n';
    if (d.augloop_token) {
      setTmStatus('har', 'success', 'Success!');
      toast('Token extracted from HAR!', 'success');
      loadStatus();
      loadTokenStatus();
    } else {
      setTmStatus('har', 'error', 'No token found');
      toast('No token found in HAR', 'warn');
    }
  } catch(e) {
    setTmStatus('har', 'error', 'Error');
    logEl.textContent += 'Error: ' + e + '\n';
    toast('HAR extraction failed: ' + e, 'error');
  }
}

// ── Manual ──
async function setManualToken() {
  const bearer = document.getElementById('manualBearer').value.trim();
  const jwt = document.getElementById('manualJwt').value.trim();
  if (!bearer && !jwt) { toast('Please enter at least one token', 'warn'); return; }

  setTmStatus('manual', 'running', 'Saving...');
  try {
    const d = await api('POST', '/token/manual', { bearer_token: bearer, auth_token: jwt });
    setTmStatus('manual', 'success', 'Saved!');
    toast('Token saved!', 'success');
    loadStatus();
    loadTokenStatus();
  } catch(e) {
    setTmStatus('manual', 'error', 'Error');
    toast('Save failed: ' + e, 'error');
  }
}

function setTmStatus(method, status, text) {
  const el = document.getElementById(method + 'Status');
  el.className = 'tm-status ' + status;
  el.textContent = text;
}

// ═══════════════════════════════════════════════════════════════════
// Chat
// ═══════════════════════════════════════════════════════════════════

let chatHistory = [];
let chatStreaming = false;

function handleChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(120, el.scrollHeight) + 'px';
}

function clearChat() {
  chatHistory = [];
  document.getElementById('chatMessages').innerHTML =
    '<div class="chat-msg assistant"><div class="role">Assistant</div>Conversation cleared. What can I help you with?</div>';
}

function addMsg(role, text, isToolCall, toolResult) {
  const container = document.getElementById('chatMessages');
  const el = document.createElement('div');
  el.className = 'chat-msg ' + role;

  let html = '<div class="role">' + role + '</div>';

  if (isToolCall) {
    html += '<div class="tool-call-box">' +
      '<div class="tc-header" onclick="this.nextElementSibling.classList.toggle(\'collapsed\')">' +
      '🔧 Tool Call: ' + text + '</div>' +
      '<div class="tc-result">' + escapeHtml(toolResult || '') + '</div>' +
      '</div>';
  } else {
    if (document.getElementById('useMarkdown').checked && role === 'assistant') {
      html += '<div class="md">' + renderMd(text) + '</div>';
    } else {
      html += escapeHtml(text);
    }
  }

  el.innerHTML = html;
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return el;
}

function addTypingIndicator() {
  const container = document.getElementById('chatMessages');
  const el = document.createElement('div');
  el.className = 'chat-msg assistant';
  el.id = 'typingIndicator';
  el.innerHTML = '<div class="role">Assistant</div><div class="typing-indicator"><span></span><span></span><span></span></div>';
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
}

function removeTypingIndicator() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

async function sendChat() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text || chatStreaming) return;

  input.value = '';
  input.style.height = 'auto';

  addMsg('user', text);
  chatHistory.push({ role: 'user', content: text });

  const useTools = document.getElementById('useTools').checked;
  const useStream = document.getElementById('useStream').checked;

  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  chatStreaming = true;

  let toolsList = null;
  if (useTools) {
    try {
      const td = await api('GET', '/v1/tools');
      toolsList = td.tools.map(function(t) {
        return { type: 'function', function: { name: t.name, description: t.description, parameters: t.parameters } };
      });
    } catch(e) {
      toast('Failed to load tools: ' + e, 'warn');
    }
  }

  const body = {
    model: document.getElementById('modelSelect') ? document.getElementById('modelSelect').value : 'claude-opus-4.8',
    messages: chatHistory.map(function(m) { return { role: m.role, content: m.content }; }),
    stream: useStream,
  };
  if (toolsList) body.tools = toolsList;

  if (useStream) {
    await streamChat(body);
  } else {
    await nonStreamChat(body);
  }

  btn.disabled = false;
  chatStreaming = false;
}

async function nonStreamChat(body) {
  addTypingIndicator();
  try {
    const r = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    removeTypingIndicator();
    const d = await r.json();
    if (!r.ok) {
      addMsg('error', 'Error: ' + (d.detail || JSON.stringify(d)));
      return;
    }
    const content = d.choices[0].message.content || '';
    addMsg('assistant', content);
    chatHistory.push({ role: 'assistant', content: content });

    // Show tool results
    if (d.tool_results && d.tool_results.length) {
      d.tool_results.forEach(function(tc) {
        addMsg('assistant', tc.name, true, JSON.stringify(tc.result, null, 2));
      });
    }
  } catch(e) {
    removeTypingIndicator();
    addMsg('error', 'Failed: ' + e);
  }
}

async function streamChat(body) {
  addTypingIndicator();
  try {
    const r = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    removeTypingIndicator();

    if (!r.ok) {
      const d = await r.json();
      addMsg('error', 'Error: ' + (d.detail || 'HTTP ' + r.status));
      return;
    }

    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let fullText = '';
    let msgEl = null;

    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buf += dec.decode(chunk.value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (!line.startsWith('data: ')) continue;
        if (line.includes('[DONE]')) continue;
        try {
          const d = JSON.parse(line.slice(6));
          const delta = d.choices[0].delta;
          if (delta.content) {
            if (!msgEl) {
              msgEl = addMsg('assistant', delta.content);
              fullText = delta.content;
            } else {
              fullText += delta.content;
              const mdOn = document.getElementById('useMarkdown').checked;
              const contentEl = msgEl.querySelector('.md') || msgEl;
              if (mdOn) {
                const mdDiv = msgEl.querySelector('.md');
                if (mdDiv) {
                  mdDiv.innerHTML = renderMd(fullText);
                } else {
                  msgEl.innerHTML = '<div class="role">Assistant</div><div class="md">' + renderMd(fullText) + '</div>';
                }
              } else {
                contentEl.innerHTML = '<div class="role">Assistant</div>' + escapeHtml(fullText);
              }
            }
            document.getElementById('chatMessages').scrollTop = 999999;
          }
        } catch(e2) {}
      }
    }

    if (fullText) {
      chatHistory.push({ role: 'assistant', content: fullText });
    }
  } catch(e) {
    removeTypingIndicator();
    addMsg('error', 'Stream failed: ' + e);
  }
}

// ═══════════════════════════════════════════════════════════════════
// Tools
// ═══════════════════════════════════════════════════════════════════

async function loadModels() {
  try {
    const d = await api('GET', '/v1/models');
    const sel = document.getElementById('modelSelect');
    if (!sel) return;
    const saved = localStorage.getItem('selectedModel') || 'claude-opus-4.8';
    sel.innerHTML = d.data.map(function(m) {
      const owner = m.owned_by ? ' (' + m.owned_by + ')' : '';
      return '<option value="' + escapeHtml(m.id) + '"' + (m.id === saved ? ' selected' : '') + '>' +
        escapeHtml(m.id) + owner + '</option>';
    }).join('');
  } catch(e) {
    console.error('Failed to load models:', e);
  }
}

function saveModelChoice() {
  const sel = document.getElementById('modelSelect');
  if (sel) localStorage.setItem('selectedModel', sel.value);
}

async function loadTools() {
  try {
    const d = await api('GET', '/v1/tools');
    const el = document.getElementById('toolsList');
    if (!d.tools || d.tools.length === 0) {
      el.innerHTML = '<div style="color:var(--muted);">No tools available.</div>';
      return;
    }
    el.innerHTML = d.tools.map(function(t, i) {
      const params = t.parameters ? JSON.stringify(t.parameters.properties || {}, null, 2) : '{}';
      return '<div class="tool-card" onclick="showToolDetail(' + i + ')">' +
        '<span class="tc-name">' + escapeHtml(t.name) + '</span>' +
        '<span class="tc-cat">(' + escapeHtml(t.category) + ')</span>' +
        '<div class="tc-desc">' + escapeHtml(t.description) + '</div>' +
        '<div class="tc-params">' + escapeHtml(params.substring(0, 200)) + (params.length > 200 ? '...' : '') + '</div>' +
        '</div>';
    }).join('');
    el._toolsData = d.tools;
  } catch(e) {
    toast('Failed to load tools: ' + e, 'error');
  }
}

function showToolDetail(index) {
  const tools = document.getElementById('toolsList')._toolsData;
  if (!tools || !tools[index]) return;
  const t = tools[index];
  const params = t.parameters || {};
  const props = params.properties || {};

  let paramFields = '';
  if (params.required && params.required.length) {
    paramFields += '<div style="margin-bottom:8px;font-size:12px;color:var(--warning);">Required: ' + params.required.join(', ') + '</div>';
  }
  Object.keys(props).forEach(function(name) {
    const p = props[name];
    const isReq = (params.required || []).includes(name);
    paramFields += '<div class="field"><label>' + name + (isReq ? ' *' : '') + ' (' + (p.type || 'any') + ')</label>' +
      '<input type="text" data-param="' + name + '" placeholder="' + (p.description || '') + '"></div>';
  });

  document.getElementById('modalContent').innerHTML =
    '<div class="modal-title">🔧 ' + escapeHtml(t.name) + '</div>' +
    '<div style="margin-bottom:12px;color:var(--muted);">' + escapeHtml(t.description) + '</div>' +
    '<div style="margin-bottom:12px;"><strong>Category:</strong> ' + escapeHtml(t.category) + '</div>' +
    '<div style="margin-bottom:8px;font-weight:600;">Parameters:</div>' +
    (paramFields || '<div style="color:var(--muted);">No parameters</div>') +
    '<div class="btn-row">' +
    '<button class="btn sm" onclick="executeToolFromModal(' + index + ')">Execute</button>' +
    '<button class="btn sm secondary" onclick="closeModal()">Close</button>' +
    '</div>' +
    '<div id="toolResult" style="margin-top:12px;"></div>';
  document.getElementById('modalOverlay').classList.add('show');
}

async function executeToolFromModal(index) {
  const tools = document.getElementById('toolsList')._toolsData;
  if (!tools || !tools[index]) return;
  const t = tools[index];
  const inputs = document.querySelectorAll('#modalContent input[data-param]');
  const args = {};
  inputs.forEach(function(inp) {
    const val = inp.value.trim();
    if (val) {
      try { args[inp.dataset.param] = JSON.parse(val); }
      catch(e) { args[inp.dataset.param] = val; }
    }
  });

  const resultEl = document.getElementById('toolResult');
  resultEl.innerHTML = '<div style="color:var(--muted);">Executing...</div>';

  try {
    const d = await api('POST', '/v1/tools/' + encodeURIComponent(t.name) + '/execute', { arguments: args });
    resultEl.innerHTML = '<div style="margin-top:8px;padding:10px;background:var(--bg);border-radius:6px;font-family:monospace;font-size:12px;white-space:pre-wrap;max-height:300px;overflow-y:auto;">' +
      escapeHtml(typeof d.content === 'string' ? d.content : JSON.stringify(d.content, null, 2)) +
      (d.is_error ? '\n\n[ERROR]' : '') + '</div>';
  } catch(e) {
    resultEl.innerHTML = '<div style="color:var(--error);">Error: ' + escapeHtml(e.message) + '</div>';
  }
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('show');
}

// ═══════════════════════════════════════════════════════════════════
// Conversations
// ═══════════════════════════════════════════════════════════════════

async function loadConvos() {
  try {
    const d = await api('GET', '/v1/conversations?limit=50');
    const el = document.getElementById('convosList');
    if (!d.conversations || d.conversations.length === 0) {
      el.innerHTML = '<div style="color:var(--muted);">No conversations yet.</div>';
      return;
    }
    el.innerHTML = d.conversations.map(function(c) {
      return '<div class="convo-item">' +
        '<div class="ci-info">' +
        '<div class="ci-title">' + escapeHtml(c.title || 'Untitled') + '</div>' +
        '<div class="ci-meta">' + c.message_count + ' messages | ' + new Date(c.updated_at * 1000).toLocaleString() + '</div>' +
        '</div>' +
        '<div class="ci-actions">' +
        '<button class="btn sm secondary" onclick="viewConvo(\'' + c.id + '\')">View</button>' +
        '<button class="btn sm danger" onclick="deleteConvo(\'' + c.id + '\')">Delete</button>' +
        '</div></div>';
    }).join('');
  } catch(e) {
    toast('Failed to load conversations: ' + e, 'error');
  }
}

async function viewConvo(id) {
  try {
    const d = await api('GET', '/v1/conversations/' + id + '/messages');
    const msgs = d.messages || [];
    document.getElementById('modalContent').innerHTML =
      '<div class="modal-title">Conversation Messages</div>' +
      '<div style="max-height:60vh;overflow-y:auto;">' +
      msgs.map(function(m) {
        return '<div class="chat-msg ' + m.role + '" style="max-width:100%;margin-bottom:8px;">' +
          '<div class="role">' + m.role + '</div>' +
          escapeHtml(m.content || '') + '</div>';
      }).join('') +
      '</div>' +
      '<div class="btn-row"><button class="btn sm secondary" onclick="closeModal()">Close</button></div>';
    document.getElementById('modalOverlay').classList.add('show');
  } catch(e) {
    toast('Failed: ' + e, 'error');
  }
}

async function deleteConvo(id) {
  if (!confirm('Delete this conversation?')) return;
  try {
    await api('DELETE', '/v1/conversations/' + id);
    toast('Conversation deleted', 'success');
    loadConvos();
  } catch(e) {
    toast('Delete failed: ' + e, 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════

function escapeHtml(text) {
  if (!text) return '';
  return String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}

function renderMd(text) {
  if (!text) return '';
  let html = escapeHtml(text);
  // Code blocks
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(m, lang, code) {
    return '<pre><code>' + code + '</code></pre>';
  });
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Bold/Italic
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  // Lists
  html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
  html = html.replace(/^[\-\*]\s+(.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
  // Tables (simple)
  html = html.replace(/^\|(.+)\|$/gm, function(m, content) {
    const cells = content.split('|').map(function(c) { return c.trim(); });
    return '<tr>' + cells.map(function(c) { return '<td>' + c + '</td>'; }).join('') + '</tr>';
  });
  html = html.replace(/(<tr>[\s\S]*?<\/tr>)/g, '<table>$1</table>');
  // Paragraphs
  html = html.replace(/\n\n/g, '</p><p>');
  html = '<p>' + html + '</p>';
  // Clean up
  html = html.replace(/<p>\s*<(h\d|pre|ul|table)/g, '<$1');
  html = html.replace(/<\/(h\d|pre|ul|table)>\s*<\/p>/g, '</$1>');
  return html;
}

// ═══════════════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════════════

loadStatus();
loadModels();
setInterval(loadStatus, 30000);
</script>
</body>
</html>"""


def launch_desktop(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True):
    """Launch Desktop UI

    1. Start FastAPI server in the background
    2. Open browser to access the UI
    """
    import subprocess
    import sys as _sys

    server_dir = Path(__file__).parent
    url = f"http://{host}:{port}"

    if open_browser:
        # Delay opening browser until server starts
        def _open():
            time.sleep(2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    # Start server
    print(f"[Desktop UI] Starting server at {url}")
    print(f"[Desktop UI] Server directory: {server_dir}")

    subprocess.run(
        [_sys.executable, "server.py"],
        cwd=str(server_dir),
    )


if __name__ == "__main__":
    launch_desktop()
