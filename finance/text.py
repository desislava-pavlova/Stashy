"""Small wording helpers shared by models and the stash screens. Imports nothing from Stashy, so
any module can use it without creating an import cycle."""


def possessive(name):
    """ "Alex" -> "Alex's"; "The Smiths" -> "The Smiths'" (a name already ending in s gets just
    the apostrophe)."""
    suffix = "'" if name.endswith(("s", "S")) else "'s"
    return f"{name}{suffix}"
