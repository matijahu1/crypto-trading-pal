"""
CommandLoop — reads user input and dispatches to registered command objects.

Design decisions:
  - Commands are registered by name in a plain dict; O(1) lookup.
  - 'exit' is handled directly in the loop (it's not a BaseCommand subclass
    because it terminates the loop itself — no execute() makes sense).
  - Unknown commands get a friendly error with a list of available commands.
"""

from __future__ import annotations

from commands.base import BaseCommand


class CommandLoop:
    """Main REPL loop for the trading assistant."""

    EXIT_COMMAND = "exit"
    PROMPT = "▶  "

    def __init__(self, commands: list[BaseCommand]) -> None:
        """
        Args:
            commands: All registered command handlers (excluding 'exit').
        """
        self._commands: dict[str, BaseCommand] = {
            cmd.name: cmd for cmd in commands
        }

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the REPL. Blocks until the user enters 'exit'."""
        self._print_banner()
        while True:
            try:
                raw = input(self.PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                # Ctrl-D / Ctrl-C — exit cleanly
                print("\nGoodbye.")
                break

            if not raw:
                continue

            tokens = raw.split()
            keyword, args = tokens[0].lower(), tokens[1:]

            if keyword == self.EXIT_COMMAND:
                print("Goodbye.")
                break

            self._dispatch(keyword, args)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _dispatch(self, keyword: str, args: list[str]) -> None:
        """Look up and execute a command by keyword."""
        cmd = self._commands.get(keyword)
        if cmd is None:
            available = ", ".join(sorted(self._commands)) + ", exit"
            print(f"  Unknown command '{keyword}'. Available: {available}")
            return

        cmd.execute(args)

    @staticmethod
    def _print_banner() -> None:
        print("╔══════════════════════════════════════════════════╗")
        print("║         Crypto Trading Assistant  v0.1           ║")
        print("║         Powered by Bybit public API              ║")
        print("╚══════════════════════════════════════════════════╝")
        print("  Type  show <SYMBOL>  to analyse a perpetual.")
        print("  Type  balance <SYMBOL>  to show balance.")
        print("  Type  exit           to quit.\n")
