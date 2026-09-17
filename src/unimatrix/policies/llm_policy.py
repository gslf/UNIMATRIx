"""One observable call per decision; no repair or reflection policy."""

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
import lmstudio

from ..benchmark.runner import InfrastructureFailure
from ..core.ids import canonical, digest
from ..core.visibility import PROTOCOL


class ContextConfigurationError(ValueError):
    pass


class LLMPolicy:
    def __init__(self, config, client=None):
        from ..benchmark.validation import validate_policy

        validate_policy(config)
        self.config = config
        self.fingerprint = digest(config)
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, read=None))

    def system_prompt(self):
        personality = self.config.get("system_prompt")
        return PROTOCOL + "\n\nAgent personality:\n" + personality if personality else None

    async def decide(self, observation, budget):
        if "context_tokens" in self.config:
            return await self._decide_with_context(observation)
        # Legacy OpenAI-compatible configurations remain readable and runnable.
        # No local generation cap: until migrated, server settings govern context.
        headers = {}
        if self.config.get("api_key_env"):
            headers["Authorization"] = "Bearer " + os.environ[self.config["api_key_env"]]
        elif self.config.get("api_key_file"):
            key = json.loads(Path(self.config["api_key_file"]).read_text())["api_key"]
            headers["Authorization"] = "Bearer " + key
        body = dict(
            model=self.config["model"],
            messages=(
                [dict(role="system", content=self.system_prompt())] if self.system_prompt() else []
            )
            + [dict(role="user", content=canonical(observation))],
            temperature=self.config.get("temperature", 0),
            # Constrain only JSON syntax; envelope validity remains a scored outcome.
            # LM Studio supports json_schema but rejects json_object.
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "json_object", "schema": {"type": "object"}},
            },
        )
        if "seed" in self.config:
            body["seed"] = self.config["seed"]
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
            raise InfrastructureFailure(type(error).__name__) from error
        data = response.json()
        usage = data.get("usage", {})
        generated = usage.get("completion_tokens")
        if self.config["budget_track"] == "accounted_compute" and (
            type(generated) is not int or generated < 0
        ):
            raise InfrastructureFailure("missing_generation_accounting")
        choice = data["choices"][0]
        raw = choice["message"].get("content") or ""
        return raw, dict(
            system_prompt=self.system_prompt(),
            purpose="decision",
            generated_tokens=generated,
            input_tokens=usage.get("prompt_tokens"),
            reasoning_tokens=usage.get("completion_tokens_details", {}).get("reasoning_tokens"),
            finish_reason=choice.get("finish_reason"),
            response_hash=digest(data),
        )

    async def _decide_with_context(self, observation):
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
            # A per-decision scope guarantees cancellation closes the SDK streams.
            async with lmstudio.AsyncClient(
                api_host=urlparse(self.config["endpoint"]).netloc,
                api_token=token,
            ) as client:
                model = await client.llm.model(self.config["model"], config=load_config)
                actual = await model.get_context_length()
                if actual != context:
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
                prompt = canonical(observation)
                if self.system_prompt():
                    prompt = lmstudio.Chat(self.system_prompt())
                    prompt.add_user_message(canonical(observation))
                result = await model.respond(
                    prompt,
                    response_format={"type": "object"},
                    config={
                        # This bound is derived solely from context. stopAtLimit
                        # stops earlier, accounting for prompt/template tokens.
                        "maxTokens": context,
                        "contextOverflowPolicy": "stopAtLimit",
                        "temperature": self.config.get("temperature", 0),
                    },
                    on_prediction_fragment=fragment_received,
                )
        except ContextConfigurationError:
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
        return "".join(answer), dict(
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
        await self.client.aclose()
