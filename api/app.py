"""Outpost core: route registry and per-module schema hooks.

Endpoints live in api/routes/<name>.py. Every module there (except names starting
with "_") is imported at startup. See AGENTS.md for the recipe.
"""
import importlib
import pkgutil
import re

ROUTES = []
MODULES = []


class HTTPError(Exception):
    """Raise from a route to send {"error": message} with this status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def route(method, pattern, public=False):
    """Register fn(req) for METHOD and a full-match path regex.

    Named groups in the pattern become req.params. Routes require the bearer
    token unless public=True. Return a dict (200) or (status, dict).
    """
    def register(fn):
        ROUTES.append((method.upper(), re.compile(pattern), fn, public))
        return fn
    return register


def load_routes():
    if not MODULES:
        import routes
        for info in sorted(pkgutil.iter_modules(routes.__path__), key=lambda item: item.name):
            if not info.name.startswith("_"):
                MODULES.append(importlib.import_module(f"routes.{info.name}"))
    return MODULES


def migrate(db):
    """Run each module's idempotent migrate(db) at startup."""
    for module in load_routes():
        if hasattr(module, "migrate"):
            module.migrate(db)


def check(db):
    """Validate a database before backup/restore; modules may add check(db)."""
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    for module in load_routes():
        if hasattr(module, "check"):
            module.check(db)
