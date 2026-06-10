"""Shadow Dark currency helpers.

Internal storage: a single integer of copper pieces (cp).
Conversion: 1 gp = 10 sp = 100 cp.
"""

import re

CP_PER_SP = 10
CP_PER_GP = 100

_VALUE_TOKEN_RE = re.compile(r"(\d+)\s*(gp|sp|cp)", re.IGNORECASE)


def parse_to_cp(gp: int | None, sp: int | None, cp: int | None) -> int | None:
    """Combine gp/sp/cp inputs into a single copper total.

    Returns None if none of the three were provided (no value tracked).
    Returns 0 if at least one was provided but all were 0.
    """
    if gp is None and sp is None and cp is None:
        return None
    return (gp or 0) * CP_PER_GP + (sp or 0) * CP_PER_SP + (cp or 0)


def format_cp(value: int | None) -> str | None:
    """Render a copper total like '1gp 5sp', skipping zero denominations.

    Returns None if value is None. Returns '0cp' if value is exactly 0.
    """
    if value is None:
        return None
    if value == 0:
        return "0cp"

    sign = "-" if value < 0 else ""
    n = abs(value)
    gp, rem = divmod(n, CP_PER_GP)
    sp, cp = divmod(rem, CP_PER_SP)

    parts: list[str] = []
    if gp:
        parts.append(f"{gp}gp")
    if sp:
        parts.append(f"{sp}sp")
    if cp:
        parts.append(f"{cp}cp")
    return sign + " ".join(parts)


def parse_value_string(s: str) -> int | None:
    """Parse a value string like '5gp 2sp 3cp' into a copper total.

    Returns None for empty/whitespace input. Returns 0 for the bare string '0'.
    Raises ValueError on anything that doesn't match the gp/sp/cp grammar.
    """
    s = s.strip()
    if not s:
        return None
    if s == "0":
        return 0

    pairs: list[tuple[int, str]] = []
    pos = 0
    for m in _VALUE_TOKEN_RE.finditer(s):
        gap = s[pos:m.start()].strip()
        if gap:
            raise ValueError(f'Unexpected text "{gap}" in value')
        pairs.append((int(m.group(1)), m.group(2).lower()))
        pos = m.end()
    trailing = s[pos:].strip()
    if trailing:
        raise ValueError(f'Unexpected text "{trailing}" in value')
    if not pairs:
        raise ValueError(f'Expected something like "5gp 2sp 3cp" — got "{s}"')

    total = 0
    for n, unit in pairs:
        if unit == "gp":
            total += n * CP_PER_GP
        elif unit == "sp":
            total += n * CP_PER_SP
        else:
            total += n
    return total
