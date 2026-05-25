from types import SimpleNamespace

from services.trace_capture import LocalTracer


def test_local_tracer_captures_prompt_and_tool_call_fields():
    tracer = LocalTracer(thread_id="thread-1", agent_name="router")

    tracer.on_chain_start(
        {"name": "router"},
        {"messages": ["hi"]},
        run_id="root",
        metadata={"agent_name": "router"},
    )
    tracer.on_chat_model_start(
        {"name": "AzureChatOpenAI"},
        [[SimpleNamespace(type="human", content="hi")]],
        run_id="gen-1",
        parent_run_id="root",
        metadata={
            "prompt_name": "chat_mode",
            "prompt_version": "sha256:abc",
            "prompt_id": "chat_mode:sha256:abc",
        },
    )
    tracer.on_llm_end(
        SimpleNamespace(
            llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2}},
            generations=[[
                SimpleNamespace(
                    text="",
                    message=SimpleNamespace(tool_calls=[{"name": "lookup", "args": {"q": "hi"}}]),
                )
            ]],
        ),
        run_id="gen-1",
    )
    tracer.on_tool_start(
        {"name": "lookup", "description": "Search docs"},
        "hi",
        run_id="tool-1",
        parent_run_id="root",
    )

    rows = tracer._build_trace_rows({"answer": "ok"})
    events = tracer._build_trace_events(rows)
    observations = [event["body"] for event in events if event["event_type"] == "observation-create"]
    generation = next(obs for obs in observations if obs["observation_id"] == "gen-1")
    tool = next(obs for obs in observations if obs["observation_id"] == "tool-1")

    assert generation["prompt_id"] == "chat_mode:sha256:abc"
    assert generation["prompt_name"] == "chat_mode"
    assert generation["prompt_version"] == "sha256:abc"
    assert generation["tool_call_names"] == ["lookup"]
    assert generation["tool_calls"][0]["args"] == {"q": "hi"}
    assert tool["tool_call_names"] == ["lookup"]
    assert tool["tool_definitions"]["description"] == "Search docs"
