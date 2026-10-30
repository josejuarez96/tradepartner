"""Read the timing notes embedded in a paper run result message."""

_PREFIX = "paper_phase "


def owner_message(message: str | None) -> str | None:
    """Return the owner-facing notes, or None when no notes remain."""
    if not message:
        return None
    notes = [part for part in message.split("; ") if part and not part.startswith(_PREFIX)]
    return "; ".join(notes) or None


def parse_paper_phases(message: str | None) -> dict[str, str]:
    """Read the wrapper's `paper_phase name=value` notes as strings."""
    phases: dict[str, str] = {}
    if message:
        for part in message.split("; "):
            if part.startswith(_PREFIX):
                name, separator, value = part[len(_PREFIX) :].partition("=")
                if separator and name and value:
                    phases[name] = value
    return phases
