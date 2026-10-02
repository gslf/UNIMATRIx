"""One observable call per decision; no repair or reflection policy."""

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import httpx
import lmstudio

from ..actions.availability import implemented_verbs
from ..actions.schemas import SCHEMA, empty
from ..benchmark.runner import InfrastructureFailure
from ..core.ids import canonical, digest
from ..core.visibility import DECISION_INSTRUCTION, PROTOCOL
from .managed_models import ManagedModel, model_pool

SAMPLING_FIELDS = ("top_p", "top_k", "presence_penalty", "frequency_penalty", "repeat_penalty")


def requires_http_generation(config):
    return (config.get("structured_output") in {"decision_schema", "compact_decision"}
            or not config.get("constrain_output", True) or "reasoning_effort" in config
            or any(k in config for k in SAMPLING_FIELDS))


class ContextConfigurationError(ValueError):
    pass


class LLMPolicy:
    def __init__(self, config, client=None):
        from ..benchmark.validation import validate_policy

        validate_policy(config)
        self.config = deepcopy(config)



        pool = (model_pool.get() if config.get("managed_instance") and "seed" in config
                and requires_http_generation(config) else None)
        self._instance = pool.acquire(config) if pool is not None else ManagedModel(config)
        self._owns_instance = pool is None
        self.instance_id = self._instance.identifier
        self._instance_load_lock = self._instance.load_lock
        self.fingerprint = digest(config)
        self.request_timeout_seconds = config.get("request_timeout_seconds", 120)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.request_timeout_seconds), follow_redirects=False
        )

    def sampling_requested(self):
        """Explicit wire settings only; unspecified controls remain provider defaults."""
        return dict(temperature=self.config.get("temperature", 0),
                    **{k: self.config[k] for k in (*SAMPLING_FIELDS, "seed") if k in self.config})

    def system_prompt(self):
        sections = [
            (title, self.config.get(key))
            for title, key in (
                ("Agent personality", "system_prompt"),
                ("Objectives", "goal"),
                ("Private information", "briefing"),
            )
        ]
        text = "".join(f"\n\n{title}:\n{body}" for title, body in sections if body)
        return self.output_protocol() + text

    def output_protocol(self, *, concise=False):
        if self.config.get("structured_output") != "compact_decision":
            return PROTOCOL
        compact = (
            "Return one JSON decision object using only operations, messages, forecasts, "
            "private_note or memory_query. Omit unused fields. The server supplies "
            "protocol, tick and agent_id. An empty object means no action. "
            "Each operation must have a verb field. Do not echo the interface examples."
        )
        if concise:
            compact = (
                "Return one compact JSON decision object. "
                "The server supplies protocol, tick and agent_id."
            )
        return PROTOCOL.replace(DECISION_INSTRUCTION, compact)

    def render_prompt(self, observation):
        """Show the selected response contract without changing recorded world data."""
        if self.config.get("structured_output") != "compact_decision":
            return self.prompt(observation)
        packet = deepcopy(observation)
        protocol = packet.get("protocol")
        if isinstance(protocol, str) and protocol.startswith(PROTOCOL):
            packet["protocol"] = self.output_protocol(concise=True) + protocol[len(PROTOCOL):]
        interface = packet.get("interface")
        if isinstance(interface, dict):
            example = interface.get("envelope")
            if isinstance(example, dict):
                interface["envelope"] = {
                    k: v for k, v in example.items()
                    if k not in {"protocol", "tick", "agent_id"}
                }
            if isinstance(interface.get("notes"), str):
                interface["notes"] = interface["notes"].replace(
                    "Use the current tick and your authenticated slot. ", ""
                )
        return self.prompt(packet)

    def response_schema(self, observation):
        if "tick" not in observation or "agent_id" not in observation:
            return {"type": "object"}
        if self.config.get("structured_output") not in {"decision_schema", "compact_decision"}:
            return {"type": "object"}
        schema = deepcopy(SCHEMA)
        variants = schema["properties"]["operations"]["items"]["oneOf"]
        domain = observation.get("scenario", {}).get("domain")
        supported = implemented_verbs(domain, [v["properties"]["verb"]["const"] for v in variants])
        schema["properties"]["operations"]["items"]["oneOf"] = [
            v for v in variants if v["properties"]["verb"]["const"] in supported
        ]
        if self.config.get("structured_output") == "compact_decision":
            schema["properties"] = {
                k: schema["properties"][k]
                for k in ("operations", "messages", "forecasts", "private_note", "memory_query")
            }
            schema["required"] = []
            return schema
        schema["properties"]["tick"] = {"const": observation["tick"]}
        schema["properties"]["agent_id"] = {"const": observation["agent_id"]}
        return schema

    def expand_response(self, raw, observation):
        if "tick" not in observation or "agent_id" not in observation:
            return raw
        if self.config.get("structured_output") != "compact_decision":
            return raw

        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate_json_key")
                result[key] = value
            return result

        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 6144:
            return raw
        try:
            decision = json.loads(
                raw,
                object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
        except (ValueError, TypeError):
            return raw
        fields = {"operations", "messages", "forecasts", "private_note", "memory_query"}
        if not isinstance(decision, dict) or set(decision) - fields:
            return raw

        return canonical(dict(empty(observation["tick"], observation["agent_id"]), **decision))

    @staticmethod
    def prompt(observation):

        if "interface" not in observation:
            return canonical(observation)
        rest = {k: v for k, v in observation.items() if k != "interface"}
        return '{"interface":' + canonical(observation["interface"]) + "," + canonical(rest)[1:]

    async def decide(self, observation, budget, *, diagnostic_tokens=None):
        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                return await self._decide(observation, budget, diagnostic_tokens=diagnostic_tokens)
        except TimeoutError as error:
            raise InfrastructureFailure("provider_decision_timeout", retryable=False) from error

    async def _decide(self, observation, budget, *, diagnostic_tokens=None):
        if "context_tokens" in self.config:
            return await self._decide_with_context(observation, diagnostic_tokens=diagnostic_tokens)
        return await self._decide_http(observation, diagnostic_tokens=diagnostic_tokens)

    async def _decide_http(self, observation, *, diagnostic_tokens=None, model_identifier=None):

        headers = {}
        if self.config.get("api_key_env"):
            headers["Authorization"] = "Bearer " + os.environ[self.config["api_key_env"]]
        elif self.config.get("api_key_file"):
            key = json.loads(Path(self.config["api_key_file"]).read_text())["api_key"]
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
        try:
            response = await self.client.post(
                self.config["endpoint"].rstrip("/") + "/v1/chat/completions",
                json=body,
                headers=headers,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise InfrastructureFailure(f"provider_http_{response.status_code}")
            if response.is_error:
                detail = response.text
                authorization = headers.get("Authorization")
                if authorization:
                    detail = detail.replace(authorization.removeprefix("Bearer "), "[redacted]")
                raise httpx.HTTPStatusError(
                    f"provider_http_{response.status_code}: {detail[:2000]}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
        except httpx.RequestError as error:
            raise InfrastructureFailure(
                type(error).__name__, retryable=not isinstance(error, httpx.TimeoutException)
            ) from error
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
        return self.expand_response(answer, observation), dict(
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
        )

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
            token = json.loads(Path(self.config["api_key_file"]).read_text())["api_key"]
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


                async with self._instance_load_lock:
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
        return self.expand_response(answer, observation), dict(
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
