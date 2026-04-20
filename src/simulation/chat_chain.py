"""
Stateful patient-persona chain for clinical simulation.

Architecture
------------

  ChatPromptTemplate                     ← system (case context) +
    ├─ system: case presentation               MessagesPlaceholder (history) +
    ├─ MessagesPlaceholder("history")          human (current question)
    └─ human: {question}
          │
          ▼
  MedGemmaRunnable                       ← bridges LangChain message list
    ├─ agent.generate_medgemma()  (vLLM)       → our string-in / string-out API
    └─ agent.chat()               (HF/Transformers)
          │
          ▼ AIMessage
  RunnableWithMessageHistory             ← persists HumanMessage + AIMessage
    └─ InMemoryChatMessageHistory              into per-session store after
         keyed by session_id                   every turn

Effect: the patient persona sees every prior exchange in context and cannot
contradict itself across turns within the same simulation session.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.runnables.history import RunnableWithMessageHistory

logger = logging.getLogger(__name__)

# ── Per-session history store ─────────────────────────────────────────────────

_store: dict[str, InMemoryChatMessageHistory] = {}


def get_session_history(session_id: str) -> InMemoryChatMessageHistory:
    """Return (or create) the ChatMessageHistory for *session_id*."""
    if session_id not in _store:
        _store[session_id] = InMemoryChatMessageHistory()
    return _store[session_id]


def clear_session_history(session_id: str) -> None:
    """Discard history for a completed or expired session to free memory."""
    _store.pop(session_id, None)


# ── Patient persona prompt ────────────────────────────────────────────────────

_PATIENT_SYSTEM = """\
You are roleplaying as a patient in a medical simulation for resident education.

Patient background:
{case_presentation}

Your known history (answer ONLY from this information, stay in character):
{history_context}

Rules:
- Respond in first person, naturally and emotionally.
- Only reveal what the resident specifically asks about.
- If asked about something not in your history, say you're unsure.
- Show appropriate distress matching the presentation.
- Do NOT use medical or anatomical terms - describe symptoms as an ordinary person would.
- Keep responses to 2-4 sentences.

Examples of how to respond:

Resident: "When did the pain start?"
Patient: "It started yesterday morning when I was eating breakfast."

Resident: "How long has this been going on?"
Patient: "Since yesterday morning, so about a day now."

Resident: "Does anything make it better?"
Patient: "I tried taking some painkillers from my medicine cabinet, but they haven't helped much."

Resident: "What medications do you take?"
Patient: "Just my blood pressure pill - lisinopril, I think 10mg? Take it every morning."
"""

_PATIENT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _PATIENT_SYSTEM),
    MessagesPlaceholder(variable_name="history"),   # ← prior turns injected here
    ("human", "{question}"),
])


# ── LangChain Runnable wrapping MedGemma ─────────────────────────────────────

class MedGemmaRunnable(Runnable):
    """
    Adapts our MedGemma agent (string-in / string-out) to the LangChain
    Runnable interface (messages-in / AIMessage-out).

    Dispatches to:
    - agent.generate_medgemma()  if the agent is a VLLMModelManager
    - agent.chat()               if the agent is a MedGemmaAgent (HF Transformers)

    Returning AIMessage ensures RunnableWithMessageHistory stores the patient
    reply in the session history automatically.
    """

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def invoke(
        self,
        input: PromptValue | list[BaseMessage],
        config: dict | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        # ChatPromptTemplate passes a PromptValue; extract the messages list
        messages: list[BaseMessage] = (
            input.to_messages() if isinstance(input, PromptValue) else input
        )
        try:
            if hasattr(self.agent, "sync_chat_messages"):
                # VLLMClient path: send proper role-separated messages so the
                # model receives system/user/assistant structure and stays in
                # patient persona — far more reliable than a flat string.
                oai_messages = []
                for msg in messages:
                    if msg.type == "system":
                        oai_messages.append({"role": "system", "content": msg.content})
                    elif msg.type == "human":
                        oai_messages.append({"role": "user", "content": msg.content})
                    elif msg.type == "ai":
                        oai_messages.append({"role": "assistant", "content": msg.content})
                text = self.agent.sync_chat_messages(
                    oai_messages, temperature=0.7, max_tokens=256
                )
            elif hasattr(self.agent, "generate_medgemma"):
                flat_prompt = self._flatten(messages)
                text = self.agent.generate_medgemma(
                    flat_prompt, temperature=0.3, max_tokens=256
                )
            elif hasattr(self.agent, "chat"):
                flat_prompt = self._flatten(messages)
                text = self.agent.chat(flat_prompt)
            else:
                logger.warning("MedGemmaRunnable: agent has no known inference method")
                text = "I'm sorry, I don't quite understand. Could you ask that differently?"
        except Exception as exc:
            logger.warning("MedGemmaRunnable inference failed: %s", exc)
            text = "I'm not feeling well enough to answer right now."

        return AIMessage(content=text)

    @staticmethod
    def _flatten(messages: list[BaseMessage]) -> str:
        """
        Flatten a LangChain messages list to a single prompt string.

        Format:
          <system content>

          Patient: <prior ai turn>
          Resident: <prior human turn>
          ...
          Resident: <current question>
        """
        parts: list[str] = []
        for msg in messages:
            if msg.type == "system":
                parts.append(msg.content)
            elif msg.type == "human":
                parts.append(f"Resident: {msg.content}")
            elif msg.type == "ai":
                parts.append(f"Patient: {msg.content}")
        return "\n\n".join(parts)


# ── Factory ───────────────────────────────────────────────────────────────────

def build_patient_chain(agent: Any) -> RunnableWithMessageHistory:
    """
    Build and return the stateful patient-persona chain for a given agent.

    Usage
    -----
    ::

        chain = build_patient_chain(agent)

        response: AIMessage = chain.invoke(
            {
                "case_presentation": case.presentation,
                "history_context": history_ctx,
                "question": resident_question,
            },
            config={"configurable": {"session_id": session_id}},
        )
        patient_reply = response.content

    The chain automatically:
    - Appends the resident's question as a HumanMessage to the session history.
    - Appends the patient's reply as an AIMessage to the session history.
    - Re-injects all prior turns on every subsequent call so the patient
      persona remains consistent across the entire session.
    """
    chain = _PATIENT_PROMPT | MedGemmaRunnable(agent)
    return RunnableWithMessageHistory(
        chain,
        get_session_history,
        input_messages_key="question",
        history_messages_key="history",
    )
