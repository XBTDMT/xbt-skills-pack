"""Writes the CLI spec from the team's template. Run from the project root."""
from pathlib import Path

Path("knowledge/02-spec-cli.md").write_text("# CLI spec\n\nThe shelf command prints the low-stock report, one sku per line.\n")
print("done")
