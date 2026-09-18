#!/usr/bin/env python3
"""
augloop_client.py - AugLoop (Microsoft 365 Copilot) API Client

Responsible for:
  1. Managing Bearer Token authentication
  2. Establishing WebSocket sessions
  3. HealthCheck
  4. Sending workflow requests (prompt suggestions / AI chat)
  5. Parsing responses
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
    """AugLoop API Client"""

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

        # Runtime state
        self._client: httpx.AsyncClient | None = None
        self._session_key: str | None = None

        # Prompt sanitizer (strips hardcoded built-in prompts)
        self.strip_prompts = aug.get("strip_prompts", True)
        self.prompt_stripper = PromptStripper() if self.strip_prompts else None

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    @property
    def token_preview(self) -> str:
        if not self.token:
            return "(empty)"
        return self.token[:40] + "..." if len(self.token) > 40 else self.token

    # ── Internal Methods ────────────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        """Build request headers sent to AugLoop (extracted from HAR #22)"""
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
        # 🔑 x-session-key is a required header for Workflow API
        # Without it, the server returns 400 Bad Request
        if self._session_key:
            headers["x-session-key"] = self._session_key
        return headers

    def _build_workflow_url(self, output_type: str) -> str:
        """Build workflow POST URL"""
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

    # ── Public Methods ──────────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """
        Send HealthCheck request (corresponding to HAR #2)
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
                "uiLanguage": "en-US",
                "releaseAudienceGroup": "Insiders",
                "releaseChannel": "CC",
                "releaseFork": "2606-Jun",
                "sessionId": str(uuid.uuid4()),
                "flights": "",
                "privateMode": False,
                "disabledServiceGroups": [],
                "userSystemTimezone": "UTC",
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
        Get Copilot suggested prompts (corresponding to HAR #22, promptType=DynamicActionButton)
        Returns full JSON response (including categoryPromptsList)
        """
        if not self.has_token:
            return {"error": "Bearer Token is empty, please acquire a token first or configure manually"}

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
                "error": f"AugLoop returned {resp.status_code}",
                "detail": resp.text[:500],
            }

        data = resp.json()
        # Extract x-session-key (may be needed for subsequent WebSocket connections)
        self._session_key = resp.headers.get("x-session-key")

        # Parse prompt list
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
        Send AI chat request

        Args:
            message: User input message text
            document_state: Document state (Blank / Existing / FormulaSelected ...)
            conversation_id: Optional conversation ID (for multi-turn dialogue)
            system_prompt: Optional system prompt (for tool calling, etc.)
        """
        if not self.has_token:
            return {"error": "Bearer Token is empty, please acquire a token first or configure manually"}

        cfg = self.chat_cfg
        msg_field = cfg.get("message_field", "promptText")

        # Build request body
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
            # Optional fields
            body.setdefault("suggestionContext", {"documentState": document_state})
            if conversation_id:
                body["conversationId"] = conversation_id
            # Inject system prompt (for tool calling)
            if system_prompt:
                body["systemPrompt"] = system_prompt
            return body

        # Try main prompt_type
        all_types = [cfg.get("prompt_type", "UserPrompt")] + self.fallback_types
        # Deduplicate
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

            # Strip hardcoded built-in prompts (retaining user-provided system_prompt)
            _saved_prompts = {}
            for _k in ("systemPrompt", "systemPromptText", "systemPromptType"):
                if _k in body:
                    _saved_prompts[_k] = body[_k]
            if self.prompt_stripper:
                body = self.prompt_stripper.strip_dict(body)
                logger.debug("Stripped hardcoded prompt fields")
            body.update(_saved_prompts)  # Restore our configured prompts

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
                    # 204 No Content = chat signal accepted, response pushed via WebSocket
                    self._session_key = resp.headers.get("x-session-key")
                    logger.info("[HTTP] 204 No Content - Chat signal accepted (response pushed via WebSocket)")
                    return {
                        "status": "accepted",
                        "prompt_type": ptype,
                        "response_text": "",
                    }
                elif resp.status_code == 401:
                    return {
                        "error": "Token expired (401), please refresh or acquire a new Token",
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
                        "error": f"AugLoop returned {resp.status_code}",
                        "prompt_type": ptype,
                        "detail": resp.text[:500],
                    }
            except httpx.RequestError as e:
                last_error = {"prompt_type": ptype, "error": str(e)}
                logger.error("Request failed: %s", e)
                continue
            except Exception as e:
                last_error = {"prompt_type": ptype, "error": str(e)}
                logger.error("send_chat exception: %s", e, exc_info=True)
                continue

        return {
            "error": "All promptTypes failed",
            "attempts": last_error,
        }

    def _extract_response_text(self, data: dict) -> str:
        """
        Extract AI reply text from AugLoop response
        Tries multiple candidate field names
        """
        cfg = self.chat_cfg
        primary_field = cfg.get("response_field", "response")

        # Fields tried by priority
        fields = [primary_field, "response", "chatResponse", "answer", "text",
                  "content", "message", "reply"]

        for f in fields:
            val = data.get(f)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, list) and val:
                # Could be a message array
                for item in val:
                    if isinstance(item, dict):
                        txt = item.get("content") or item.get("text") or item.get("message")
                        if txt:
                            return str(txt)
                    elif isinstance(item, str):
                        return item

        # Try nested fields
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

        # If not found, return raw JSON snippet for debugging
        return f"(Unable to extract response text automatically, raw response: {json.dumps(data, ensure_ascii=False)[:200]})"

    def update_token(self, token: str):
        """Update Token (at runtime)"""
        self.token = token
        logger.info("Token updated: %s...", token[:30])

    def update_session_key(self, session_key: str | None):
        """Update session key (obtained from WebSocket Phase 1)"""
        self._session_key = session_key
        if session_key:
            logger.info("Session key updated: %s...", session_key[:30])

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
