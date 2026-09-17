"""Candidate model files, independent of the benchmark plan."""

import re
import uuid
from pathlib import Path

from ..persistence.json_files import read_json, write_json
from .validation import validate_policy


class ModelRepository:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, ident):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", ident):
            raise ValueError("Use a model file ID with letters, numbers, underscores or hyphens")
        path = self.directory / (ident + ".json")
        if path.is_symlink():
            raise ValueError("Model files must not be symbolic links")
        return path

    def get(self, ident):
        config = read_json(self.path(ident))
        validate_policy(config)
        if not isinstance(config, dict):
            raise ValueError("A candidate file must contain a model configuration")
        return config

    def all(self):
        result = []
        for path in sorted(self.directory.glob("*.json")):
            if path.name.startswith("."):
                continue
            try:
                result.append(dict(id=path.stem, config=self.get(path.stem)))
            except (ValueError, OSError, TypeError) as error:
                result.append(
                    dict(id=path.stem, error="Invalid model configuration: " + type(error).__name__)
                )
        return result

    def save(self, ident, config, api_key=None):
        path = self.path(ident)
        config = dict(config)
        secret = None
        if api_key is not None:
            config.pop("api_key_env", None)
            config.pop("api_key_file", None)
            if api_key:
                # A new key gets a new path: an older run keeps its original credentials.
                secret = self.directory / ("." + uuid.uuid4().hex + "-credentials.json")
                config["api_key_file"] = str(secret)
        validate_policy(config)
        if secret:
            write_json(secret, dict(api_key=api_key))
        write_json(path, config)
        return config
