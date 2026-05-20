import sys
import types
import json

import observability


def test_langfuse_prompt_metadata_returns_prompt_object(monkeypatch):
    prompt = object()

    monkeypatch.setattr(observability, "langfuse_is_configured", lambda: True)
    monkeypatch.setattr(observability, "configure_langfuse_environment", lambda: None)
    monkeypatch.setattr(observability, "get_langfuse_obj", lambda name: prompt)

    assert observability.langfuse_prompt_metadata("chat_mode") == {
        "langfuse_prompt": prompt,
    }


def test_langfuse_prompt_metadata_omits_missing_prompt(monkeypatch):
    monkeypatch.setattr(observability, "langfuse_is_configured", lambda: True)
    monkeypatch.setattr(observability, "configure_langfuse_environment", lambda: None)
    monkeypatch.setattr(observability, "get_langfuse_obj", lambda name: None)

    assert observability.langfuse_prompt_metadata("missing") == {}


def test_langfuse_callbacks_for_prompt_uses_official_callback(monkeypatch):
    class FakeCallbackHandler:
        pass

    monkeypatch.setattr(observability, "langfuse_is_configured", lambda: True)
    monkeypatch.setattr(observability, "configure_langfuse_environment", lambda: None)
    monkeypatch.setitem(
        sys.modules,
        "langfuse.langchain",
        types.SimpleNamespace(CallbackHandler=FakeCallbackHandler),
    )

    callbacks = observability.langfuse_callbacks_for_prompt("chat_mode")

    assert len(callbacks) == 1
    assert isinstance(callbacks[0], FakeCallbackHandler)


def test_generation_prompt_metadata_includes_stack_and_primary_prompt():
    stack_json = json.dumps([
        {
            "name": "core",
            "base_name": "core",
            "source_name": "core",
            "version": "sha256:core",
        },
        {
            "name": "chat_mode",
            "base_name": "chat_mode",
            "source_name": "chat_mode",
            "version": "sha256:chat",
        },
    ])
    meta = observability.generation_prompt_metadata(
        {
            "prompt_stack_name": "chat_default",
            "prompt_stack_json": stack_json,
            "prompt_name": "chat_mode",
            "prompt_version": "sha256:chat",
            "agent_name": "chat_agent",
            "task_type": "chat_turn",
        },
        prompt_name="chat_mode",
    )

    assert meta["prompt_stack_name"] == "chat_default"
    assert meta["primary_prompt"] == {"name": "chat_mode", "version": "sha256:chat"}
    assert meta["prompt_stack_json"][1]["base_name"] == "chat_mode"
    assert meta["agent_name"] == "chat_agent"
