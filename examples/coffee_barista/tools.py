"""coffee_barista.tools — simple file-backed memory tools for the example blueprints."""

from pathlib import Path


def read_memory(file_path: str = "MEMORY.md") -> str:
    """Read past user preferences from a markdown file."""
    p = Path(file_path)
    if not p.exists():
        return "(no memory yet)"
    content = p.read_text(encoding="utf-8").strip()
    return content if content else "(no memory yet)"


def save_memory(entry: str, file_path: str = "MEMORY.md") -> str:
    """Append a preference / feedback line to the memory file."""
    p = Path(file_path)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"- {entry}\n")
    return f"Saved: {entry}"
