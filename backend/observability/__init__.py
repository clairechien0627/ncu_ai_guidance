"""Observability integration helpers.

Langfuse is the sole observability backend.  It is initialized via
``auth_check_langfuse()`` in ``main.py`` and uses the official LangChain
callback handler together with the SDK-supported
``metadata["langfuse_prompt"]`` registration path.
"""

from __future__ import annotations

import logging
import os
import json
from langchain_core.callbacks import BaseCallbackHandler

from config import settings
from prompting.registry import get_langfuse_obj

logger = logging.getLogger(__name__)


def _get_langfuse_base_url() -> str | None:
    return (
        os.getenv("LANGFUSE_BASE_URL")
        or settings.langfuse_base_url
        or settings.langfuse_host
    )


def _build_langfuse_handler():
    from langfuse.langchain import CallbackHandler
    from agents.request_context import get_user_id

    uid = get_user_id()
    return CallbackHandler(user_id=uid) if uid else CallbackHandler()


def configure_langfuse_environment() -> None:
    """Normalize Langfuse SDK environment variables for self-hosted setups."""
    if not settings.langfuse_enabled:
        return
    base_url = _get_langfuse_base_url()
    if base_url:
        os.environ.setdefault("LANGFUSE_BASE_URL", base_url)


def initialize_langfuse_client():
    """Create the Langfuse client.

    ``should_export_span=is_langfuse_span`` limits exports to spans that were
    explicitly created via the Langfuse SDK (``@observe``, callback handlers,
    etc.), keeping traces clean and avoiding re-export of raw LangChain spans.
    """
    configure_langfuse_environment()
    if not langfuse_is_configured():
        return None
    try:
        from langfuse import Langfuse, is_langfuse_span

        return Langfuse(
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=_get_langfuse_base_url(),
            should_export_span=is_langfuse_span,
        )
    except Exception as exc:
        logger.warning("Langfuse client initialization failed: %s", exc)
        return None


def langfuse_is_configured() -> bool:
    return bool(
        settings.langfuse_enabled
        and settings.langfuse_public_key.get_secret_value()
        and settings.langfuse_secret_key.get_secret_value()
    )


def auth_check_langfuse() -> None:
    """Log Langfuse auth status during startup without breaking the app."""
    if not langfuse_is_configured():
        logger.info("Langfuse disabled or missing credentials.")
        return
    try:
        client = initialize_langfuse_client()
        if client is None:
            return
        auth_check = getattr(client, "auth_check", None)
        if callable(auth_check):
            ok = auth_check()
            logger.info("Langfuse auth check: %s", "ok" if ok else "failed")
        else:
            logger.info("Langfuse configured; auth_check unavailable in installed SDK.")
    except Exception as exc:
        logger.warning("Langfuse auth check failed: %s", exc)


def langfuse_callbacks_for_prompt(prompt_name: str | None) -> list[BaseCallbackHandler]:
    """Return Langfuse callbacks, or an empty list if disabled."""
    configure_langfuse_environment()
    if not langfuse_is_configured():
        return []
    try:
        return [_build_langfuse_handler()]
    except Exception as exc:
        logger.warning("Langfuse callbacks unavailable: %s", exc)
        return []


def langfuse_callbacks_from_metadata(metadata: dict | None) -> list[BaseCallbackHandler]:
    prompt_name = metadata.get("prompt_name") if isinstance(metadata, dict) else None
    return langfuse_callbacks_for_prompt(prompt_name if isinstance(prompt_name, str) else None)


def langfuse_prompt_metadata(prompt_name: str | None) -> dict:
    """Return metadata used by Langfuse CallbackHandler to link prompts.

    The Langfuse LangChain integration registers ``metadata["langfuse_prompt"]``
    on prompt-template/chain runs and attaches it to the following generation.
    """
    configure_langfuse_environment()
    if not prompt_name or not langfuse_is_configured():
        return {}
    prompt_obj = get_langfuse_obj(prompt_name)
    return {"langfuse_prompt": prompt_obj} if prompt_obj is not None else {}


