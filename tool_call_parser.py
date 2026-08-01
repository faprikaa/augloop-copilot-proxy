#!/usr/bin/env python3
"""
tool_call_parser.py - Parse tool-call requests from AI text

AugLoop Copilot does not natively support OpenAI function calling.
Uses prompt engineering so AI can request tool calls in text.
"""

import json
import re
import logging

logger = logging.getLogger("tools.parser")

# Tag strings built via concat to avoid source parsing issues
TC_OPEN = "<" + "tool_call"
TC_CLOSE = "<" + "/tool_call" + ">"


class ToolCallParser:
    """Parse tool-call requests from AI response text."""

    # Use str.replace() instead of str.format() to avoid
    # curly-brace conflicts with JSON examples in the template.
    SYSTEM_PROMPT_TEMPLATE = (
        "You can use the following tools to help answer questions.\n\n"
        "Available tools:\n\n"
        "__TOOL_DESCRIPTIONS__\n\n"
        "## How to call tools\n\n"
        "When you need to use a tool, include this in your reply:\n\n"
        + chr(60) + 'tool_call name="tool_name"' + chr(62) + '\n'
        + '{"param": "value"}' + '\n'
        + TC_CLOSE + '\n\n'
        "After calling a tool, I will give you the result.\n"
        "Then you can continue answering.\n\n"
        "If no tool is needed, answer normally.\n"
        "Only call tools when truly needed.\n"
        "Wait for results - never fabricate them.\n"
        "Arguments must be valid JSON."
    )

    @staticmethod
    def build_system_prompt(tools):
        """Build system prompt with tool descriptions."""
        descriptions = []
        for tool in tools:
            params_str = json.dumps(tool.parameters, ensure_ascii=False, indent=2)
            descriptions.append(f"### {tool.name}\n{tool.description}\nParameters:\n{params_str}")
        return ToolCallParser.SYSTEM_PROMPT_TEMPLATE.replace(
            "__TOOL_DESCRIPTIONS__", "\n\n".join(descriptions)
        )

    @staticmethod
    def parse(text):
        """
        Parse tool calls from AI text.

        Returns:
            (display_text, tool_calls)
            - display_text: text without tool-call markers
            - tool_calls: list of dicts with name, arguments, raw
        """
        tool_calls = []
        display_parts = []

        # Pattern: tag name="xxx" followed by JSON, then closing tag
        # Built via concat to avoid source parsing issues
        pattern_str = (
            r'<' + r'tool_call\s+name="(\w+)"' + chr(62)
            + r'(.*?)'
            + r'<' + r'/tool_call' + chr(62)
        )
        pattern = re.compile(pattern_str, re.DOTALL)

        last_end = 0
        for m in pattern.finditer(text):
            display_parts.append(text[last_end:m.start()])
            name = m.group(1)
            raw_args = m.group(2).strip()
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError as e:
                args = {"_parse_error": str(e), "_raw": raw_args}
            tool_calls.append({"name": name, "arguments": args, "raw": m.group(0)})
            last_end = m.end()

        display_parts.append(text[last_end:])
        display_text = "".join(display_parts).strip()

        # Also check JSON code block format
        if not tool_calls:
            block_pattern = re.compile(r"```tool_call\s*\n(.*?)\n```", re.DOTALL)
            for m in block_pattern.finditer(text):
                try:
                    data = json.loads(m.group(1).strip())
                    if isinstance(data, dict) and "name" in data:
                        tool_calls.append({
                            "name": data["name"],
                            "arguments": data.get("arguments", data),
                            "raw": m.group(0),
                        })
                except json.JSONDecodeError:
                    pass
            if tool_calls:
                display_text = block_pattern.sub("", text).strip()

        return display_text, tool_calls

    @staticmethod
    def format_tool_result(name, result, call_id=""):
        """Format a tool result for feeding back to the AI."""
        return (
            f'<tool_result name="{name}" call_id="{call_id}">'
            f"\n{result}\n"
            + chr(60) + "/tool_result" + chr(62)
        )

    @staticmethod
    def has_tool_calls(text):
        """Quick check if text contains tool calls."""
        return TC_OPEN in text or "```tool_call" in text
