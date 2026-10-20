"""Bounded inference checks using the same transport and credentials as evaluations."""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx

from .transport import ContextConfigurationError, InfrastructureFailure, ModelClient


async def check_model(config, *, timeout=None):
    started = monotonic()
    thinking = config.get("openrouter", {}).get("reasoning", {}).get("enabled") is True
    diagnostic_tokens = min(config.get("max_output_tokens", 8192), 8192) if thinking else 128
    if timeout is None:
        timeout = 120 if thinking else 30
    result = {
        "model": config["model"],
        "protocol": "LM Studio SDK" if "context_tokens" in config else "OpenAI-compatible",
        "credentials": "configured"
        if config.get("api_key_env") or config.get("api_key_file")
        else "none",
    }
    policy = ModelClient(config)
    stage = "credentials"
    try:
        if config.get("api_key_env"):
            token = os.environ[config["api_key_env"]]
        elif config.get("api_key_file"):
            token = json.loads(Path(config["api_key_file"]).read_text(encoding="utf8"))["api_key"]
        else:
            token = None
        if token is not None and (not isinstance(token, str) or not token.strip()):
            raise ValueError("empty_credentials")
        stage = "inference"
        async with asyncio.timeout(timeout):
            raw, metadata = await policy.decide(
                {
                    "instruction": 'Reply with the JSON object {"ok":true}. This is a connection test.'
                },
                diagnostic_tokens=diagnostic_tokens,
            )
            if metadata.get("finish_reason") == "length":
                raise ValueError("diagnostic_output_limit")
            if not isinstance(json.loads(raw), dict):
                raise ValueError("non_object")
        result.update(
            ok=True,
            code="ready",
            message="Model returned a JSON object. The configured inference transport works.",
            hint="Configured credentials were accepted."
            if result["credentials"] == "configured"
            else "No API key was sent. This does not verify authentication enforcement.",
            generated_tokens=metadata.get("generated_tokens"),
        )
    except Exception as error:

        detail = str(error).lower()
        cause = error.__cause__
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        if isinstance(error, InfrastructureFailure):
            status = error.http_status
        if isinstance(error, (TimeoutError, httpx.TimeoutException)) or isinstance(
            cause, httpx.TimeoutException
        ):
            code, message, hint = (
                "timeout",
                "Inference did not complete within the time limit.",
                "Check model loading, server load and network; then retry.",
            )
        elif stage == "credentials":
            code, message, hint = (
                "credentials",
                "Cannot read the configured credentials.",
                "Check the API key environment variable or credentials file on this application server.",
            )
        elif status in (401, 403) or any(
            x in detail
            for x in (
                "unauthorized",
                "unauthenticated",
                "authentication",
                "invalid api",
                "401",
                "403",
            )
        ):
            code, message, hint = (
                "authentication",
                "Server rejected authentication or access.",
                "Check the saved API key and permission to use this model.",
            )
        elif isinstance(error, ContextConfigurationError) and detail.startswith("reasoning_"):
            code, message, hint = (
                "configuration",
                "The requested reasoning level could not be verified for this model.",
                "Choose a level advertised for this exact model, or use Provider default. "
                "Explicit reasoning control requires LM Studio 0.4.8 or newer.",
            )
        elif isinstance(error, ContextConfigurationError):
            code, message, hint = (
                "configuration",
                "Loaded context or seed differs from the saved configuration.",
                "Reload the model with the saved context and seed, or edit Model settings.",
            )
        elif status == 404 or "model not found" in detail or "no model" in detail:
            code, message, hint = (
                "not_found",
                "Model or inference route was not found.",
                "Check the exact model identifier, address and backend.",
            )
        elif "429" in detail:
            code, message, hint = (
                "rate_limit",
                "Server rate limit reached (HTTP 429).",
                "Wait and retry; check provider quota.",
            )
            if "upstream_provider_shared_pool" in detail:
                hint = ("The upstream provider's shared capacity is rate limited. Wait, choose "
                        "another provider, or configure your own provider key in OpenRouter.")
        elif "provider_http_5" in detail:
            code, message, hint = (
                "server",
                "Inference server returned an internal error.",
                "Inspect server logs and model availability.",
            )
        elif "missing_generation_accounting" in detail:
            code, message, hint = (
                "accounting",
                "Server responded without generation-token accounting.",
                "Use a provider with token usage reporting or select opaque compute.",
            )
        elif "diagnostic_output_limit" in detail:
            code, message, hint = (
                "output_limit",
                "The connection test exhausted its output budget before completing the answer.",
                f"The test allows {diagnostic_tokens} tokens including reasoning; this does not establish a usable JSON response.",
            )
        elif isinstance(
            error,
            (json.JSONDecodeError, ValueError, IndexError, KeyError, TypeError, AttributeError),
        ):
            code, message, hint = (
                "response",
                "Server response was not a usable JSON object.",
                f"Check structured-output support. The connection test allows {diagnostic_tokens} output tokens including reasoning.",
            )
        elif (
            isinstance(cause, httpx.RequestError)
            or isinstance(error, httpx.RequestError)
            or any(
                term in detail for term in ("econnrefused", "connection", "connect to", "websocket")
            )
        ):
            code, message, hint = (
                "connection",
                "Cannot connect to the inference server.",
                "Check address, port, TLS, network and whether the server is running.",
            )
        else:
            code, message, hint = (
                "provider",
                "The inference request failed.",
                "Check server logs, model identifier and structured-output support.",
            )
        result.update(ok=False, code=code, message=message, hint=hint)
        if isinstance(error, InfrastructureFailure) and error.http_status:
            result["provider_detail"] = str(error)
        if status:
            result["http_status"] = status
    finally:
        await policy.close()
    result.update(
        latency_ms=round((monotonic() - started) * 1000), checked_at=datetime.now(UTC).isoformat()
    )
    return result
