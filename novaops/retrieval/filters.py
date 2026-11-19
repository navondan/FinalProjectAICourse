"""Fail-closed document audience filtering."""


def audience_is_allowed(audience: str | None, *, is_manager: bool) -> bool:
    if audience is None or audience == "all":
        return True
    if audience == "manager":
        return is_manager
    return False
