#!/usr/bin/env python3
"""
augloop_client.py - AugLoop (Microsoft 365 Copilot) API 客户端

负责:
  1. 管理 Bearer Token 认证
  2. WebSocket 会话建立
  3. HealthCheck
  4. 发送 workflow 请求 (提示词建议 / AI 对话)
  5. 解析响应
"""

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx

from prompt_stripper import PromptStripper

logger = logging.getLogger("augloop")


class AugLoopClient:
    """AugLoop API 客户端"""

    def __init__(self, config: dict):
        self.config = config
        aug = config.get("augloop", {})
        self.base_url = aug.get("base_url", "https://augloop.svc.cloud.microsoft")
        self.workflow = aug.get("workflow", "OfficeCopilotOrchestrationWorkflow")
        self.token = aug.get("bearer_token", "")
        self.x_client_metadata = aug.get("x_client_metadata", "")
        self.session_id = aug.get("x_office_session_id", str(uuid.uuid4()).upper())
        self.license_type = aug.get("copilot_license_type", "ConsumerPro")
        self.prompts_cfg = aug.get("prompts", {})
        self.chat_cfg = aug.get("chat", {})
        self.fallback_types = aug.get("chat_fallback_prompt_types", [])

        # 运行时状态
        self._client: httpx.AsyncClient | None = None
        self._session_key: str | None = None

        # 提示词清理器（自动删除硬编码的内置提示词）
        self.strip_prompts = aug.get("strip_prompts", True)
        self.prompt_stripper = PromptStripper() if self.strip_prompts else None

    # ── 属性 ───────────────────────────────────────────────────────────────

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    @property
    def token_preview(self) -> str:
        if not self.token:
            return "(空)"
        return self.token[:40] + "..." if len(self.token) > 40 else self.token

    # ── 内部方法 ───────────────────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        """构建发往 AugLoop 的请求头 (从 HAR #22 提取)"""
        headers = {
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": (
                "Microsoft Office/16.0 (Windows NT 10.0; "
                "Microsoft Excel 16.0.20228; Pro)"
            ),
            "X-IDCRL_ACCEPTED": "t",
            "X-Office-Version": "16.0.20228",
            "X-Office-Application": "1",
            "X-Office-Platform": "Win32",
            "X-Office-AudienceGroup": "Insiders",
            "X-Office-SessionId": self.session_id,
        }
        if self.x_client_metadata:
            headers["x-client-metadata"] = self.x_client_metadata
        # 🔑 x-session-key 是 Workflow API 的关键头
        # 没有 it 会返回 400 Bad Request
        if self._session_key:
            headers["x-session-key"] = self._session_key
        return headers

    def _build_workflow_url(self, output_type: str) -> str:
        """构建 workflow POST URL"""
        return (
            f"{self.base_url}/workflows/{self.workflow}"
            f"?includeMetadata=true"
            f"&tryResolveUpstreamDependencies=true"
            f"&outputTypes={output_type}"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                verify=True,
            )
        return self._client

    # ── 公开方法 ──────────────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """
        发送 HealthCheck 请求 (对应 HAR #2)
        POST {base_url}/  -> [{"status":"OK"}]
        """
        client = await self._get_client()
        body = {
            "payload": {},
            "payloadSchema": {
                "category": 1,
                "schema": {"name": "HealthCheckRequest"},
            },
            "requestedSchema": {
                "category": 1,
                "schema": {"name": "HealthCheckResponse"},
            },
            "clientMetadata": {
                "appName": "Excel",
                "appPlatform": "Win32",
                "appVersion": "16.0.20228.20102",
                "uiLanguage": "zh-CN",
                "releaseAudienceGroup": "Insiders",
                "releaseChannel": "CC",
                "releaseFork": "2606-Jun",
                "sessionId": str(uuid.uuid4()),
                "flights": "",
                "privateMode": False,
                "disabledServiceGroups": [],
                "userSystemTimezone": "Asia/Shanghai",
                "isClientTelemetrySampled": False,
                "runtimeVersion": "2.37.2387",
                "docSessionId": str(uuid.uuid4()).upper(),
            },
        }
        resp = await client.post(
            f"{self.base_url}/",
            json=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Microsoft Office/16.0 (Windows NT 10.0; Microsoft Excel 16.0.20228; Pro)",
            },
        )
        logger.info("HealthCheck -> %d", resp.status_code)
        return {"status_code": resp.status_code, "data": resp.json()}

    async def get_prompts(self, document_state: str = "Blank") -> dict[str, Any]:
        """
        获取 Copilot 建议提示词 (对应 HAR #22, promptType=DynamicActionButton)
        返回完整响应 JSON (含 categoryPromptsList)
        """
        if not self.has_token:
            return {"error": "Bearer Token 为空, 请先运行 har_extractor.py 或手动配置"}

        client = await self._get_client()
        cfg = self.prompts_cfg
        request_id = str(uuid.uuid4()).upper()
        body = {
            "promptType": cfg.get("prompt_type", "DynamicActionButton"),
            "suggestionContext": {"documentState": document_state},
            "copilotLicenseType": self.license_type,
            "requestId": request_id,
            "H_": {
                "T_": cfg.get("signal_type", "AugLoop_OfficeCopilotOrchestration_CopilotPromptsSignal"),
                "B_": cfg.get("signal_base", ["AugLoop_Signals_Signal"]),
            },
        }

        url = self._build_workflow_url(
            cfg.get("output_type", "AugLoop_OfficeCopilotOrchestration_CopilotPrompts")
        )
        headers = self._build_headers()

        logger.info("get_prompts -> POST %s", url[:70])
        resp = await client.post(url, json=body, headers=headers)

        if resp.status_code != 200:
            return {
                "error": f"AugLoop 返回 {resp.status_code}",
                "detail": resp.text[:500],
            }

        data = resp.json()
        # 提取 x-session-key (后续 WebSocket 可能需要)
        self._session_key = resp.headers.get("x-session-key")

        # 整理提示词列表
        prompts_list = []
        for cat in data.get("categoryPromptsList", []):
            for p in cat.get("prompts", []):
                prompts_list.append({
                    "text": p.get("promptText", ""),
                    "command": p.get("promptCommand", ""),
                    "category": cat.get("category", ""),
                    "autoExecute": p.get("autoExecute", False),
                })

        return {
            "status": "ok",
            "prompts": prompts_list,
        }

    async def send_chat(
        self,
        message: str,
        document_state: str = "Existing",
        conversation_id: str | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """
        发送 AI 对话请求

        参数:
            message: 用户输入的消息文本
            document_state: 文档状态 (Blank / Existing / FormulaSelected ...)
            conversation_id: 可选对话 ID (用于多轮对话)
            system_prompt: 可选系统提示词 (用于 tool calling 等)
        """
        if not self.has_token:
            return {"error": "Bearer Token 为空, 请先运行 har_extractor.py 或手动配置"}

        cfg = self.chat_cfg
        msg_field = cfg.get("message_field", "promptText")

        # 构建请求体
        def _build_body(prompt_type: str) -> dict:
            body = {
                "promptType": prompt_type,
                msg_field: message,
                "copilotLicenseType": self.license_type,
                "requestId": str(uuid.uuid4()).upper(),
                "H_": {
                    "T_": cfg.get(
                        "signal_type",
                        "AugLoop_OfficeCopilotOrchestration_CopilotChatSignal",
                    ),
                    "B_": cfg.get("signal_base", ["AugLoop_Signals_Signal"]),
                },
            }
            # 可选字段
            body.setdefault("suggestionContext", {"documentState": document_state})
            if conversation_id:
                body["conversationId"] = conversation_id
            # 注入系统提示词 (用于 tool calling)
            if system_prompt:
                body["systemPrompt"] = system_prompt
            return body

        # 尝试主 prompt_type
        all_types = [cfg.get("prompt_type", "UserPrompt")] + self.fallback_types
        # 去重
        seen = set()
        unique_types = [t for t in all_types if not (t in seen or seen.add(t))]

        client = await self._get_client()
        url = self._build_workflow_url(
            cfg.get("output_type", "AugLoop_OfficeCopilotOrchestration_CopilotChatResponse")
        )
        headers = self._build_headers()

        last_error = None
        for ptype in unique_types:
            body = _build_body(ptype)

            # ✨ 自动删除硬编码的内置提示词 (但保留我们主动设置的 system_prompt)
            _saved_prompts = {}
            for _k in ("systemPrompt", "systemPromptText", "systemPromptType"):
                if _k in body:
                    _saved_prompts[_k] = body[_k]
            if self.prompt_stripper:
                body = self.prompt_stripper.strip_dict(body)
                logger.debug("已删除硬编码的提示词字段")
            body.update(_saved_prompts)  # 恢复我们设置的提示词

            logger.info("send_chat (promptType=%s) -> POST %s", ptype, url[:70])
            try:
                resp = await client.post(url, json=body, headers=headers)

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"response": resp.text[:2000]}
                    self._session_key = resp.headers.get("x-session-key")
                    return {
                        "status": "ok",
                        "prompt_type": ptype,
                        "response_text": self._extract_response_text(data),
                    }
                elif resp.status_code == 204:
                    # 204 No Content = 聊天信号被接受，响应通过 WebSocket 推送
                    self._session_key = resp.headers.get("x-session-key")
                    logger.info("[HTTP] 204 No Content - 聊天信号已被接受 (响应通过 WebSocket 推送)")
                    return {
                        "status": "accepted",
                        "prompt_type": ptype,
                        "response_text": "",
                    }
                elif resp.status_code == 401:
                    return {
                        "error": "Token 已过期 (401), 请重新抓包提取新 Token",
                        "detail": resp.text[:300],
                    }
                else:
                    last_error = {
                        "prompt_type": ptype,
                        "status_code": resp.status_code,
                        "detail": resp.text[:300],
                    }
                    logger.warning(
                        "promptType=%s -> %d: %s",
                        ptype,
                        resp.status_code,
                        resp.text[:100],
                    )
                    if resp.status_code == 400:
                        continue
                    return {
                        "error": f"AugLoop 返回 {resp.status_code}",
                        "prompt_type": ptype,
                        "detail": resp.text[:500],
                    }
            except httpx.RequestError as e:
                last_error = {"prompt_type": ptype, "error": str(e)}
                logger.error("请求失败: %s", e)
                continue
            except Exception as e:
                last_error = {"prompt_type": ptype, "error": str(e)}
                logger.error("send_chat 异常: %s", e, exc_info=True)
                continue

        return {
            "error": "所有 promptType 均失败",
            "attempts": last_error,
        }

    def _extract_response_text(self, data: dict) -> str:
        """
        从 AugLoop 响应中提取 AI 回复文本
        尝试多个可能的字段名
        """
        cfg = self.chat_cfg
        primary_field = cfg.get("response_field", "response")

        # 按优先级尝试的字段
        fields = [primary_field, "response", "chatResponse", "answer", "text",
                  "content", "message", "reply"]

        for f in fields:
            val = data.get(f)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, list) and val:
                # 可能是消息数组
                for item in val:
                    if isinstance(item, dict):
                        txt = item.get("content") or item.get("text") or item.get("message")
                        if txt:
                            return str(txt)
                    elif isinstance(item, str):
                        return item

        # 尝试嵌套字段
        for key in ["result", "data", "output", "choices"]:
            sub = data.get(key)
            if isinstance(sub, dict):
                for f in fields:
                    val = sub.get(f)
                    if isinstance(val, str) and val.strip():
                        return val
            elif isinstance(sub, list):
                for item in sub:
                    if isinstance(item, dict):
                        for f in fields:
                            val = item.get(f)
                            if isinstance(val, str) and val.strip():
                                return val

        # 如果找不到, 返回整个 JSON 让用户自行判断
        return f"(无法自动提取回复文本, 原始响应: {json.dumps(data, ensure_ascii=False)[:200]})"

    def update_token(self, token: str):
        """更新 Token (运行时)"""
        self.token = token
        logger.info("Token updated: %s...", token[:30])

    def update_session_key(self, session_key: str | None):
        """更新 session key (从 WebSocket Phase 1 获取)"""
        self._session_key = session_key
        if session_key:
            logger.info("Session key updated: %s...", session_key[:30])

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
