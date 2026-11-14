"""NASA Earthdata Login resolution — EARTHDATA_TOKEN-first, shared helper.

Precedence at call time (never hardcode credentials):
  1. EARTHDATA_TOKEN       -> OAuth bearer token (~60 day lifetime)
  2. EDL_USER/EDL_PASS or EARTHDATA_USERNAME/EARTHDATA_PASSWORD
  3. ~/.netrc entry for 'machine urs.earthdata.nasa.gov'

Used by tp.nisar / tp.hls_fetch / tp.modis_nrt and the fetch modules
(imerg/smap/nisar). earthaccess and asf_search are imported lazily
inside the functions that need them.
"""
from __future__ import annotations

import os
from pathlib import Path

EDL_HOST = "urs.earthdata.nasa.gov"


def edl_token() -> str | None:
    """The EARTHDATA_TOKEN env var (OAuth bearer), or None."""
    return os.environ.get("EARTHDATA_TOKEN") or None


def edl_auth_headers() -> dict:
    """Bearer Authorization header for plain HTTPS fetches ({} without token)."""
    token = edl_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def ensure_earthdata() -> dict:
    """Resolve EDL creds: {"token"} | {"user","password"} | {"netrc"}.

    Raises a clear RuntimeError naming what is missing instead of letting a
    network or auth layer fail obscurely.
    """
    token = edl_token()
    if token:
        return {"token": token}
    user = os.environ.get("EDL_USER") or os.environ.get("EARTHDATA_USERNAME")
    pw = os.environ.get("EDL_PASS") or os.environ.get("EARTHDATA_PASSWORD")
    if user and pw:
        return {"user": user, "password": pw}
    netrc_path = Path(os.environ.get("NETRC") or Path.home() / ".netrc")
    if netrc_path.is_file():
        import netrc

        try:
            auths = netrc.netrc(str(netrc_path)).authenticators(EDL_HOST)
        except (netrc.NetrcParseError, OSError):
            auths = None
        if auths:
            return {"netrc": netrc_path}
    missing = (
        "EDL_USER/EDL_PASS"
        if not (user or pw)
        else ("EDL_PASS" if user else "EDL_USER")
    )
    raise RuntimeError(
        f"Earthdata credentials required: set EARTHDATA_TOKEN or {missing} "
        "env vars (or EARTHDATA_USERNAME/EARTHDATA_PASSWORD), or add "
        f"'machine {EDL_HOST}' to {netrc_path} "
        "(see README: cp .env.example .env)."
    )


def earthaccess_login(creds: dict | None = None):
    """earthaccess.login() for an ensure_earthdata() result.

    Token and user/pass both use strategy='environment' — earthaccess reads
    EARTHDATA_TOKEN itself; EDL_USER/PASS map onto EARTHDATA_USERNAME/PASSWORD.
    """
    import earthaccess

    creds = creds or ensure_earthdata()
    try:
        if "user" in creds:
            os.environ["EARTHDATA_USERNAME"] = creds["user"]
            os.environ["EARTHDATA_PASSWORD"] = creds["password"]
        return earthaccess.login(
            strategy="netrc" if "netrc" in creds else "environment"
        )
    except Exception as e:
        # No {e} interpolation: the underlying error may echo credentials.
        raise RuntimeError(
            "Earthdata login failed: check EARTHDATA_TOKEN, "
            "EDL_USER/EDL_PASS, or ~/.netrc credentials"
        ) from e


def asf_session(creds: dict | None = None):
    """asf_search ASFSession authenticated with the EDL bearer token.

    Returns None when the resolved creds carry no token — callers keep their
    unauthenticated (metadata-only) or EDL_USER/EDL_PASS path.
    """
    creds = creds or ensure_earthdata()
    if "token" not in creds:
        return None
    import asf_search as asf

    return asf.ASFSession().auth_with_token(creds["token"])


def gdal_auth_env(creds: dict | None = None) -> dict:
    """GDAL config options carrying the same creds to the /vsicurl/ layer.

    A bearer token goes only into GDAL_HTTP_HEADERS — no cross-host auth
    forwarding flag: the S3 signed URL carries its own signature.
    """
    creds = creds or ensure_earthdata()
    if "token" in creds:
        return {
            "GDAL_HTTP_HEADERS": f"Authorization: Bearer {creds['token']}",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        }
    if "user" in creds:
        return {
            "GDAL_HTTP_USERPWD": f"{creds['user']}:{creds['password']}",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        }
    return {
        "GDAL_HTTP_NETRC": "YES",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    }
