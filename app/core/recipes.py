"""Recipe selection and validation for PLC-requested numeric recipe IDs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class RecipeError(ValueError):
    """Base error for an invalid or unavailable requested recipe."""


class RecipeNotFoundError(RecipeError):
    """No configured recipe matches a requested PLC numeric ID."""


class RecipeRevisionError(RecipeError):
    """PLC revision differs from the configured recipe revision."""


@dataclass(frozen=True, slots=True)
class RecipeDefinition:
    recipe_id: int
    name: str
    revision: int
    models_file: str
    thresholds_file: str
    camera_rois: dict[int, tuple[int, int, int, int]]
    product_parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RecipeRuntime:
    definition: RecipeDefinition
    models: dict[str, dict[str, Any]]
    input_size: tuple[int, int]
    ok_threshold: float


class RecipeRepository:
    def __init__(self, definitions: dict[int, RecipeDefinition], project_root: Path):
        self._definitions = definitions
        self._project_root = project_root

    @classmethod
    def from_yaml(cls, recipe_path: str | Path, project_root: str | Path) -> "RecipeRepository":
        recipe_path = Path(recipe_path)
        root = Path(project_root)
        try:
            raw = yaml.safe_load(recipe_path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise RecipeError(f"Unable to read recipe configuration {recipe_path}: {exc}") from exc
        recipes = raw.get("recipes")
        if not isinstance(recipes, dict) or not recipes:
            raise RecipeError("recipes.yaml requires a non-empty 'recipes' mapping")
        definitions: dict[int, RecipeDefinition] = {}
        for name, value in recipes.items():
            if not isinstance(value, dict):
                raise RecipeError(f"Recipe {name!r} must be a mapping")
            recipe_id = int(value.get("id", 0 if name == "default" else -1))
            if not 0 <= recipe_id <= 0xFFFF or recipe_id in definitions:
                raise RecipeError(f"Recipe {name!r} has missing, duplicate, or invalid numeric id")
            revision = int(value.get("revision", 0))
            rois = cls._parse_rois(value.get("camera_rois", value.get("rois", {})), name)
            definitions[recipe_id] = RecipeDefinition(
                recipe_id=recipe_id,
                name=str(value.get("name", name)),
                revision=revision,
                models_file=str(value.get("models_file", "configs/model.yaml")),
                thresholds_file=str(value.get("thresholds_file", "configs/thresholds.yaml")),
                camera_rois=rois,
                product_parameters=dict(value.get("product_parameters", {})),
            )
        return cls(definitions, root)

    @staticmethod
    def _parse_rois(raw: Any, recipe_name: str) -> dict[int, tuple[int, int, int, int]]:
        if raw in (None, []):
            return {}
        if not isinstance(raw, dict):
            raise RecipeError(f"Recipe {recipe_name!r} camera_rois must be a mapping")
        result: dict[int, tuple[int, int, int, int]] = {}
        for camera, roi in raw.items():
            if not isinstance(roi, (list, tuple)) or len(roi) != 4:
                raise RecipeError(f"Recipe {recipe_name!r} ROI for camera {camera!r} must be [x, y, width, height]")
            x, y, width, height = (int(value) for value in roi)
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise RecipeError(f"Recipe {recipe_name!r} ROI for camera {camera!r} is invalid")
            result[int(camera)] = (x, y, width, height)
        return result

    def get(self, recipe_id: int, revision: int | None = None) -> RecipeDefinition:
        try:
            definition = self._definitions[recipe_id]
        except KeyError as exc:
            raise RecipeNotFoundError(f"Requested recipe ID {recipe_id} is not configured") from exc
        if revision is not None and definition.revision != revision:
            raise RecipeRevisionError(
                f"Recipe ID {recipe_id} revision {revision} does not match configured revision {definition.revision}"
            )
        return definition

    def load(self, recipe_id: int, revision: int | None = None) -> RecipeRuntime:
        definition = self.get(recipe_id, revision)
        models_path = self._resolve(definition.models_file)
        thresholds_path = self._resolve(definition.thresholds_file)
        try:
            models_raw = yaml.safe_load(models_path.read_text(encoding="utf-8")) or {}
            thresholds_raw = yaml.safe_load(thresholds_path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise RecipeError(f"Recipe {definition.name!r} references unreadable configuration: {exc}") from exc
        models = models_raw.get("models")
        if not isinstance(models, dict) or not models:
            raise RecipeError(f"Recipe {definition.name!r} model configuration has no 'models' mapping")
        input_size = thresholds_raw.get("input_size", [280, 280])
        if not isinstance(input_size, (list, tuple)) or len(input_size) != 2 or min(input_size) <= 0:
            raise RecipeError(f"Recipe {definition.name!r} has invalid input_size")
        try:
            threshold = float(thresholds_raw["ok_threshold"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RecipeError(f"Recipe {definition.name!r} has invalid ok_threshold") from exc
        normalized_models: dict[str, dict[str, Any]] = {}
        for key, value in models.items():
            if not isinstance(value, dict):
                continue
            model = dict(value)
            if model.get("path"):
                path = Path(str(model["path"]))
                model["path"] = str(path if path.is_absolute() else self._project_root / path)
            normalized_models[str(key)] = model
        return RecipeRuntime(
            definition=definition,
            models=normalized_models,
            input_size=(int(input_size[0]), int(input_size[1])),
            ok_threshold=threshold,
        )

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        path = path if path.is_absolute() else self._project_root / path
        if not path.is_file():
            raise RecipeError(f"Recipe configuration file does not exist: {path}")
        return path