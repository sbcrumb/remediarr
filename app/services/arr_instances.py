"""Multi-instance Sonarr/Radarr configuration.

Seerr assigns each configured Sonarr/Radarr instance a 0-based index (this
shows up as `media.serviceId`/`media.serviceId4k` in its issue API) in the
order the instances were added in Seerr's own settings. This mirrors that
ordering on remediarr's side:

    SONARR_URL / SONARR_API_KEY / SONARR_HTTP_TIMEOUT       -> instance 0
    SONARR_URL_1 / SONARR_API_KEY_1 / SONARR_HTTP_TIMEOUT_1  -> instance 1
    SONARR_URL_2 / ...                                       -> instance 2
    (and so on, same pattern for RADARR_*)

Instance 0 is always required (existing single-instance config keeps working
unchanged — zero config changes needed unless multiple instances are wanted).
Additional instances are read directly via os.getenv rather than through
pydantic Settings, since the count is open-ended and Settings needs a fixed
set of declared fields.

Keeping instances in the SAME order they were added in Seerr is the admin's
responsibility — there's no stable identifier in Seerr's API to derive this
automatically (see PROJECT.md's v3 planning section for why: `serviceUrl` is
whatever the admin typed into Seerr's own instance config, sometimes a
public address, sometimes an internal one, and not reliably matchable back
to remediarr's own configured URLs).
"""

import os
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ArrInstance:
    index: int
    base: str  # already includes /api/v3, trailing slash stripped
    headers: dict
    timeout: int


def load_instances(prefix: str) -> Dict[int, ArrInstance]:
    """prefix e.g. "SONARR" or "RADARR". Instance 0 is read from the plain
    <prefix>_URL/<prefix>_API_KEY/<prefix>_HTTP_TIMEOUT vars (required,
    matches existing single-instance behavior exactly). Instances 1+ are
    read from <prefix>_URL_<N>/... and are optional — reading stops at the
    first missing N, so gaps aren't supported (SONARR_URL_1 + SONARR_URL_3
    with no SONARR_URL_2 silently stops at instance 1)."""
    instances: Dict[int, ArrInstance] = {}

    def _add(index: int, url: str, key: str, timeout: int) -> None:
        base = url.rstrip("/") + "/api/v3"
        instances[index] = ArrInstance(
            index=index,
            base=base,
            headers={"X-Api-Key": key} if key else {},
            timeout=timeout,
        )

    _add(
        0,
        os.getenv(f"{prefix}_URL", ""),
        os.getenv(f"{prefix}_API_KEY", ""),
        int(os.getenv(f"{prefix}_HTTP_TIMEOUT", "60")),
    )

    i = 1
    while True:
        url = os.getenv(f"{prefix}_URL_{i}")
        if not url:
            break
        _add(
            i,
            url,
            os.getenv(f"{prefix}_API_KEY_{i}", ""),
            int(os.getenv(f"{prefix}_HTTP_TIMEOUT_{i}", os.getenv(f"{prefix}_HTTP_TIMEOUT", "60"))),
        )
        i += 1

    return instances


def get_instance(instances: Dict[int, ArrInstance], index: int) -> Optional[ArrInstance]:
    return instances.get(index)
