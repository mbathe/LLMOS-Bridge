"""Tests for ContextManager — message tracking, compression, token estimation."""

import pytest

from llmos_bridge.apps.context_manager import ContextManager, Message, estimate_tokens
from llmos_bridge.apps.models import ContextConfig, ContextStrategy


@pytest.fixture
def config():
    return ContextConfig(max_tokens=1000, strategy=ContextStrategy.truncate, keep_last_n_messages=5)


@pytest.fixture
def mgr(config):
    return ContextManager(config)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_string(self):
        assert estimate_tokens("hi") == 1

    def test_normal_string(self):
        result = estimate_tokens("hello world, this is a test")
        assert result == len("hello world, this is a test") // 4

    def test_long_string(self):
        text = "a" * 1000
        assert estimate_tokens(text) == 250


class TestMessage:
    def test_to_dict_basic(self):
        msg = Message(role="user", content="hello")
        d = msg.to_dict()
        assert d == {"role": "user", "content": "hello"}

    def test_to_dict_with_tool_calls(self):
        msg = Message(role="assistant", content="thinking", tool_calls=[{"id": "1", "type": "function"}])
        d = msg.to_dict()
        assert "tool_calls" in d
        assert d["tool_calls"][0]["id"] == "1"

    def test_to_dict_with_tool_call_id(self):
        msg = Message(role="tool", content="result", tool_call_id="tc1", name="read_file")
        d = msg.to_dict()
        assert d["tool_call_id"] == "tc1"
        assert d["name"] == "read_file"

    def test_to_dict_omits_empty_fields(self):
        msg = Message(role="user", content="hi")
        d = msg.to_dict()
        assert "tool_calls" not in d
        assert "tool_call_id" not in d
        assert "name" not in d


class TestContextManagerBasics:
    def test_initial_state(self, mgr):
        assert mgr.message_count == 0
        assert mgr.total_tokens == 0
        assert mgr.messages == []

    def test_add_user_message(self, mgr):
        mgr.add_user_message("hello")
        assert mgr.message_count == 1
        assert mgr.messages[0].role == "user"
        assert mgr.messages[0].content == "hello"

    def test_add_assistant_message(self, mgr):
        mgr.add_assistant_message("response", tool_calls=[{"id": "1"}])
        assert mgr.message_count == 1
        assert mgr.messages[0].role == "assistant"
        assert mgr.messages[0].tool_calls == [{"id": "1"}]

    def test_add_tool_result(self, mgr):
        mgr.add_tool_result("tc1", "read_file", '{"content": "data"}')
        assert mgr.message_count == 1
        assert mgr.messages[0].role == "tool"
        assert mgr.messages[0].tool_call_id == "tc1"

    def test_token_tracking(self, mgr):
        mgr.add_user_message("hello world test message")
        assert mgr.total_tokens > 0

    def test_set_system_prompt(self, mgr):
        mgr.set_system_prompt("You are a helpful assistant.")
        msgs = mgr.get_messages_for_llm()
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a helpful assistant."

    def test_clear(self, mgr):
        mgr.add_user_message("one")
        mgr.add_user_message("two")
        mgr.clear()
        assert mgr.message_count == 0
        assert mgr.total_tokens == 0

    def test_clear_keeps_system(self, mgr):
        mgr.set_system_prompt("system msg")
        mgr.add_user_message("hello")
        mgr.clear()
        msgs = mgr.get_messages_for_llm()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"


class TestGetMessagesForLLM:
    def test_no_system_prompt(self, mgr):
        mgr.add_user_message("hi")
        msgs = mgr.get_messages_for_llm()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"

    def test_with_system_prompt(self, mgr):
        mgr.set_system_prompt("be helpful")
        mgr.add_user_message("hi")
        mgr.add_assistant_message("hello!")
        msgs = mgr.get_messages_for_llm()
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"

    def test_messages_are_copies(self, mgr):
        mgr.add_user_message("hi")
        msgs1 = mgr.messages
        msgs2 = mgr.messages
        assert msgs1 is not msgs2


class TestGetSummary:
    def test_empty(self, mgr):
        assert mgr.get_summary() == ""

    def test_summary_content(self, mgr):
        mgr.add_user_message("Fix the bug")
        mgr.add_assistant_message("Looking at the code")
        mgr.add_tool_result("tc1", "read_file", "content here")
        summary = mgr.get_summary()
        assert "User: Fix the bug" in summary
        assert "Assistant: Looking at the code" in summary
        assert "Tool(read_file)" in summary

    def test_summary_limits_to_10(self, mgr):
        config = ContextConfig(max_tokens=100000, keep_last_n_messages=100)
        big_mgr = ContextManager(config)
        for i in range(20):
            big_mgr.add_user_message(f"message {i}")
        summary = big_mgr.get_summary()
        lines = summary.strip().split("\n")
        assert len(lines) == 10


class TestCompression:
    def test_no_compression_under_threshold(self, mgr):
        mgr.add_user_message("short")
        assert mgr.message_count == 1

    def test_truncate_compression(self):
        config = ContextConfig(max_tokens=1000, strategy=ContextStrategy.truncate, keep_last_n_messages=2)
        mgr = ContextManager(config)
        for i in range(20):
            mgr.add_user_message("x" * 400)
        # Compression triggers when exceeding 80% of max_tokens (800),
        # keeping only the last 2. After the loop, there will be fewer
        # messages than were added.
        assert mgr.message_count < 20

    def test_sliding_window_compression(self):
        config = ContextConfig(max_tokens=1000, strategy=ContextStrategy.sliding_window, keep_last_n_messages=2)
        mgr = ContextManager(config)
        for i in range(20):
            mgr.add_user_message("x" * 400)
        # Sliding window should have a summary message from compression
        has_summary = any("Previous conversation summary" in m.content for m in mgr.messages)
        assert has_summary

    def test_needs_compression(self):
        config = ContextConfig(max_tokens=1000, keep_last_n_messages=5)
        mgr = ContextManager(config)
        mgr.set_system_prompt("a" * 4000)
        assert mgr.needs_compression() is True

    def test_not_needs_compression(self, mgr):
        mgr.add_user_message("short")
        assert mgr.needs_compression() is False

    def test_summarize_falls_back_to_sliding(self):
        config = ContextConfig(max_tokens=1000, strategy=ContextStrategy.summarize, keep_last_n_messages=2)
        mgr = ContextManager(config)
        for i in range(20):
            mgr.add_user_message("y" * 400)
        # Should fall back to sliding window behavior
        assert mgr.message_count < 20

    def test_tokens_recalculated_after_compression(self):
        config = ContextConfig(max_tokens=1000, strategy=ContextStrategy.truncate, keep_last_n_messages=2)
        mgr = ContextManager(config)
        for i in range(20):
            mgr.add_user_message("z" * 400)
        # Tokens should only reflect remaining messages
        expected = sum(m.token_estimate for m in mgr.messages)
        assert mgr.total_tokens == expected
