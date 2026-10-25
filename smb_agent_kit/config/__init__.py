"""Config: YAML loading with schema validation and the fictional-data check.

Every business config a project ships describes an invented business. The check is a required
field, `fictional: true`: a config without it, or with any other value, fails loudly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class ConfigError(ValueError):
    pass


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


def require_fictional(data: dict, source: Path | str) -> dict:
    """Refuse a business config that does not declare itself fictional."""
    if data.get("fictional") is not True:
        raise ConfigError(
            f"{source}: a business config must carry `fictional: true` (real business data "
            "never ships in a project repo)"
        )
    return data


def load_model(path: Path, model: type[M], *, fictional: bool = False) -> M:
    """Load a YAML file into `model`. With `fictional=True`, apply `require_fictional` first."""
    data = load_yaml(path)
    if fictional:
        require_fictional(data, path)
    return model.model_validate(data)
