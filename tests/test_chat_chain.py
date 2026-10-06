"""Tests for src.simulation.chat_chain — stateful patient history management."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from simulation.chat_chain import (
    get_session_history,
    clear_session_history,
    MedGemmaRunnable,
    _store,
)
from langchain_core.messages import HumanMessage, AIMessage


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_get_session_history_creates_new():
    sid = "test-new-session-001"
    _store.pop(sid, None)  # clean slate
    h = get_session_history(sid)
    assert h is not None
    assert len(h.messages) == 0


def test_get_session_history_returns_same_instance():
    sid = "test-same-instance-002"
    _store.pop(sid, None)
    h1 = get_session_history(sid)
    h1.add_message(HumanMessage(content="hello"))
    h2 = get_session_history(sid)
    assert h1 is h2
    assert len(h2.messages) == 1


def test_clear_session_history_removes():
    sid = "test-clear-003"
    _store.pop(sid, None)
    h = get_session_history(sid)
    h.add_message(HumanMessage(content="hi"))
    clear_session_history(sid)
    assert sid not in _store
    # After clearing, get_session returns a fresh one
    h2 = get_session_history(sid)
    assert len(h2.messages) == 0
    _store.pop(sid, None)


def test_clear_nonexistent_session_no_error():
    clear_session_history("nonexistent-session-xyz")


def test_session_isolation():
    sid_a = "test-isolation-a"
    sid_b = "test-isolation-b"
    _store.pop(sid_a, None)
    _store.pop(sid_b, None)

    ha = get_session_history(sid_a)
    hb = get_session_history(sid_b)
    ha.add_message(HumanMessage(content="question from A"))
    hb.add_message(HumanMessage(content="question from B"))

    assert len(ha.messages) == 1
    assert len(hb.messages) == 1
    assert ha.messages[0].content == "question from A"
    assert hb.messages[0].content == "question from B"

    _store.pop(sid_a, None)
    _store.pop(sid_b, None)


def test_flatten_messages():
    from langchain_core.messages import SystemMessage

    msgs = [
        SystemMessage(content="You are a patient."),
        HumanMessage(content="What hurts?"),
        AIMessage(content="My chest hurts."),
        HumanMessage(content="Since when?"),
    ]
    flat = MedGemmaRunnable._flatten(msgs)
    assert "You are a patient." in flat
    assert "Resident: What hurts?" in flat
    assert "Patient: My chest hurts." in flat
    assert "Resident: Since when?" in flat


def test_vllm_chat_path_uses_text_content_parts():
    from langchain_core.messages import SystemMessage

    class FakeVllmAgent:
        def __init__(self):
            self.messages = None

        def sync_chat_messages(self, messages, temperature=0.7, max_tokens=512):
            self.messages = messages
            return "My chest hurts."

    agent = FakeVllmAgent()
    runnable = MedGemmaRunnable(agent)

    result = runnable.invoke([
        SystemMessage(content="You are a patient."),
        HumanMessage(content="What hurts?"),
        AIMessage(content="My stomach hurt earlier."),
        HumanMessage(content="What about now?"),
    ])

    assert result.content == "My chest hurts."
    assert agent.messages == [
        {
            "role": "system",
            "content": [{"type": "text", "text": "You are a patient."}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "What hurts?"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "My stomach hurt earlier."}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "What about now?"}],
        },
    ]


def test_vllm_chat_path_trims_old_history_for_local_context():
    from langchain_core.messages import SystemMessage

    class FakeVllmAgent:
        def __init__(self):
            self.messages = None

        def sync_chat_messages(self, messages, temperature=0.7, max_tokens=512):
            self.messages = messages
            self.max_tokens = max_tokens
            return "It started this morning."

    agent = FakeVllmAgent()
    runnable = MedGemmaRunnable(agent)
    messages = [SystemMessage(content="You are a patient.")]
    for idx in range(8):
        messages.append(HumanMessage(content=f"question {idx}"))
        messages.append(AIMessage(content=f"answer {idx}"))
    messages.append(HumanMessage(content="When did it start?"))

    result = runnable.invoke(messages)

    assert result.content == "It started this morning."
    assert agent.messages[0]["role"] == "system"
    assert [msg["role"] for msg in agent.messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert agent.messages[1]["content"][0]["text"] == "question 6"
    assert agent.messages[-1]["content"][0]["text"] == "When did it start?"
    assert agent.max_tokens == 128
