"""IBWD command-line interface."""

from __future__ import annotations

import click

from ibwd.scan import run_scan


@click.group()
def main() -> None:
    """IBWD — persistent codebase memory for AI coding agents."""


@main.command()
def scan() -> None:
    """Scan the repo (incrementally) and update the graph."""
    summary = run_scan()
    click.echo(
        f"Scanned {summary['total_files']} files "
        f"(added={summary['added']}, changed={summary['changed']}, "
        f"unchanged={summary['unchanged']}, removed={summary['removed']})"
    )


if __name__ == "__main__":
    main()
