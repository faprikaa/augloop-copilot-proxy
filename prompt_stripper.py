#!/usr/bin/env python3
"""
prompt_stripper.py - 提示词清理模块

负责从请求中删除硬编码的内置提示词，包括：
  - systemPrompt / systemPromptText (系统提示词)
  - promptCommand (命令提示词)
  - instructions (指令)
  - system role messages (role=system 的消息)
  - 其他微软内置的提示词字段
"""

import json
import os
from typing import Any


class PromptStripper:
    """提示词清理器 — 支持「替换模式」绕过模型安全检测"""

    # 需要删除的敏感字段
    STRIP_FIELDS = {
        "systemPrompt",
        "systemPromptText",
        "promptCommand",
        "instructions",
        "systemInstructions",
        "assistantMessage",
        "systemMessage",
        "contextData",
        "priorContext",
    }

    # 支持替换模式的字段（替换为自定义系统提示词，而非删除）
    REPLACE_FIELDS = {"systemPrompt", "systemPromptText"}

    def __init__(self, custom_fields: list[str] = None, strip_system_messages: bool = True,
                 custom_system_prompt: str = ""):
        """
        初始化清理器

        Args:
            custom_fields: 额外要删除的字段名列表
            strip_system_messages: 是否删除 messages 数组中 role=system 的消息
            custom_system_prompt: 自定义系统提示词。设置后，systemPrompt 等字段会被
                                  替换（而非删除），messages 中的 system 消息也会被替换。
                                  这样身份指令以系统提示词形式注入，不会触发安全检测。
        """
        self.strip_fields = self.STRIP_FIELDS.copy()
        if custom_fields:
            self.strip_fields.update(custom_fields)
        self.strip_system_messages = strip_system_messages
        self.custom_system_prompt = custom_system_prompt

    def strip_dict(self, data: dict) -> dict:
        """
        递归删除/替换字典中的提示词字段

        Args:
            data: 要清理的字典

        Returns:
            清理后的字典（原地修改）
        """
        if not isinstance(data, dict):
            return data

        # 删除或替换顶级敏感字段
        for field in list(data.keys()):
            if field in self.strip_fields:
                if self.custom_system_prompt and field in self.REPLACE_FIELDS:
                    # 替换模式：用自定义系统提示词替换微软内置的
                    data[field] = self.custom_system_prompt
                else:
                    del data[field]
            elif isinstance(data[field], dict):
                self.strip_dict(data[field])
            elif isinstance(data[field], list):
                self.strip_list(data[field])

        return data

    def strip_list(self, data: list) -> list:
        """
        清理列表中的提示词 — 支持替换或注入 system 消息

        Args:
            data: 要清理的列表

        Returns:
            清理后的列表
        """
        # 处理 messages 数组
        if self.strip_system_messages and all(isinstance(x, dict) for x in data):
            has_system = any(item.get("role") == "system" for item in data)

            if has_system:
                if self.custom_system_prompt:
                    # 替换第一条第 system 消息为自定义提示词，多余删除
                    replaced = False
                    new_list = []
                    for item in data:
                        if item.get("role") == "system":
                            if not replaced:
                                item["content"] = self.custom_system_prompt
                                new_list.append(item)
                                replaced = True
                            # 跳过多余的 system 消息
                        else:
                            new_list.append(item)
                    data[:] = new_list
                else:
                    # 没有自定义提示词，删除 system 消息
                    data[:] = [item for item in data if item.get("role") != "system"]
            elif self.custom_system_prompt:
                # 没有 system 消息但有自定义提示词 → 注入一条
                data.insert(0, {"role": "system", "content": self.custom_system_prompt})

        # 递归处理每一项
        for item in data:
            if isinstance(item, dict):
                self.strip_dict(item)
            elif isinstance(item, list):
                self.strip_list(item)

        return data

    def strip_json_string(self, json_str: str) -> str:
        """
        清理 JSON 字符串中的提示词

        Args:
            json_str: JSON 格式的字符串

        Returns:
            清理后的 JSON 字符串
        """
        try:
            data = json.loads(json_str)
            self.strip_dict(data)
            return json.dumps(data, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            # 不是有效的 JSON，原样返回
            return json_str

    def strip_request_body(self, body: dict | str) -> dict | str:
        """
        清理请求体中的提示词

        Args:
            body: 请求体（字典或 JSON 字符串）

        Returns:
            清理后的请求体
        """
        if isinstance(body, str):
            return self.strip_json_string(body)
        elif isinstance(body, dict):
            return self.strip_dict(body.copy())
        else:
            return body

    def strip_messages(self, messages: list) -> list:
        """
        特殊处理 OpenAI 格式的 messages 数组

        Args:
            messages: messages 列表

        Returns:
            清理后的 messages 列表
        """
        if not isinstance(messages, list):
            return messages

        cleaned = []
        for msg in messages:
            if not isinstance(msg, dict):
                cleaned.append(msg)
                continue

            # 跳过 system 消息
            if msg.get("role") == "system" and self.strip_system_messages:
                continue

            # 清理消息内容中的提示词字段
            cleaned_msg = msg.copy()
            self.strip_dict(cleaned_msg)
            cleaned.append(cleaned_msg)

        return cleaned


# 读取自定义系统提示词（从环境变量或文件）
_CUSTOM_PROMPT = os.environ.get("CUSTOM_SYSTEM_PROMPT", "")
_CUSTOM_FILE = os.environ.get("CUSTOM_SYSTEM_PROMPT_FILE", "")
if _CUSTOM_FILE and os.path.exists(_CUSTOM_FILE):
    try:
        with open(_CUSTOM_FILE, "r", encoding="utf-8") as _f:
            _CUSTOM_PROMPT = _f.read().strip()
    except Exception:
        pass

# 全局默认实例（支持自定义系统提示词注入）
_default_stripper = PromptStripper(custom_system_prompt=_CUSTOM_PROMPT)


def strip_dict(data: dict) -> dict:
    """便利函数：使用默认清理器删除字典中的提示词"""
    return _default_stripper.strip_dict(data.copy())


def strip_json_string(json_str: str) -> str:
    """便利函数：使用默认清理器删除 JSON 字符串中的提示词"""
    return _default_stripper.strip_json_string(json_str)


def strip_request_body(body: dict | str) -> dict | str:
    """便利函数：使用默认清理器删除请求体中的提示词"""
    return _default_stripper.strip_request_body(body)


def strip_messages(messages: list) -> list:
    """便利函数：使用默认清理器清理 OpenAI 格式的 messages"""
    return _default_stripper.strip_messages(messages)


if __name__ == "__main__":
    # 测试示例
    test_data = {
        "promptType": "UserPrompt",
        "promptText": "用户输入",
        "systemPrompt": "你是一个 Excel 专家",
        "systemPromptText": "要求你提供格式化的答案",
        "promptCommand": "在单元格中添加数据",
        "instructions": "按照以下步骤...",
        "messages": [
            {"role": "system", "content": "系统提示词"},
            {"role": "user", "content": "用户消息"},
            {"role": "assistant", "content": "回复消息"},
        ],
    }

    print("=== 原始数据 ===")
    print(json.dumps(test_data, indent=2, ensure_ascii=False))

    print("\n=== 清理后 ===")
    cleaned = strip_dict(test_data)
    print(json.dumps(cleaned, indent=2, ensure_ascii=False))

    print("\n=== 删除的字段 ===")
    for key in test_data:
        if key not in cleaned:
            print(f"  ✓ {key}")
