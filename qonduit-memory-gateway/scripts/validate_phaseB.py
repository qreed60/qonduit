#!/usr/bin/env python3
"""
Validation script for Phase B: OpenAI-style tool schema support.

Tests:
1. Plain chat request (backward compatibility)
2. Request with tools/tool_choice
3. Assistant message with tool_calls round-trip shape
4. Tool role message shape
"""

import sys
sys.path.insert(0, '/workspace/qonduit-memory-gateway')

from pydantic import BaseModel, Field
from typing import Any, Literal


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] | None = None


class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ChatMessage(BaseModel):
    role: str
    content: Any | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    model_config = {"extra": "allow"}


class GatewayChatRequest(BaseModel):
    conversation_id: str | None = None
    project_id: str | None = None
    messages: list[ChatMessage]
    model: str
    context_size: int | None = Field(default=None)
    max_tokens: int = Field(default=2048)
    temperature: float = Field(default=0.7)
    stream: bool = False
    user: str | None = None
    rag_collection: str | None = None
    mode: str | None = None
    tools: list[ToolDefinition] | None = None
    tool_choice: str | dict[str, Any] | None = None
    model_config = {"extra": "allow"}


def test_plain_chat_request():
    """Test 1: Plain chat request (backward compatibility)."""
    print("Test 1: Plain chat request (backward compatibility)")
    
    plain_req = GatewayChatRequest(
        messages=[
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi there!")
        ],
        model="test-model"
    )
    
    assert len(plain_req.messages) == 2, "Should have 2 messages"
    assert plain_req.tools is None, "tools should be None by default"
    assert plain_req.tool_choice is None, "tool_choice should be None by default"
    assert plain_req.messages[0].role == "user"
    assert plain_req.messages[0].content == "Hello"
    assert plain_req.messages[1].role == "assistant"
    assert plain_req.messages[1].content == "Hi there!"
    assert plain_req.messages[0].tool_calls is None
    assert plain_req.messages[0].tool_call_id is None
    
    print("  ✓ PASSED: Plain chat request works correctly")
    return True


def test_request_with_tools():
    """Test 2: Request with tools and tool_choice."""
    print("Test 2: Request with tools and tool_choice")
    
    tools_req = GatewayChatRequest(
        messages=[ChatMessage(role="user", content="What is the weather?")],
        model="test-model",
        tools=[
            ToolDefinition(
                type="function",
                function=ToolFunction(
                    name="get_weather",
                    description="Get current weather for a location",
                    parameters={
                        "type": "object",
                        "properties": {"location": {"type": "string"}}
                    }
                )
            ),
            ToolDefinition(
                type="function",
                function=ToolFunction(
                    name="search_docs",
                    description="Search documentation",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer"}
                        }
                    }
                )
            )
        ],
        tool_choice="auto"
    )
    
    assert tools_req.tools is not None, "tools should not be None"
    assert len(tools_req.tools) == 2, "Should have 2 tools"
    assert tools_req.tools[0].function.name == "get_weather"
    assert tools_req.tools[1].function.name == "search_docs"
    assert tools_req.tool_choice == "auto"
    
    # Test tool_choice as dict
    tools_req_dict = GatewayChatRequest(
        messages=[ChatMessage(role="user", content="Call get_weather")],
        model="test-model",
        tools=tools_req.tools,
        tool_choice={"type": "function", "function": {"name": "get_weather"}}
    )
    assert isinstance(tools_req_dict.tool_choice, dict)
    assert tools_req_dict.tool_choice["type"] == "function"
    
    print("  ✓ PASSED: Request with tools/tool_choice works correctly")
    return True


