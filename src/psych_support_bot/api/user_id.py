"""Single validation point for request-supplied subject identifiers.

All user/session identifiers that arrive from query/body/headers must pass
through ``normalize_user_id`` before they reach a database lookup, a
pseudonym derivation or an external API call. The strict charset both enforces
the ID formats this system actually issues (``acct_``+hex, uuid, uuid-hex,
legacy slugs) and structurally cuts the untrusted-data path into queries and
providers: nothing that fails the pattern is ever concatenated, interpolated
or forwarded.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

USER_ID_MAX_LEN = 64
# uuid / uuid-hex / acct_<hex> / legacy alnum slugs; no ':' '*' '/' '?' or any
# delimiter that could reshape a query, path or provider filter.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def normalize_user_id(raw: str | None, *, field: str = "user_id") -> str:
    """Return the identifier only when it is provably well-formed; else 422."""
    value = (raw or "").strip()
    if len(value) > USER_ID_MAX_LEN or not _USER_ID_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail=f"{field} is not a valid identifier.")
    return value
