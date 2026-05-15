from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentRoute:
    intent: str
    agent_name: str
    prompt_name: str
    prompt_version: str = "unknown"
    original_intent: str | None = None
    resolved_intent: str | None = None
    compose_after: bool = False
    evaluate_after: bool = False


@dataclass(frozen=True)
class AgentResult:
    response: str
    sources: list[str] = field(default_factory=list)
    task_type: str = "chat_turn"
    route_intent: str | None = "chat"
    agent_name: str = "chat_agent"
    prompt_name: str = "chat"
    prompt_version: str = "unknown"
    trace_run_id: str | None = None
    # Set by an agent to request a handoff to another intent.
    # The router checks this and re-routes with _hop_count + 1.
    # retrieval_agent → "research" when comprehensive coverage is needed but retrieval finds nothing
    # question_agent → always None (resolves its own escalation internally)
    next_intent: str | None = None
