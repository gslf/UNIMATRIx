"""Candidate model files: list, save (credentials stored apart), persona presets, health check."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from ..models.health import check_model
from ..models.personas import catalog
from ..models.repository import ModelRepository


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict
    api_key: str | None = None


def build_models_router(models_dir="config/models"):
    models = ModelRepository(models_dir)
    router = APIRouter(prefix="/api")

    def checked(fn, *args):
        try:
            return fn(*args)
        except FileNotFoundError as error:
            raise HTTPException(404, "Model file not found") from error
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(422, str(error)) from error
        except OSError as error:
            raise HTTPException(500, "Could not read or write the model file") from error

    @router.get("/models")
    def model_list():
        return models.all()

    @router.get("/personalities")
    def personalities():
        return catalog()

    @router.post("/models/{ident}/test")
    async def test_model(ident: str):
        return await check_model(checked(models.get, ident))

    @router.put("/models/{ident}")
    def save_model(ident: str, body: ModelIn):
        return checked(models.save_from_web, ident, body.config, body.api_key)

    router.models = models
    return router
