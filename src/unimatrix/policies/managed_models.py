"""Batch-scoped ownership of managed model instances; no prompt or response state."""

import asyncio
import json
import os
import uuid
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import lmstudio

from ..core.ids import digest

model_pool = ContextVar("unimatrix_managed_model_pool", default=None)


class ManagedModel:
    def __init__(self, config):
        self.config = deepcopy(config)
        self.identifier = "unimatrix-" + uuid.uuid4().hex if config.get("managed_instance") else None
        self.load_lock = asyncio.Lock()
        self.loaded = False
        self.load_failed = False
        self.closed = False

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.identifier is None:
            return
        token = ""
        if self.config.get("api_key_env"):
            token = os.environ[self.config["api_key_env"]]
        elif self.config.get("api_key_file"):
            token = json.loads(Path(self.config["api_key_file"]).read_text())["api_key"]
        try:
            async with asyncio.timeout(5):
                async with lmstudio.AsyncClient(
                    api_host=urlparse(self.config["endpoint"]).netloc, api_token=token,
                ) as client:
                    await client.llm.unload(self.identifier)
        except Exception:


            pass


class ManagedModelPool:
    def __init__(self):
        self.instances = {}
        self.closed = False

    def acquire(self, config):
        if self.closed:
            raise RuntimeError("managed_model_pool_closed")
        key = digest(config)
        if key not in self.instances:
            self.instances[key] = ManagedModel(config)
        return self.instances[key]

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            results = await asyncio.gather(
                *(model.close() for model in self.instances.values()), return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, BaseException)]
            if failures:
                raise failures[0]
        finally:
            self.instances.clear()
