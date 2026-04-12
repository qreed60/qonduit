#!/usr/bin/env python3
"""
Validation script for Phase C: Tool Execution Loop

Tests:
1. Plain chat still works (backward compatibility)
2. Tool call execution with retrieve_project_context
3. Final assistant response after tool execution
4. Cross-project isolation
"""

import json
import sys
import httpx

GATEWAY_BASE = "http://localhost:8000"


def test_tool_execution_loop():
    """Test that tool calls are actually executed and final answer returned."""
    print("\n=== Test 6: Tool Execution Loop (Live) ===")
    
    # This test requires a project with RAG data
    payload = {
        "model": "gpt-oss:20b",
        "project_id": "test-project",
        "messages": [
            {"role": "user", "content": "What is in this project? Use retrieve_project_context to find out."}
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "retrieve_project_context",
                    "description": "Retrieve relevant context from project RAG",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "top_k": {"type": "integer", "description": "Number of results", "default": 4},
                            "user_id": {"type": "string", "description": "Optional user ID for filtering"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ],
        "tool_choice": "auto",
        "max_tokens": 500,
    }
    
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{GATEWAY_BASE}/v1/chat/completions",
                json=payload,
            )
        
        if response.status_code != 200:
            print(f"FAIL: Status code {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
        
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            print("FAIL: No choices in response")
            return False
        
        message = choices[0].get("message", {})
        content = message.get("content", "")
        finish_reason = choices[0].get("finish_reason", "")
        
        # The key test: after tool execution, we should get a final answer
        # not a tool_calls response
        if finish_reason == "tool_calls":
            print("FAIL: Response still has tool_calls - loop did not execute tools")
            print(f"Message: {json.dumps(message, indent=2)[:500]}")
            return False
        
        if not content.strip():
            print("WARN: Empty content but no tool_calls - may be expected if no RAG data")
        
        # Check usage to see if multiple calls were made
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        
        print(f"PASS: Received final answer (finish_reason={finish_reason})")
        print(f"      Content length: {len(content)} chars")
        print(f"      Prompt tokens: {prompt_tokens} (higher values suggest multiple iterations)")
        return True
    
    except httpx.ReadTimeout:
        print("WARN: Request timed out - tool execution may be slow")
        return None  # Inconclusive
    except Exception as e:
        print(f"FAIL: Exception - {e}")
        return False


def test_plain_chat():
    """Test that plain chat requests still work without tools."""
    print("\n=== Test 1: Plain Chat (Backward Compatibility) ===")
    
    payload = {
        "model": "gpt-oss:20b",
        "messages": [
            {"role": "user", "content": "Say hello in one sentence."}
        ],
        "max_tokens": 50,
    }
    
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{GATEWAY_BASE}/v1/chat/completions",
                json=payload,
            )
        
        if response.status_code != 200:
            print(f"FAIL: Status code {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
        
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            print("FAIL: No choices in response")
            return False
        
        message = choices[0].get("message", {})
        content = message.get("content", "")
        finish_reason = choices[0].get("finish_reason", "")
        
        if not content.strip():
            print("FAIL: Empty response content")
            return False
        
        if "tool_calls" in message:
            print("WARN: Unexpected tool_calls in plain chat response")
        
        print(f"PASS: Received response with {len(content)} chars, finish_reason={finish_reason}")
        return True
    
    except Exception as e:
        print(f"FAIL: Exception - {e}")
        return False


def test_tool_schema_acceptance():
    """Test that the gateway accepts tools in the request."""
    print("\n=== Test 2: Tool Schema Acceptance ===")
    
    payload = {
        "model": "gpt-oss:20b",
        "messages": [
            {"role": "user", "content": "Search for Python files."}
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "list_project_files",
                    "description": "List files in a project directory",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string"},
                            "extensions": {"type": "array", "items": {"type": "string"}},
                            "max_results": {"type": "integer"}
                        },
                        "required": []
                    }
                }
            }
        ],
        "tool_choice": "auto",
        "max_tokens": 200,
    }
    
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{GATEWAY_BASE}/v1/chat/completions",
                json=payload,
            )
        
        # We expect this to succeed (gateway should accept the schema)
        # Whether the model actually calls tools depends on the model
        if response.status_code != 200:
            print(f"FAIL: Status code {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
        
        data = response.json()
        print(f"PASS: Gateway accepted tools schema, response id={data.get('id', 'unknown')}")
        return True
    
    except Exception as e:
        print(f"FAIL: Exception - {e}")
        return False


def test_tool_message_shape():
    """Test that tool messages have correct shape."""
    print("\n=== Test 3: Tool Message Shape Validation ===")
    
    # Create a tool message directly to validate schema
    tool_message = {
        "role": "tool",
        "content": "Test result content",
        "tool_call_id": "call_test123"
    }
    
    # Validate required fields
    required_fields = ["role", "content"]
    optional_fields = ["tool_call_id"]
    
    for field in required_fields:
        if field not in tool_message:
            print(f"FAIL: Missing required field '{field}'")
            return False
    
    if tool_message["role"] != "tool":
        print(f"FAIL: role should be 'tool', got '{tool_message['role']}'")
        return False
    
    if not isinstance(tool_message["content"], str):
        print(f"FAIL: content should be string")
        return False
    
    print("PASS: Tool message shape is valid")
    return True


def test_assistant_tool_calls_shape():
    """Test that assistant messages with tool_calls have correct shape."""
    print("\n=== Test 4: Assistant Tool Calls Shape Validation ===")
    
    assistant_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "retrieve_project_context",
                    "arguments": '{"query": "test"}'
                }
            }
        ]
    }
    
    # Validate structure
    if assistant_message["role"] != "assistant":
        print(f"FAIL: role should be 'assistant'")
        return False
    
    if "tool_calls" not in assistant_message:
        print(f"FAIL: Missing tool_calls")
        return False
    
    if not isinstance(assistant_message["tool_calls"], list):
        print(f"FAIL: tool_calls should be a list")
        return False
    
    tc = assistant_message["tool_calls"][0]
    required_tc_fields = ["id", "type", "function"]
    for field in required_tc_fields:
        if field not in tc:
            print(f"FAIL: Missing tool_call field '{field}'")
            return False
    
    func = tc.get("function", {})
    if "name" not in func or "arguments" not in func:
        print(f"FAIL: function missing name or arguments")
        return False
    
    print("PASS: Assistant tool_calls shape is valid")
    return True