def langfuse_prompt_metadata_from_metadata(metadata: dict | None) -> dict:
    prompt_name = metadata.get("prompt_name") if isinstance(metadata, dict) else None
    return langfuse_prompt_metadata(prompt_name if isinstance(prompt_name, str) else None)


def langfuse_config_for_prompt(prompt_name: str | None) -> dict:
    metadata = langfuse_prompt_metadata(prompt_name)
    callbacks = langfuse_callbacks_for_prompt(prompt_name)
    config: dict = {}
    if metadata:
        config["metadata"] = metadata
    if callbacks:
        config["callbacks"] = callbacks
    return config


def update_current_observation_io(*, input=None, output=None, metadata: dict | None = None) -> None:
    """Update the active Langfuse observation with compact, curated IO."""
    configure_langfuse_environment()
    if not langfuse_is_configured():
        return
    try:
        from langfuse import get_client

        get_client().update_current_span(input=input, output=output, metadata=metadata)
    except Exception as exc:
        logger.debug("Langfuse current observation update failed: %s", exc)


def generation_prompt_metadata(metadata: dict | None = None, *, prompt_name: str | None = None) -> dict:
    """Build compact prompt metadata for a Langfuse generation observation."""
    metadata = metadata or {}
    prompt = metadata.get("primary_prompt_json")
    if isinstance(prompt, str):
        try:
            prompt = json.loads(prompt)
        except Exception:
            pass
    if not isinstance(prompt, dict):
        prompt = {
            "name": prompt_name or metadata.get("prompt_name"),
            "version": metadata.get("prompt_version"),
        }
    stack_json = metadata.get("prompt_stack_json")
    if isinstance(stack_json, str):
        try:
            stack_json = json.loads(stack_json)
        except Exception:
            pass
    return {
        "prompt_stack_name": metadata.get("prompt_stack_name"),
        "primary_prompt": prompt,
        "prompt_stack_json": stack_json,
        "agent_name": metadata.get("agent_name"),
        "task_type": metadata.get("task_type"),
        "route_intent": metadata.get("route_intent"),
    }


def _serialize_generation_io(value):
    if isinstance(value, list):
        return [_serialize_generation_io(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_generation_io(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            return str(value)
    if hasattr(value, "type") and hasattr(value, "content"):
        return {
            "type": getattr(value, "type", type(value).__name__),
            "content": getattr(value, "content", ""),
        }
    return value


async def ainvoke_traced_generation(
    runnable,
    input_value,
    *,
    prompt_name: str,
    name: str = "AzureChatOpenAI",
    metadata: dict | None = None,
):
    """Invoke a LangChain runnable as one semantic Langfuse generation.

    This avoids exposing internal LCEL nodes such as RunnableSequence and
    RunnableLambda when the surrounding application already has a meaningful
    chain/agent observation.
    """
    configure_langfuse_environment()
    if not langfuse_is_configured():
        return await runnable.ainvoke(input_value)

    try:
        from langfuse import get_client

        langfuse = get_client()
        prompt_obj = get_langfuse_obj(prompt_name)
        observation = langfuse.start_as_current_observation(
            as_type="generation",
            name=name,
            input=_serialize_generation_io(input_value),
            model=settings.azure_chat_deployment,
            prompt=prompt_obj,
            metadata=generation_prompt_metadata(metadata, prompt_name=prompt_name),
        )
    except Exception as exc:
        logger.warning("Langfuse manual generation tracing setup failed for %s: %s", prompt_name, exc)
        return await runnable.ainvoke(input_value)

    with observation as generation:
        result = await runnable.ainvoke(input_value)
        try:
            generation.update(
                input=_serialize_generation_io(input_value),
                output=_serialize_generation_io(result),
                model=settings.azure_chat_deployment,
                metadata=generation_prompt_metadata(metadata, prompt_name=prompt_name),
            )
        except Exception as exc:
            logger.warning("Langfuse manual generation output update failed for %s: %s", prompt_name, exc)
        return result
