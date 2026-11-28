"""Temporary compatibility bridge for extracting the legacy webserver module.

The original dashboard used one shared module namespace. Existing route handlers
still resolve many helpers by global name. The bridge keeps those call sites and
the public `webserver` import stable while domains are separated.

Do not add new cross-domain dependencies through this mechanism. New code should
prefer explicit imports and narrow service interfaces. The bridge can be removed
after legacy global references have been untangled.
"""

from importlib import import_module
from types import ModuleType


def bind_legacy_namespace(namespace: dict[str, object]) -> None:
    """Expose the current composition-root namespace while a domain is imported."""
    import webserver

    namespace.update(
        (name, value)
        for name, value in vars(webserver).items()
        if not name.startswith("__")
    )


def load_domain_modules(
    namespace: dict[str, object],
    module_names: tuple[str, ...],
) -> tuple[ModuleType, ...]:
    """Load domains in source order and preserve the legacy cross-module namespace."""
    modules: list[ModuleType] = []

    for module_name in module_names:
        module = import_module(module_name)
        modules.append(module)
        namespace.update(
            (name, value)
            for name, value in vars(module).items()
            if not name.startswith("__")
        )

    # Functions keep their own module globals. Synchronizing the shared names once
    # after registration preserves forward references from the former monolith.
    shared_namespace = {
        name: value
        for name, value in namespace.items()
        if not name.startswith("__")
    }
    for module in modules:
        module.__dict__.update(shared_namespace)

    return tuple(modules)
