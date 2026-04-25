import os
import re
from typing import Optional


_MAX_FILENAME_LEN = 255
_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-. ]+$")


def sanitize_gallery_filename(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    candidate = str(raw).strip()
    if not candidate or "\x00" in candidate:
        return None

    if len(candidate) > _MAX_FILENAME_LEN:
        return None

    if ".." in candidate or "/" in candidate or "\\" in candidate:
        return None

    candidate = os.path.basename(candidate)
    if not candidate or not _SAFE_FILENAME_RE.fullmatch(candidate):
        return None

    return candidate
