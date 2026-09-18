#!/usr/bin/env python3
"""
prompt_stripper.py - Prompt Sanitization Module

Responsible for stripping hardcoded built-in prompts from requests, including:
  - systemPrompt / systemPromptText (system prompts)
  - promptCommand (command prompts)
  - instructions (directives)
  - system role messages (messages with role=system)
  - other Microsoft built-in prompt fields
"""

import json
import os
from typing import Any


class PromptStripper:
    """Prompt sanitizer — supports 'replace mode' to bypass model security checks"""

    # Sensitive fields to remove
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

    # Fields that support replacement mode (replaced by custom system prompt rather than deleted)
    REPLACE_FIELDS = {"systemPrompt", "systemPromptText"}

    def __init__(self, custom_fields: list[str] = None, strip_system_messages: bool = True,
                 custom_system_prompt: str = ""):
        """
        Initialize the prompt stripper

        Args:
            custom_fields: Extra list of field names to remove
            strip_system_messages: Whether to remove messages with role=system in messages array
            custom_system_prompt: Custom system prompt. When set, systemPrompt and related fields
                                  will be replaced (not deleted), and system messages in messages
                                  array will also be replaced.
        """
        self.strip_fields = self.STRIP_FIELDS.copy()
        if custom_fields:
            self.strip_fields.update(custom_fields)
        self.strip_system_messages = strip_system_messages
        self.custom_system_prompt = custom_system_prompt

    def strip_dict(self, data: dict) -> dict:
        """
        Recursively delete / replace prompt fields in dictionary

        Args:
            data: Dictionary to sanitize

        Returns:
            Sanitized dictionary (modified in-place)
        """
        if not isinstance(data, dict):
            return data

        # Remove or replace top-level sensitive fields
        for field in list(data.keys()):
            if field in self.strip_fields:
                if self.custom_system_prompt and field in self.REPLACE_FIELDS:
                    # Replace mode: replace Microsoft built-in prompt with custom system prompt
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
        Sanitize prompts in a list — supports replacing or injecting system messages

        Args:
            data: List to sanitize

        Returns:
            Sanitized list
        """
        # Process messages array
        if self.strip_system_messages and all(isinstance(x, dict) for x in data):
            has_system = any(item.get("role") == "system" for item in data)

            if has_system:
                if self.custom_system_prompt:
                    # Replace the first system message with custom prompt, delete any extras
                    replaced = False
                    new_list = []
                    for item in data:
                        if item.get("role") == "system":
                            if not replaced:
                                item["content"] = self.custom_system_prompt
                                new_list.append(item)
                                replaced = True
                            # Skip extra system messages
                        else:
                            new_list.append(item)
                    data[:] = new_list
                else:
                    # No custom prompt: delete system messages
                    data[:] = [item for item in data if item.get("role") != "system"]
            elif self.custom_system_prompt:
                # No system message but custom prompt exists → inject one
                data.insert(0, {"role": "system", "content": self.custom_system_prompt})

        # Recursively process each item
        for item in data:
            if isinstance(item, dict):
                self.strip_dict(item)
            elif isinstance(item, list):
                self.strip_list(item)

        return data

    def strip_json_string(self, json_str: str) -> str:
        """
        Sanitize prompts in a JSON string

        Args:
            json_str: JSON formatted string

        Returns:
            Sanitized JSON string
        """
        try:
            data = json.loads(json_str)
            self.strip_dict(data)
            return json.dumps(data, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            # Not valid JSON, return as-is
            return json_str

    def strip_request_body(self, body: dict | str) -> dict | str:
        """
        Sanitize prompts in a request body

        Args:
            body: Request body (dict or JSON string)

        Returns:
            Sanitized request body
        """
        if isinstance(body, str):
            return self.strip_json_string(body)
        elif isinstance(body, dict):
            return self.strip_dict(body.copy())
        else:
            return body

    def strip_messages(self, messages: list) -> list:
        """
        Special handling for OpenAI-formatted messages array

        Args:
            messages: List of messages

        Returns:
            Sanitized list of messages
        """
        if not isinstance(messages, list):
            return messages

        cleaned = []
        for msg in messages:
            if not isinstance(msg, dict):
                cleaned.append(msg)
                continue

            # Skip system messages
            if msg.get("role") == "system" and self.strip_system_messages:
                continue

            # Clean prompt fields in message content
            cleaned_msg = msg.copy()
            self.strip_dict(cleaned_msg)
            cleaned.append(cleaned_msg)

        return cleaned


# Read custom system prompt (from environment variables or file)
_CUSTOM_PROMPT = os.environ.get("CUSTOM_SYSTEM_PROMPT", "")
_CUSTOM_FILE = os.environ.get("CUSTOM_SYSTEM_PROMPT_FILE", "")
if _CUSTOM_FILE and os.path.exists(_CUSTOM_FILE):
    try:
        with open(_CUSTOM_FILE, "r", encoding="utf-8") as _f:
            _CUSTOM_PROMPT = _f.read().strip()
    except Exception:
        pass

# Global default instance (supports custom system prompt injection)
_default_stripper = PromptStripper(custom_system_prompt=_CUSTOM_PROMPT)


def strip_dict(data: dict) -> dict:
    """Convenience function: strip prompts in dict using default sanitizer"""
    return _default_stripper.strip_dict(data.copy())


def strip_json_string(json_str: str) -> str:
    """Convenience function: strip prompts in JSON string using default sanitizer"""
    return _default_stripper.strip_json_string(json_str)


def strip_request_body(body: dict | str) -> dict | str:
    """Convenience function: strip prompts in request body using default sanitizer"""
    return _default_stripper.strip_request_body(body)


def strip_messages(messages: list) -> list:
    """Convenience function: strip prompts in OpenAI-formatted messages using default sanitizer"""
    return _default_stripper.strip_messages(messages)


if __name__ == "__main__":
    # Test example
    test_data = {
        "promptType": "UserPrompt",
        "promptText": "User input",
        "systemPrompt": "You are an Excel expert",
        "systemPromptText": "You must provide formatted answers",
        "promptCommand": "Add data to cell",
        "instructions": "Follow these steps...",
        "messages": [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
            {"role": "assistant", "content": "Assistant reply"},
        ],
    }

    print("=== Original Data ===")
    print(json.dumps(test_data, indent=2, ensure_ascii=False))

    print("\n=== Cleaned ===")
    cleaned = strip_dict(test_data)
    print(json.dumps(cleaned, indent=2, ensure_ascii=False))

    print("\n=== Removed Fields ===")
    for key in test_data:
        if key not in cleaned:
            print(f"  ✓ {key}")
