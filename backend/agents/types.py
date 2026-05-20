from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentRoute:
    agent_name: str
    prompt_name: str
    prompt_version: str = "unknown"
    compose_after: bool = False
    evaluate_after: bool = False


@dataclass(frozen=True)
class AgentResult:
    response: str
    sources: list[str] = field(default_factory=list)
    task_type: str = "chat_turn"
    agent_name: str = "chat_agent"
    prompt_name: str = "chat"
    prompt_version: str = "unknown"
    observation_id: str | None = None
    # Set by an agent to request a handoff to another agent.
    # The router checks this and re-routes with _hop_count + 1.
    next_agent_name: str | None = None
    # Structured coverage result from research_agent.
    coverage_result: dict | None = None
