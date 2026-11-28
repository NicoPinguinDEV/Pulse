"""Compatibility loader for extracting the legacy webserver module.

All extracted handlers must keep the original webserver global namespace. The
application historically relies on runtime rebinding (bot startup, tests that
patch services, data-root overrides, and shared locks). Copying those globals
into independent modules creates stale references.

The loader therefore compiles each domain file in the composition-root namespace
while temporarily setting the module metadata. The functions are registered
under their domain module name, but their __globals__ remains webserver.__dict__.
This keeps the source modular without changing the existing runtime contract.
"""

from importlib.util import find_spec
import sys
from types import ModuleType
from typing import Any


_METADATA_KEYS = (
    "__name__",
    "__file__",
    "__package__",
    "__loader__",
    "__spec__",
    "__cached__",
)


def bind_legacy_namespace(namespace: dict[str, Any]) -> None:
    """Compatibility no-op when domain source is executed in webserver globals.

    Kept temporarily so an extracted module can still be imported directly by
    development tools. Runtime route modules are loaded by load_domain_modules.
    """
    import webserver

    if namespace is not vars(webserver):
        namespace.update(
            (name, value)
            for name, value in vars(webserver).items()
            if not name.startswith("__")
        )


def load_domain_modules(
    namespace: dict[str, Any],
    module_names: tuple[str, ...],
) -> tuple[ModuleType, ...]:
    """Execute domain modules in the shared webserver namespace, in route order."""
    modules: list[ModuleType] = []

    for module_name in module_names:
        spec = find_spec(module_name)
        if spec is None or spec.loader is None or spec.origin is None:
            raise ImportError(f"Cannot locate Pulse web domain module: {module_name}")

        code = spec.loader.get_code(module_name)
        if code is None:
            raise ImportError(f"Cannot load Pulse web domain module code: {module_name}")

        module = ModuleType(module_name)
        module.__file__ = spec.origin
        module.__package__ = module_name.rpartition(".")[0]
        module.__loader__ = spec.loader
        module.__spec__ = spec
        sys.modules[module_name] = module

        previous_metadata = {
            key: namespace.get(key)
            for key in _METADATA_KEYS
        }
        missing_metadata = {
            key for key in _METADATA_KEYS if key not in namespace
        }

        namespace.update(
            {
                "__name__": module_name,
                "__file__": spec.origin,
                "__package__": module.__package__,
                "__loader__": spec.loader,
                "__spec__": spec,
                "__cached__": spec.cached,
            }
        )

        try:
            # Route decorators and function bodies now share webserver.__dict__.
            # Runtime updates and test patches to webserver globals are visible
            # immediately to every extracted handler.
            exec(code, namespace, namespace)
        finally:
            for key in _METADATA_KEYS:
                if key in missing_metadata:
                    namespace.pop(key, None)
                else:
                    namespace[key] = previous_metadata[key]

        # Expose the domain's definitions for imports, introspection and tests.
        # Function objects still retain the shared composition-root globals.
        module.__dict__.update(
            (name, value)
            for name, value in namespace.items()
            if not name.startswith("__")
        )
        modules.append(module)

    return tuple(modules)
