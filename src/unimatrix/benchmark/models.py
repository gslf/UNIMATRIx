"""Candidate model files, independent of the benchmark plan."""

import re
import uuid
from pathlib import Path

from ..persistence.json_files import read_json, write_json
from .validation import is_model, validate_policy


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
        if not is_model(config):
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

    def validate_peer_credentials(self, peers):
        known = [r["config"] for r in self.all() if "config" in r]
        for peer in peers:
            if not isinstance(peer, dict) or not any(k in peer for k in ("api_key_env", "api_key_file")):
                continue
            if not any(all(peer.get(k) == saved.get(k) for k in ("api_key_env", "api_key_file", "endpoint")) for saved in known):
                raise ValueError("peer_credentials_require_a_saved_model_and_unchanged_endpoint")

    def save_from_web(self, ident, config, api_key=None):


        if api_key is None and any(k in config for k in ("api_key_env", "api_key_file")):
            try:
                previous = self.get(ident)
            except (OSError, ValueError):
                raise ValueError("credential_reference_must_be_provisioned_locally") from None
            if (any(config.get(k) != previous.get(k) for k in ("api_key_env", "api_key_file"))
                    or config.get("endpoint") != previous.get("endpoint")):
                raise ValueError("credential_reference_or_endpoint_changed_supply_new_key")
        return self.save(ident, config, api_key)

    def save(self, ident, config, api_key=None):
        path = self.path(ident)
        config = dict(config)
        secret = None
        if api_key is not None:
            config.pop("api_key_env", None)
            config.pop("api_key_file", None)
            if api_key:

                secret = self.directory / ("." + uuid.uuid4().hex + "-credentials.json")
                config["api_key_file"] = str(secret)
        validate_policy(config)
        if secret:
            write_json(secret, dict(api_key=api_key))
        write_json(path, config)
        return config
