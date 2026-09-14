import json
import stat
from pathlib import Path

import httpx
import pytest

from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.web.server import build_app

MODEL = dict(
    model="test",
    snapshot="test-v1",
    endpoint="http://localhost:8080",
    context_bytes_verified=24000,
    budget_track="opaque_compute",
)


@pytest.mark.asyncio
async def test_model_editor_credentials_and_retired_endpoints(tmp_path):
    models = tmp_path / "models"
    app = build_app(tmp_path / "runs", models, tmp_path / "plans")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/configuration")).status_code == 404
        assert (await client.post("/api/plan", json={})).status_code == 404
        assert (await client.get("/api/configs")).status_code == 404
        response = await client.put(
            "/api/models/test", json={"config": MODEL, "api_key": "test-secret"}
        )
        assert response.status_code == 200
        saved = response.json()
        secret = Path(saved["api_key_file"])
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600
        assert "test-secret" not in response.text
        assert "test-secret" not in (models / "test.json").read_text()
        assert len((await client.get("/api/models")).json()) == 1
        response = await client.put("/api/models/test", json={"config": saved})
        assert response.json() == saved

        def provider(request):
            assert request.headers["Authorization"] == "Bearer test-secret"
            assert str(request.url) == "http://localhost:8080/v1/chat/completions"
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as transport:
            await LLMPolicy(saved, client=transport).decide({}, {"generation_tokens": 4096})
        response = await client.put(
            "/api/models/test", json={"config": saved, "api_key": "replacement"}
        )
        assert response.json()["api_key_file"] != str(secret)
        assert json.loads(secret.read_text())["api_key"] == "test-secret"
        invalid = dict(MODEL, context_bytes_verified=0)
        response = await client.put("/api/models/test", json={"config": invalid, "api_key": "bad"})
        assert response.status_code == 422
        response = await client.put("/api/models/test", json={"config": saved, "api_key": ""})
        assert "api_key_file" not in response.json()
        assert (await client.put("/api/models/.hidden", json={"config": MODEL})).status_code == 422
        (models / "link.json").symlink_to(tmp_path / "outside.json")
        assert (await client.put("/api/models/link", json={"config": MODEL})).status_code == 422
