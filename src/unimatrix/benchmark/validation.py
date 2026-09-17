"""Validate complete bindings before creating files or opening model clients."""

import re
from urllib.parse import urlparse

from ..policies.scripted import BASELINES
from .fingerprints import runtime_fingerprint


def validate_policy(config):
    if isinstance(config, str):
        if config not in BASELINES:
            raise ValueError("unknown_scripted_policy")
        return
    required = {"model", "snapshot", "endpoint", "budget_track"}
    allowed = required | {
        "context_tokens",
        "context_bytes_verified",
        "temperature",
        "seed",
        "api_key_env",
        "api_key_file",
        "input_price_per_million",
        "output_price_per_million",
        "max_input_tokens",
        "personality",
        "system_prompt",
    }
    if not isinstance(config, dict) or not required <= config.keys() or set(config) - allowed:
        raise ValueError("invalid_model_configuration")
    if any(
        not isinstance(config[k], str) or not config[k].strip()
        for k in ("model", "snapshot", "endpoint")
    ):
        raise ValueError("model_snapshot_and_endpoint_must_be_strings")
    for key, limit in (("personality", 120), ("system_prompt", 8000)):
        if key in config and (
            not isinstance(config[key], str) or not config[key].strip() or len(config[key]) > limit
        ):
            raise ValueError("invalid_" + key)
    if config.get("personality") and not config.get("system_prompt"):
        raise ValueError("personality_requires_system_prompt")
    if config.get("api_key_env") and config.get("api_key_file"):
        raise ValueError("choose_one_api_key_source")
    for key in ("api_key_env", "api_key_file"):
        if key in config and (not isinstance(config[key], str) or not config[key].strip()):
            raise ValueError("invalid_api_key_source")
    if not config["model"] or not config["snapshot"]:
        raise ValueError("immutable_model_snapshot_required")
    url = urlparse(config["endpoint"])
    try:
        url.port
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
    elif (
        type(config.get("context_bytes_verified")) is not int
        or config["context_bytes_verified"] < 24000
    ):
        raise ValueError("verified_context_too_small")
    if config["budget_track"] not in {"accounted_compute", "opaque_compute"}:
        raise ValueError("unknown_compute_track")


def validate_manifest(manifest):
    fields = {
        "mode",
        "domain",
        "level",
        "seed",
        "role",
        "replicate",
        "population",
        "slots",
        "focal_slot",
        "policies",
        "ticks",
        "release_status",
        "suite_hash",
        "engine_version",
        "runtime_fingerprint",
        "run_id",
        "initial_population",
        "generation_tokens_per_tick",
        "ablations",
        "release_evidence_hash",
    }
    if set(manifest) - fields:
        raise ValueError("unknown_manifest_fields")
    if any(type(manifest.get(key)) is not int for key in ["level", "seed", "replicate"]):
        raise ValueError("integer_instance_axes_required")
    if manifest.get("role") not in {"advantaged", "disadvantaged"}:
        raise ValueError("invalid_role")
    from ..core.ids import digest

    if manifest.get("run_id") != digest({k: v for k, v in manifest.items() if k != "run_id"})[:24]:
        raise ValueError("manifest_identity_mismatch")
    mode = manifest.get("mode")
    if mode not in {"core", "society", "explore"}:
        raise ValueError("explicit_v3_mode_required")
    domain = manifest.get("domain")
    if domain not in {f"D{i}" for i in range(1, 9)} | {"social"}:
        raise ValueError("invalid_scenario_cell")
    if mode == "core" and domain == "social":
        raise ValueError("social_world_has_no_core_score")
    if type(manifest.get("ticks")) is not int or not 1 <= manifest["ticks"] <= 1000000:
        raise ValueError("invalid_horizon")
    if domain != "social" and (manifest["ticks"] != 240 or manifest.get("level") != 2):
        raise ValueError("invalid_episode_shape")
    slots = manifest.get("slots", [])
    if (
        not 2 <= len(slots) <= 128
        or len(set(slots)) != len(slots)
        or set(manifest.get("policies", {})) != set(slots)
        or manifest.get("focal_slot") not in slots
    ):
        raise ValueError("all_slots_must_be_bound")
    if domain != "social" and not 8 <= len(slots) <= 128:
        raise ValueError("benchmark_requires_7_to_127_peers")
    if mode != "core" and not 2 <= manifest.get("initial_population", 0) <= len(slots):
        raise ValueError("invalid_initial_population")
    if not re.fullmatch("[a-f0-9]{24}", manifest.get("run_id", "")):
        raise ValueError("invalid_run_id")
    if manifest.get("runtime_fingerprint") != runtime_fingerprint():
        raise ValueError("runtime_changed_create_new_manifest")
    for config in manifest["policies"].values():
        validate_policy(config)
    peers = [manifest["policies"][s] for s in slots if s != manifest["focal_slot"]]
    llms = sum(isinstance(c, dict) for c in peers)
    if (manifest.get("population") == "P0" and llms != 0) or (
        manifest.get("population") == "P1" and llms < 1
    ):
        raise ValueError("pool_binding_mismatch")
    if manifest.get("population") not in {"P0", "P1", "society", "explore"}:
        raise ValueError("unknown_pool")
    if manifest.get("release_status") not in {"draft", "validated"}:
        raise ValueError("uncertified_implementation")