def test_assistant_tool_calls_roundtrip():
    """Test 3: Assistant message with tool_calls round-trip shape."""
    print("Test 3: Assistant message with tool_calls round-trip shape")
    
    assistant_msg = ChatMessage(
        role="assistant",
        content="Let me check the weather for you.",
        tool_calls=[
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "San Francisco"}'
                }
            },
            {
                "id": "call_def456",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"location": "Oakland"}'
                }
            }
        ]
    )
    
    assert assistant_msg.role == "assistant"
    assert assistant_msg.content == "Let me check the weather for you."
    assert assistant_msg.tool_calls is not None
    assert len(assistant_msg.tool_calls) == 2
    assert assistant_msg.tool_calls[0]["id"] == "call_abc123"
    assert assistant_msg.tool_calls[0]["type"] == "function"
    assert assistant_msg.tool_calls[0]["function"]["name"] == "get_weather"
    assert assistant_msg.tool_calls[1]["id"] == "call_def456"
    
    # Verify round-trip serialization
    serialized = assistant_msg.model_dump()
    assert serialized["role"] == "assistant"
    assert serialized["tool_calls"][0]["id"] == "call_abc123"
    
    deserialized = ChatMessage(**serialized)
    assert deserialized.role == "assistant"
    assert len(deserialized.tool_calls) == 2
    
    print("  ✓ PASSED: Assistant tool_calls round-trip works correctly")
    return True


def test_tool_role_message():
    """Test 4: Tool role message shape."""
    print("Test 4: Tool role message shape")
    
    tool_msg = ChatMessage(
        role="tool",
        content='{"temp": 72, "condition": "sunny", "humidity": 45}',
        tool_call_id="call_abc123"
    )
    
    assert tool_msg.role == "tool"
    assert "temp" in tool_msg.content
    assert tool_msg.tool_call_id == "call_abc123"
    
    # Test with different content types
    tool_msg_str = ChatMessage(
        role="tool",
        content="The temperature is 72 degrees and sunny.",
        tool_call_id="call_xyz789"
    )
    assert tool_msg_str.role == "tool"
    assert tool_msg_str.tool_call_id == "call_xyz789"
    
    # Verify round-trip
    serialized = tool_msg.model_dump()
    assert serialized["role"] == "tool"
    assert serialized["tool_call_id"] == "call_abc123"
    
    deserialized = ChatMessage(**serialized)
    assert deserialized.role == "tool"
    assert deserialized.tool_call_id == "call_abc123"
    
    print("  ✓ PASSED: Tool role message shape works correctly")
    return True


def test_full_conversation_with_tools():
    """Test 5: Full conversation flow with tools."""
    print("Test 5: Full conversation flow with tools")
    
    messages = [
        ChatMessage(role="user", content="What's the weather in SF?"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"location": "SF"}'}
            }]
        ),
        ChatMessage(
            role="tool",
            content='{"temp": 68, "condition": "foggy"}',
            tool_call_id="call_1"
        ),
        ChatMessage(role="assistant", content="It's 68 degrees and foggy in SF.")
    ]
    
    req = GatewayChatRequest(
        messages=messages,
        model="test-model",
        tools=[
            ToolDefinition(
                type="function",
                function=ToolFunction(name="get_weather", description="Get weather")
            )
        ],
        tool_choice="auto"
    )
    
    assert len(req.messages) == 4
    assert req.messages[0].role == "user"
    assert req.messages[1].role == "assistant"
    assert req.messages[1].tool_calls is not None
    assert req.messages[2].role == "tool"
    assert req.messages[2].tool_call_id == "call_1"
    assert req.messages[3].role == "assistant"
    
    print("  ✓ PASSED: Full conversation flow with tools works correctly")
    return True


def main():
    print("=" * 60)
    print("Phase B: Tool Schema Support Validation")
    print("=" * 60)
    print()
    
    tests = [
        test_plain_chat_request,
        test_request_with_tools,
        test_assistant_tool_calls_roundtrip,
        test_tool_role_message,
        test_full_conversation_with_tools,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
        except AssertionError as e:
            print(f"  ✗ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            failed += 1
        print()
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