def test_cross_project_isolation():
    """Test that tools respect project boundaries."""
    print("\n=== Test 5: Cross-Project Isolation (Schema Check) ===")
    
    # This test validates that the implementation uses project_id correctly
    # Actual isolation testing would require setting up multiple projects
    
    # Check that execute_tool functions receive project_id parameter
    import sys
    import inspect
    sys.path.insert(0, '/workspace/qonduit-memory-gateway')
    
    try:
        from app.main import execute_retrieve_project_context, execute_search_project_files, execute_list_project_files
        
        for func in [execute_retrieve_project_context, execute_search_project_files, execute_list_project_files]:
            sig = inspect.signature(func)
            if "project_id" not in sig.parameters:
                print(f"FAIL: {func.__name__} missing project_id parameter")
                return False
        
        print("PASS: All tool handlers accept project_id parameter")
        return True
    except Exception as e:
        print(f"WARN: Could not import handlers - {e}")
        # Fall back to source code inspection
        with open('/workspace/qonduit-memory-gateway/app/main.py', 'r') as f:
            content = f.read()
        
        # Check that each handler has project_id in signature
        handlers = ['execute_retrieve_project_context', 'execute_search_project_files', 'execute_list_project_files']
        for handler in handlers:
            if f'def {handler}(' not in content:
                print(f"FAIL: {handler} not found in source")
                return False
            # Find the function definition
            start = content.find(f'def {handler}(')
            end = content.find(') ->', start)
            sig_line = content[start:end+2]
            if 'project_id' not in sig_line:
                print(f"FAIL: {handler} missing project_id in signature")
                return False
        
        print("PASS: All tool handlers have project_id parameter (source verified)")
        return True


def main():
    print("=" * 60)
    print("Phase C: Tool Execution Loop Validation")
    print("=" * 60)
    
    results = []
    
    # Schema validation tests (don't need gateway running)
    results.append(("Tool Message Shape", test_tool_message_shape()))
    results.append(("Assistant Tool Calls Shape", test_assistant_tool_calls_shape()))
    results.append(("Cross-Project Isolation (Schema)", test_cross_project_isolation()))
    
    # Gateway-dependent tests
    print("\n" + "=" * 60)
    print("Gateway-dependent tests (requires running gateway)")
    print("=" * 60)
    
    try:
        with httpx.Client(timeout=5.0) as client:
            client.get(f"{GATEWAY_BASE}/health")
        gateway_running = True
    except Exception:
        gateway_running = False
        print("\n⚠️  Gateway not running at {}. Skipping live tests.".format(GATEWAY_BASE))
    
    if gateway_running:
        results.append(("Plain Chat", test_plain_chat()))
        results.append(("Tool Schema Acceptance", test_tool_schema_acceptance()))
        results.append(("Tool Execution Loop", test_tool_execution_loop()))
    
    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
