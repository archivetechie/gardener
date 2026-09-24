"""Read legacy standing tables by named columns, preserving issue state evidence."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def read_standing(path: Path) -> list[dict]:
    """Parse Markdown tables without filtering state words from issue prose."""
    if not path.exists():
        return []
    header: list[str] = []
    result = []
    for line in path.read_text().splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        names = [re.sub(r"[*_`]", "", c).lower().replace(" ", "_") for c in cells]
        if "state" in names and ("issue" in names or "id" in names):
            header = names
            continue
        if not header or all(re.fullmatch(r"[: -]*", c) for c in cells):
            continue
        if len(cells) < len(header):
            raise ValueError(f"malformed standing row in {path}: {line}")
        if len(cells) > len(header):
            cells = cells[:len(header) - 1] + ["|".join(cells[len(header) - 1:])]
        row = dict(zip(header, cells))
        row["state"] = re.sub(r"[*_`]", "", row["state"]).strip().lower()
        title = row.get("issue", row.get("id", ""))
        row["id"] = row.get("id") or "issue-" + hashlib.sha256(title.encode()).hexdigest()[:12]
        result.append(row)
    return result


def render_active(path: Path) -> str:
    """Show state/date before the description so truncation cannot hide them."""
    rows = [r for r in read_standing(path) if r["state"] not in {"fixed", "closed", "accepted"}]
    lines = []
    for row in rows:
        date = next((row[k] for k in ("last_observed_failure", "last_seen", "since", "first_seen")
                     if row.get(k)), "unknown")
        lines.append(f"  [{row['state']}] {date} {row['id']}: {row.get('issue', '')}")
    lines.append(f"  ({len(rows)} active issues; none omitted)")
    return "\n".join(lines)
