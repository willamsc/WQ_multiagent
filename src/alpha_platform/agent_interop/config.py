"""A4 deterministic role routing; configuration never imports plugins or shell commands."""

from .backends import BackendRegistry, MockBackend
from .contracts import Role, identifier, object_fields, strict_json


def parse_backend_config(value):
    object_fields(value, ("schema_version", "roles"))
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("BACKEND_CONFIG_VERSION_UNSUPPORTED")
    object_fields(value["roles"], ("generator", "critic"))
    return {Role(role): identifier(name) for role, name in value["roles"].items()}


def load_backend_config(path):
    if path is None:
        return {Role.GENERATOR: "mock", Role.CRITIC: "mock_alt"}
    with open(path, "rb") as handle:
        raw = handle.read(65537)
    return parse_backend_config(strict_json(raw, 65536))


def mock_registry():
    registry = BackendRegistry()
    registry.register("mock", MockBackend)
    registry.register("mock_alt", MockBackend)
    return registry
