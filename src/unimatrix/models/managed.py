"""A managed LM Studio instance owned by one model client: loaded on first use, unloaded on close."""

import asyncio
import json
import os
import uuid
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import lmstudio


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
        except Exception:  # best effort: LM Studio's TTL unloads it anyway
            pass
