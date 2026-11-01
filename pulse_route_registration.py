"""Safe replacement of selected FastAPI route methods."""

from __future__ import annotations

import copy
from collections.abc import Iterable


def remove_and_add(app, path: str, methods: Iterable[str], endpoint, **kwargs) -> None:
    """Replace only selected methods and retain other methods on the same path."""
    if isinstance(methods, (str, bytes)):
        raise ValueError("methods must be an iterable of HTTP method names")

    replacement_methods = {str(method).upper() for method in methods}
    if not replacement_methods:
        raise ValueError("at least one HTTP method must be supplied")

    registrars = {"GET": app.get, "POST": app.post}
    unsupported = replacement_methods.difference(registrars)
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise ValueError(f"unsupported route replacement method(s): {names}")

    # FastAPI implicitly registers HEAD for GET. Replace that implicit method too,
    # otherwise an earlier route may continue to serve HEAD with the old endpoint.
    methods_to_replace = set(replacement_methods)
    if "GET" in replacement_methods:
        methods_to_replace.add("HEAD")

    retained_routes = []
    for route in app.router.routes:
        if getattr(route, "path", None) != path:
            retained_routes.append(route)
            continue

        current_methods = getattr(route, "methods", None)
        if not current_methods:
            retained_routes.append(route)
            continue

        current_methods = {str(method).upper() for method in current_methods}
        if not current_methods.intersection(methods_to_replace):
            retained_routes.append(route)
            continue

        untouched_methods = current_methods.difference(methods_to_replace)
        if untouched_methods:
            preserved_route = copy.copy(route)
            preserved_route.methods = untouched_methods
            retained_routes.append(preserved_route)

    app.router.routes[:] = retained_routes
    for method in sorted(replacement_methods):
        registrars[method](path, **kwargs)(endpoint)
