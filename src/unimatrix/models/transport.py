"""The inference transport: one observable model call per decision, over an OpenAI-compatible
HTTP API or the LM Studio SDK (verified context, managed instances, reasoning effort).
No repair or reflection policy: what the model answers is what gets recorded."""

import asyncio
import json
import logging
import math
import os
import random
import time
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import lmstudio

from ..core.ids import canonical, digest
from .managed import ManagedModel

SAMPLING_FIELDS = ("top_p", "top_k", "presence_penalty", "frequency_penalty", "repeat_penalty")
# Local reasoning models can think for tens of thousands of tokens on one week: the deadline
# only has to catch a stuck provider, never a slow but healthy one.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 8 * 60 * 60
HTTP_RETRY_BACKOFF = (15, 30, 60, 120, 240, 300)
HTTP_RETRY_BUDGET_SECONDS = 15 * 60
logger = logging.getLogger(__name__)


def retry_after_seconds(value):
    """Accept Retry-After seconds or an HTTP date; never shorten a valid server delay."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def requires_http_generation(config):
    return (config.get("structured_output") in {"decision_schema", "compact_decision"}
            or not config.get("constrain_output", True) or "reasoning_effort" in config
            or any(k in config for k in SAMPLING_FIELDS))


class InfrastructureFailure(RuntimeError):
    """The provider failed (network, HTTP 5xx, timeout); `retryable` says whether to retry."""

    def __init__(self, *args, retryable=True, http_status=None):
        super().__init__(*args)
        self.retryable = retryable
        self.http_status = http_status


class ContextConfigurationError(ValueError):
    pass


class ModelClient:
    """Subclasses define the prompt (`system_prompt`, `render_prompt`) and `response_schema`."""

    def __init__(self, config, client=None, instance=None):
        """`instance` shares one loaded model between clients (parallel seeds of a run); the client
        then leaves loading and unloading to whoever created it."""
        from .config import validate_model

        validate_model(config)
        self.config = deepcopy(config)
        self._instance = instance if instance is not None else ManagedModel(config)
        self._owns_instance = instance is None
        self.instance_id = self._instance.identifier
        self.fingerprint = digest(config)
        self.request_timeout_seconds = config.get("request_timeout_seconds",
                                                  DEFAULT_REQUEST_TIMEOUT_SECONDS)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.request_timeout_seconds), follow_redirects=False
        )

    def sampling_requested(self):
        """Explicit wire settings only; unspecified controls remain provider defaults."""
        settings = {k: self.config[k] for k in (*SAMPLING_FIELDS, "seed") if k in self.config}
        if self.config.get("temperature", 0) is not None:
            settings["temperature"] = self.config.get("temperature", 0)
        return settings

    def system_prompt(self):
        persona = self.config.get("system_prompt")
        return f"Agent personality:\n{persona}" if persona else ""

    def render_prompt(self, observation):
        return canonical(observation)

    def response_schema(self, observation):
        return {"type": "object"}

    async def decide(self, observation, *, diagnostic_tokens=None):
        """One model call: returns the answer text and its usage/provenance record."""
        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                if "context_tokens" in self.config:
                    return await self._decide_with_context(observation, diagnostic_tokens=diagnostic_tokens)
                if not self._instance.ready:
                    # A server that loads the model on its first request (LM Studio's just-in-time
                    # loading) must see one request before the parallel ones, or it loads it twice.
                    async with self._instance.load_lock:
                        if not self._instance.ready:
                            result = await self._decide_http(observation, diagnostic_tokens=diagnostic_tokens)
                            self._instance.ready = True
                            return result
                return await self._decide_http(observation, diagnostic_tokens=diagnostic_tokens)
        except TimeoutError as error:
            raise InfrastructureFailure("provider_decision_timeout", retryable=False) from error

    async def _decide_http(self, observation, *, diagnostic_tokens=None, model_identifier=None):
        headers = {}
        if self.config.get("api_key_env"):
            headers["Authorization"] = "Bearer " + os.environ[self.config["api_key_env"]]
        elif self.config.get("api_key_file"):
            key = json.loads(Path(self.config["api_key_file"]).read_text(encoding="utf8"))["api_key"]
            headers["Authorization"] = "Bearer " + key
        body = dict(
            model=model_identifier or self.config["model"],
            messages=(
                [dict(role="system", content=self.system_prompt())] if self.system_prompt() else []
            )
            + [dict(role="user", content=self.render_prompt(observation))],
            **self.sampling_requested(),

            response_format={
                "type": "json_schema",
                "json_schema": {"name": "json_object", "schema": self.response_schema(observation)},
            },
        )
        if not self.config.get("constrain_output", True):
            body.pop("response_format")
        output_limit = diagnostic_tokens or self.config.get("max_output_tokens")
        if output_limit is not None:
            body["max_tokens"] = output_limit
        if "seed" in self.config:
            body["seed"] = self.config["seed"]
        if "reasoning_effort" in self.config:
            body["reasoning_effort"] = self.config["reasoning_effort"]
        body.update(deepcopy(self.config.get("openrouter", {})))
        response, retries, retry_wait = await self._post_http(
            body, headers, retry_transient=diagnostic_tokens is None)
        data = response.json()
        usage = data.get("usage", {})
        generated = usage.get("completion_tokens")
        if self.config["budget_track"] == "accounted_compute" and (
            type(generated) is not int or generated < 0
        ):
            raise InfrastructureFailure("missing_generation_accounting")
        choice = data["choices"][0]
        raw = choice["message"].get("content") or ""
        reasoning = choice["message"].get("reasoning_content", choice["message"].get("reasoning", ""))
        answer = raw
        tags = self.config.get("reasoning_delimiters")
        if tags and not reasoning:
            stripped = raw.lstrip()

            if stripped.startswith(tags[0]) and tags[1] in stripped[len(tags[0]):]:
                thought, answer = stripped[len(tags[0]):].split(tags[1], 1)
                reasoning = thought
                answer = answer.strip()
        return answer, dict(
            sampling_requested=self.sampling_requested(),
            provider_response=raw,
            constrain_output=self.config.get("constrain_output", True),
            output_adapter=self.config.get("structured_output", "json_object"),
            system_prompt=self.system_prompt(),
            purpose="decision",
            generated_tokens=generated,
            input_tokens=usage.get("prompt_tokens"),
            reasoning_tokens=usage.get("completion_tokens_details", {}).get("reasoning_tokens"),
            reasoning=reasoning,
            provider_usage=usage,
            finish_reason=choice.get("finish_reason"),
            response_hash=digest(data),
            provider_model=data.get("model"),
            provider_fingerprint=data.get("system_fingerprint"),
            provider_name=data.get("provider"),
            provider_request_id=data.get("id"),
            provider_routing_requested=body.get("provider"),
            reasoning_requested=body.get("reasoning"),
            provider_http_retries=retries,
            provider_http_retry_wait_seconds=round(retry_wait, 3),
        )

    async def _post_http(self, body, headers, *, retry_transient):
        started, retry_wait = time.monotonic(), 0.0
        try:
            for attempt in range(len(HTTP_RETRY_BACKOFF) + 1):
                response = await self.client.post(
                    self.config["endpoint"].rstrip("/") + "/v1/chat/completions",
                    json=body, headers=headers,
                )
                if not response.is_error:
                    response.raise_for_status()
                    return response, attempt, retry_wait
                detail = response.text
                authorization = headers.get("Authorization")
                if authorization:
                    detail = detail.replace(authorization.removeprefix("Bearer "), "[redacted]")
                message = f"provider_http_{response.status_code}: {detail[:2000]}"
                if response.status_code not in (429, 503):
                    if response.status_code >= 500:
                        raise InfrastructureFailure(message, http_status=response.status_code)
                    raise httpx.HTTPStatusError(message, request=response.request, response=response)
                server_delay = retry_after_seconds(response.headers.get("Retry-After"))
                if server_delay is not None:
                    message += f"; Retry-After: {server_delay:g}s"
                error = InfrastructureFailure(message, http_status=response.status_code)
                try:
                    payload = response.json()
                    provider_error = payload.get("error", {}) if isinstance(payload, dict) else {}
                except ValueError:
                    provider_error = {}
                if isinstance(provider_error, dict) and any(
                    provider_error.get(field) in {
                        "insufficient_quota", "credit_balance_exhausted",
                        "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
                        "organization_usage_limit_exceeded",
                    } for field in ("type", "code")
                    if isinstance(provider_error.get(field), str)
                ):
                    error.retryable = False
                    raise error
                # Health checks must report the provider error within their short deadline.
                if not retry_transient:
                    raise error
                if attempt == len(HTTP_RETRY_BACKOFF):
                    raise InfrastructureFailure(
                        f"{message}; HTTP retry attempts exhausted", retryable=False,
                        http_status=response.status_code) from None
                delay = max(HTTP_RETRY_BACKOFF[attempt], server_delay or 0)
                delay += random.uniform(0, min(5, delay * 0.1))
                if time.monotonic() - started + delay > HTTP_RETRY_BUDGET_SECONDS:
                    raise InfrastructureFailure(
                        f"{message}; HTTP retry time budget exhausted", retryable=False,
                        http_status=response.status_code) from None
                logger.warning("%s; retry %s in %.1fs", message, attempt + 1, delay)
                await asyncio.sleep(delay)
                retry_wait += delay
        except httpx.RequestError as error:
            raise InfrastructureFailure(
                type(error).__name__, retryable=not isinstance(error, httpx.TimeoutException)
            ) from error

    async def _verify_reasoning_effort(self, token):
        response = await self.client.get(
            self.config["endpoint"].rstrip("/") + "/api/v1/models",
            headers={"Authorization": "Bearer " + token} if token else {},
        )
        if response.is_error:
            raise InfrastructureFailure(f"reasoning_capability_http_{response.status_code}")
        try:
            models = response.json()["models"]
            if not isinstance(models, list):
                raise ValueError("models_not_list")
            matches = [m for m in models if isinstance(m, dict)
                       and m.get("key") == self.config["model"]]
            if len(matches) != 1:
                raise ValueError("model_key_not_unique")
            reasoning = matches[0]["capabilities"]["reasoning"]
            options, default = reasoning["allowed_options"], reasoning["default"]
            if (not isinstance(options, list) or not options
                    or any(not isinstance(o, str) for o in options)
                    or len(set(options)) != len(options)
                    or not isinstance(default, str) or default not in options):
                raise ValueError("invalid_reasoning_capabilities")
        except (KeyError, TypeError, ValueError):
            raise ContextConfigurationError("reasoning_capability_not_verified") from None
        effort = self.config["reasoning_effort"]
        if effort not in options:
            raise ContextConfigurationError("reasoning_effort_not_supported:" + effort)
        return dict(requested=effort, model_key=self.config["model"],
                    allowed_options=options, default=default,
                    capabilities_sha256=digest(reasoning))

    async def _decide_with_context(self, observation, *, diagnostic_tokens=None):
        context = self.config["context_tokens"]
        token = ""
        if self.config.get("api_key_env"):
            token = os.environ[self.config["api_key_env"]]
        elif self.config.get("api_key_file"):
            token = json.loads(Path(self.config["api_key_file"]).read_text(encoding="utf8"))["api_key"]
        load_config = {"contextLength": context}
        if "seed" in self.config:
            load_config["seed"] = self.config["seed"]
        reasoning_tokens = 0
        answer = []
        reasoning = []

        def fragment_received(fragment):
            nonlocal reasoning_tokens
            if fragment.reasoning_type in {"reasoning", "reasoningStartTag", "reasoningEndTag"}:
                reasoning_tokens += fragment.tokens_count
                reasoning.append(fragment.content)
            else:
                answer.append(fragment.content)

        try:
            reasoning_config = (await self._verify_reasoning_effort(token)
                                if "reasoning_effort" in self.config else None)

            async with lmstudio.AsyncClient(
                api_host=urlparse(self.config["endpoint"]).netloc,
                api_token=token,
            ) as client:
                # Serialized so that parallel seeds sharing the instance load it at most once;
                # their predictions below run concurrently on the same loaded weights.
                async with self._instance.load_lock:
                    if self.instance_id and not self._instance.loaded:
                        if self._instance.load_failed:
                            raise InfrastructureFailure("managed_instance_load_incomplete", retryable=False)
                        try:
                            model = await client.llm.load_new_instance(
                                self.config["model"], self.instance_id, ttl=120, config=load_config)
                        except asyncio.CancelledError:
                            self._instance.load_failed = True
                            raise
                        except Exception as error:
                            self._instance.load_failed = True
                            raise InfrastructureFailure("managed_instance_load_failed", retryable=False) from error
                        self._instance.loaded = True
                    else:
                        model = await client.llm.model(self.instance_id or self.config["model"], config=load_config,
                                                       **({"ttl": 120} if self.instance_id else {}))
                    self._instance.ready = True
                actual = await model.get_context_length()
                if actual < context:
                    raise ContextConfigurationError(
                        f"model_context_mismatch: requested {context} tokens, loaded {actual}. "
                        "Reload the model in LM Studio with the requested context or edit Model settings."
                    )
                if "seed" in self.config:
                    loaded = await model.get_load_config()
                    if loaded.seed != self.config["seed"]:
                        raise ContextConfigurationError(
                            "model_seed_mismatch: reload the model with the configured seed"
                        )
                prompt = self.render_prompt(observation)
                if self.system_prompt():
                    prompt = lmstudio.Chat(self.system_prompt())
                    prompt.add_user_message(self.render_prompt(observation))
                ordered_schema = requires_http_generation(self.config) or actual != context

                if "max_input_tokens" in self.config or ordered_schema:
                    rendered = await model.apply_prompt_template(prompt)
                    input_count = await model.count_tokens(rendered)
                    if "max_input_tokens" in self.config and input_count > self.config["max_input_tokens"]:
                        raise ContextConfigurationError("input_token_budget_exceeded")
                if ordered_schema:
                    available = context - input_count - 32
                    if available < 1:
                        raise ContextConfigurationError("prompt_exhausts_model_context")
                    output_limit = min(diagnostic_tokens or self.config.get("max_output_tokens", context), available)
                    raw, usage = await self._decide_http(
                        observation, diagnostic_tokens=output_limit, model_identifier=model.identifier)
                    usage.update(context_tokens=context, loaded_context_tokens=actual,
                                 model_instance=model.identifier, protocol="lmstudio_http",
                                 schema_transport=("http_preserve_order" if self.config.get("constrain_output", True)
                                                   else "unconstrained"),
                                 rendered_input_tokens=input_count,
                                 effective_max_output_tokens=output_limit)
                    if reasoning_config is not None:
                        usage["reasoning_configuration"] = reasoning_config
                    return raw, usage
                result = await model.respond(
                    prompt,
                    response_format=self.response_schema(observation),
                    config={

                        "maxTokens": diagnostic_tokens or self.config.get("max_output_tokens", context),
                        "contextOverflowPolicy": "stopAtLimit",
                        "temperature": self.config.get("temperature", 0),
                    },
                    on_prediction_fragment=fragment_received,
                )
        except (ContextConfigurationError, InfrastructureFailure):
            raise
        except Exception as error:
            detail = str(error)
            if token:
                detail = detail.replace(token, "[redacted]")
            raise InfrastructureFailure(f"lmstudio: {detail[:2000]}") from None
        generated = result.stats.predicted_tokens_count
        if self.config["budget_track"] == "accounted_compute" and (
            type(generated) is not int or generated < 0
        ):
            raise InfrastructureFailure("missing_generation_accounting")
        stats = result.stats.to_dict()
        raw = "".join(answer)
        answer = raw
        tags = self.config.get("reasoning_delimiters")
        if tags and not reasoning:
            stripped = raw.lstrip()

            if stripped.startswith(tags[0]) and tags[1] in stripped[len(tags[0]):]:
                thought, answer = stripped[len(tags[0]):].split(tags[1], 1)
                reasoning = thought
                answer = answer.strip()
        return answer, dict(
            sampling_requested=self.sampling_requested(),
            provider_response=raw,
            constrain_output=self.config.get("constrain_output", True),
            output_adapter=self.config.get("structured_output", "json_object"),
            system_prompt=self.system_prompt(),
            purpose="decision",
            generated_tokens=generated,
            input_tokens=result.stats.prompt_tokens_count,
            reasoning_tokens=reasoning_tokens,
            reasoning="".join(reasoning),
            finish_reason=result.stats.stop_reason,
            context_tokens=context,
            loaded_context_tokens=actual,
            model_instance=model.identifier,
            protocol="lmstudio",
            response_hash=digest({"content": result.content, "stats": stats}),
        )

    async def close(self):
        try:
            if self._owns_instance:
                await self._instance.close()
        finally:
            await self.client.aclose()
