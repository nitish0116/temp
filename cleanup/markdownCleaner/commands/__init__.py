"""Cohesive command-line workflows used by :mod:`markdownCleaner.cli`.

The public command remains ``markdownCleaner.cli``.  This package keeps parsing,
report rendering, review actions, and batch orchestration independently
testable without turning the CLI module into another application layer.
"""

