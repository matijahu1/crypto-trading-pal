"""
Base interface that every command must implement.

Adding a new command:
  1. Create commands/your_command.py
  2. Subclass BaseCommand
  3. Register an instance in cli/loop.py

Nothing else needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseCommand(ABC):
    """Abstract base for all CLI commands."""

    @property
    @abstractmethod
    def name(self) -> str:
        """The keyword that triggers this command, e.g. 'show'."""

    @property
    def usage(self) -> str:
        """One-line usage hint shown in help / error messages."""
        return self.name

    @abstractmethod
    def execute(self, args: list[str]) -> None:
        """
        Run the command.

        Args:
            args: Tokens that follow the command keyword.
                  E.g. for "show ZECUSDT", args == ["ZECUSDT"].
        """