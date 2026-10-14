"""Writes the audit lane's notes for A-2 from the team's template. Run from the project root."""
from pathlib import Path

NOTES = "knowledge/audit/02-audit-notes.md"
Path(NOTES).write_text("# Audit notes — A-2\n\nThe low-stock check left out items whose qty is 0.\n")
print(f"wrote {NOTES}")
