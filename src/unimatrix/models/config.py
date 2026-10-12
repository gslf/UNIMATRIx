"""Validate candidate model configurations before any file is written or client opened."""

import math
import re
from urllib.parse import urlparse


def is_model(config):
    """Model-backed configuration: a dict with a `model`."""
    return isinstance(config, dict) and "model" in config


def validate_model(config):
    """A candidate model configuration: provider, snapshot, context and sampling settings."""
    required = {"model", "snapshot", "endpoint", "budget_track"}
    allowed = required | {
        "context_tokens",
        "context_bytes_verified",
        "temperature",
        "top_p",
        "top_k",
        "presence_penalty",
        "frequency_penalty",
        "repeat_penalty",
        "seed",
        "api_key_env",
        "api_key_file",
        "input_price_per_million",
        "output_price_per_million",
        "max_input_tokens",
        "personality",
        "system_prompt",
        "persona",
        "structured_output",
        "request_timeout_seconds",
        "max_output_tokens",
        "model_sha256",
        "backend_revision",
        "quantization",
        "managed_instance",
        "constrain_output",
        "reasoning_delimiters",
        "reasoning_effort",
    }
    if not isinstance(config, dict) or not required <= config.keys() or set(config) - allowed:
        raise ValueError("invalid_model_configuration")
    if any(
        not isinstance(config[k], str) or not config[k].strip()
        for k in ("model", "snapshot", "endpoint")
    ):
        raise ValueError("model_snapshot_and_endpoint_must_be_strings")
    if "managed_instance" in config and (type(config["managed_instance"]) is not bool or "context_tokens" not in config):
        raise ValueError("managed_instance_requires_boolean_and_LM_Studio_context")
    if "constrain_output" in config and type(config["constrain_output"]) is not bool:
        raise ValueError("constrain_output_requires_boolean")
    if "reasoning_effort" in config and (
        not isinstance(config["reasoning_effort"], str)
        or config["reasoning_effort"] not in {"low", "medium", "high"}
        or "context_tokens" not in config
    ):
        raise ValueError("reasoning_effort_requires_low_medium_high_and_LM_Studio_context")
    if "reasoning_delimiters" in config:
        tags = config["reasoning_delimiters"]
        if (not isinstance(tags, list) or len(tags) != 2
                or any(not isinstance(t, str) or not 0 < len(t) <= 64 for t in tags)
                or tags[0] == tags[1] or config.get("constrain_output", True)):
            raise ValueError("reasoning_delimiters_require_two_distinct_tags_and_unconstrained_output")
    integer_fields = {"seed": (0, 2**32 - 1), "top_k": (0, 2**31 - 1), "max_input_tokens": (1, 2**31 - 1),
                      "max_output_tokens": (1, 2**31 - 1)}
    for key, (low, high) in integer_fields.items():
        if key in config and (type(config[key]) is not int or not low <= config[key] <= high):
            raise ValueError("invalid_" + key)
    number_fields = {"temperature": (0, 2), "top_p": (0, 1),
                     "presence_penalty": (-2, 2), "frequency_penalty": (-2, 2),
                     "repeat_penalty": (0, float("inf")), "request_timeout_seconds": (0.01, 86400),
                     "input_price_per_million": (0, 1e9), "output_price_per_million": (0, 1e9)}
    for key, (low, high) in number_fields.items():
        if key in config and (type(config[key]) not in (float, int)
                              or not math.isfinite(config[key]) or not low <= config[key] <= high):
            raise ValueError("invalid_" + key)
    if config.get("repeat_penalty") == 0:
        raise ValueError("invalid_repeat_penalty")
    if "model_sha256" in config and (not isinstance(config["model_sha256"], str)
                                      or not re.fullmatch(r"[a-f0-9]{64}", config["model_sha256"])):
        raise ValueError("invalid_model_sha256")
    for key in ("backend_revision", "quantization"):
        if key in config and (not isinstance(config[key], str)
                              or not 0 < len(config[key].strip()) <= 200):
            raise ValueError("invalid_" + key)
    if config.get("structured_output", "json_object") not in {"json_object", "decision_schema", "compact_decision"}:
        raise ValueError("invalid_structured_output")
    for key, limit in (("personality", 120), ("system_prompt", 8000)):
        if key in config and (
            not isinstance(config[key], str) or not config[key].strip() or len(config[key]) > limit
        ):
            raise ValueError("invalid_" + key)
    if config.get("personality") and not config.get("system_prompt"):
        raise ValueError("personality_requires_system_prompt")
    persona = config.get("persona")
    if persona is not None and (
        not isinstance(persona, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", persona)
        or not config.get("system_prompt")
    ):
        raise ValueError("invalid_persona")
    if config.get("api_key_env") and config.get("api_key_file"):
        raise ValueError("choose_one_api_key_source")
    for key in ("api_key_env", "api_key_file"):
        if key in config and (not isinstance(config[key], str) or not config[key].strip()):
            raise ValueError("invalid_api_key_source")
    if not config["model"] or not config["snapshot"]:
        raise ValueError("immutable_model_snapshot_required")
    url = urlparse(config["endpoint"])
    try:
        _ = url.port
    except ValueError as error:
        raise ValueError("invalid_endpoint_port") from error
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("endpoint_must_not_contain_credentials_or_query")
    if "context_tokens" in config:
        if type(config["context_tokens"]) is not int or config["context_tokens"] < 1:
            raise ValueError("context_tokens_must_be_a_positive_integer")
        if url.scheme != "http" or url.path not in {"", "/"}:
            raise ValueError("LM_Studio_context_control_requires_http_host_and_port_without_path")
    elif "max_input_tokens" in config:
        raise ValueError("max_input_tokens_requires_LM_Studio_context_control_for_tokenization")
    elif (
        type(config.get("context_bytes_verified")) is not int
        or config["context_bytes_verified"] < 24000
    ):
        raise ValueError("verified_context_too_small")
    if config["budget_track"] not in {"accounted_compute", "opaque_compute"}:
        raise ValueError("unknown_compute_track")
