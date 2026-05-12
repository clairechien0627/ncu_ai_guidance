def build_trace_metadata(
    *,
    mode: str,
    agent_name: str,
    prompt_name: str,
    prompt_version: str = "v1",
) -> dict[str, str]:
    return {
        "mode": mode,
        "agent_name": agent_name,
        "prompt_name": prompt_name,
        "prompt_version": prompt_version,
    }


def estimate_prompt_tokens(*contents: str | None) -> int:
    """Cheap local estimate for prompt stack size; avoids tokenizer dependency."""
    text = "\n".join(content or "" for content in contents)
    if not text:
        return 0
    return max(1, len(text) // 4)


def build_prompt_stack_metadata(
    *,
    prompt_stack_name: str | None = None,
    prompt_stack_json: str | None = None,
    base_prompt_name: str | None = None,
    base_prompt_hash: str | None = None,
    task_prompt_name: str | None = None,
    task_prompt_hash: str | None = None,
    quality_prompt_name: str | None = None,
    quality_prompt_hash: str | None = None,
    prompt_stack_tokens: int | None = None,
) -> dict[str, str | int]:
    data: dict[str, str | int] = {}
    if prompt_stack_name:
        data["prompt_stack_name"] = prompt_stack_name
    if prompt_stack_json:
        data["prompt_stack_json"] = prompt_stack_json
    if base_prompt_name:
        data["base_prompt_name"] = base_prompt_name
    if base_prompt_hash:
        data["base_prompt_hash"] = base_prompt_hash
    if task_prompt_name:
        data["task_prompt_name"] = task_prompt_name
    if task_prompt_hash:
        data["task_prompt_hash"] = task_prompt_hash
    if quality_prompt_name:
        data["quality_prompt_name"] = quality_prompt_name
    if quality_prompt_hash:
        data["quality_prompt_hash"] = quality_prompt_hash
    if prompt_stack_tokens is not None:
        data["prompt_stack_tokens"] = prompt_stack_tokens
    return data


def update_trace_quality(
    run_id: str,
    *,
    quality_score: float | None,
    user_feedback: str | None = None,
) -> bool:
    """Update root trace quality metadata after a post-run evaluator finishes."""
    from database import SessionLocal, Trace

    db = SessionLocal()
    try:
        trace = db.query(Trace).filter(Trace.run_id == run_id).first()
        if not trace:
            return False
        trace.quality_score = quality_score
        if user_feedback is not None:
            trace.user_feedback = user_feedback
        db.commit()
        return True
    finally:
        db.close()
