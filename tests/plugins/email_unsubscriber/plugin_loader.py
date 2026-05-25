"""Helpers for loading the bundled email-unsubscriber plugin in tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


PLUGIN_PACKAGE = "hermes_plugins.email_unsubscriber"


def load_email_unsubscriber_plugin():
    """Load plugins/email-unsubscriber as hermes_plugins.email_unsubscriber."""
    existing = sys.modules.get(PLUGIN_PACKAGE)
    if existing is not None:
        return existing

    repo_root = Path(__file__).resolve().parents[3]
    plugin_dir = repo_root / "plugins" / "email-unsubscriber"
    spec = importlib.util.spec_from_file_location(
        PLUGIN_PACKAGE,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    assert spec is not None and spec.loader is not None

    if "hermes_plugins" not in sys.modules:
        ns_pkg = types.ModuleType("hermes_plugins")
        ns_pkg.__path__ = []
        ns_pkg.__package__ = "hermes_plugins"
        sys.modules["hermes_plugins"] = ns_pkg

    module = importlib.util.module_from_spec(spec)
    module.__package__ = PLUGIN_PACKAGE
    module.__path__ = [str(plugin_dir)]
    sys.modules[PLUGIN_PACKAGE] = module
    spec.loader.exec_module(module)
    return module
