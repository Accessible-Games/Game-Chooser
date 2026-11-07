"""Save tracing: a build-time variant of Game Chooser that records the file and registry writes of the games it
launches, using Process Monitor, to find out where each game keeps its saves and settings.

This package is excluded from normal builds. Nothing outside it imports it (the one exception is game-chooser.py,
inside try/except ImportError)."""
