"""Compatibility import for legacy flat-module callers.

The implementation lives under PF/backend; this shim preserves existing test
and script imports while the platform service uses the package path directly.
"""
from PF.backend.epfo_bot import *  # noqa: F401,F403
