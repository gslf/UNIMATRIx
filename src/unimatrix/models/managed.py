"""The model instance a run plays on: loaded once, shared by every seed played in parallel.

LM Studio (0.4 and later) serves several predictions at once on one loaded instance (continuous
batching, "Max Concurrent Predictions", 4 by default; further requests queue). Parallel seeds must
therefore share one instance rather than load the weights once per seed: a run creates one
`ManagedModel`, every seed's client uses it, and the first load or the first request happens
under its lock so concurrent seeds never trigger two loads.
"""

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
        # With `managed_instance` the run loads its own instance under this identifier and unloads it
        # at the end; otherwise it uses whichever instance LM Studio has loaded for the model key.
        self.identifier = "unimatrix-" + uuid.uuid4().hex if config.get("managed_instance") else None
        self.load_lock = asyncio.Lock()
        self.loaded = False
        self.load_failed = False
        self.ready = False  # one request or load has succeeded: the weights are in memory
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
