"""
pypi.py — PyPI HTTP client and metadata helpers.

Fixes applied:
  - Bug 1 (v2): Distinguishes package-not-found vs version-not-found
  - Bug 2 (v2): Always fetches unversioned endpoint for latest_versions
  - Bug 3 (v2): Truncates / normalises the license field
  - Bug 4 (v2): Marker evaluation uses target Python version, not host
  - Bug 1 (v3): sorted_versions falls back to pre-releases when no
                stable versions exist (e.g. opentelemetry-instrumentation-*)
  - Bug 5 (v3): normalize_requires_python strips trailing commas
"""

import re
from typing import Optional

import httpx
from packaging.version import Version, InvalidVersion
from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.requirements import Requirement, InvalidRequirement

PYPI_BASE = "https://pypi.org/pypi"
HTTP_TIMEOUT = 20.0
_LICENSE_MAX_CHARS = 100


async def fetch_package(client: httpx.AsyncClient, package: str) -> Optional[dict]:
    """Fetch the unversioned PyPI JSON for a package (includes all releases)."""
    url = f"{PYPI_BASE}/{package}/json"
    try:
        r = await client.get(url, timeout=HTTP_TIMEOUT)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def fetch_package_version(
    client: httpx.AsyncClient,
    package: str,
    version: str,
) -> tuple[Optional[dict], str]:
    """Fetch versioned PyPI JSON. Bug 1 (v2): distinguishes missing pkg vs version."""
    url = f"{PYPI_BASE}/{package}/{version}/json"
    try:
        r = await client.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json(), ""
        if r.status_code == 404:
            pkg_data = await fetch_package(client, package)
            if pkg_data is None:
                return None, f"Package '{package}' not found on PyPI."
            return None, f"Version '{version}' of package '{package}' not found on PyPI."
        return None, f"PyPI returned HTTP {r.status_code} for {package}=={version}."
    except Exception as exc:
        return None, f"Network error fetching {package}=={version}: {exc}"


def _is_parseable(v: str) -> bool:
    try:
        Version(v)
        return True
    except InvalidVersion:
        return False


def sorted_versions(releases: dict) -> list[str]:
    """Return versions sorted descending (newest first).

    Bug 1 (v3) fix: falls back to pre-releases when no stable versions exist
    (e.g. entire opentelemetry-instrumentation-* family is 0.xb0 pre-releases).
    Dev releases are always excluded.
    """
    stable: list[Version] = []
    prerelease: list[Version] = []
    for v in releases:
        try:
            parsed = Version(v)
            if parsed.is_devrelease:
                continue
            (prerelease if parsed.is_prerelease else stable).append(parsed)
        except InvalidVersion:
            continue

    if stable:
        stable.sort(reverse=True)
        return [str(v) for v in stable]

    prerelease.sort(reverse=True)
    return [str(v) for v in prerelease]


def has_only_prerelease_versions(releases: dict) -> bool:
    """True when a package exists but all its versions are pre-releases."""
    non_dev = [Version(v) for v in releases if _is_parseable(v) and not Version(v).is_devrelease]
    return bool(non_dev) and all(v.is_prerelease for v in non_dev)


def normalize_name(name: str) -> str:
    """Normalize a package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def normalize_requires_python(requires_python: Optional[str]) -> Optional[str]:
    """Bug 5 (v3) fix: strip trailing commas (e.g. celery '>=3.6,') and
    re-serialize via SpecifierSet for a canonical string."""
    if not requires_python:
        return None
    stripped = requires_python.rstrip(",").strip()
    try:
        return str(SpecifierSet(stripped)) or None
    except InvalidSpecifier:
        return stripped or None


def check_python_compat(requires_python: Optional[str], python_version: str) -> bool:
    """True if requires_python is satisfied by python_version."""
    normalized = normalize_requires_python(requires_python)
    if not normalized:
        return True
    try:
        return Version(python_version) in SpecifierSet(normalized)
    except (InvalidVersion, InvalidSpecifier):
        return True


def clean_license(info: dict) -> str:
    """Bug 3 (v2): prefer SPDX license_expression; truncate free-text."""
    expr = info.get("license_expression")
    if expr:
        return expr.strip()
    raw = info.get("license") or ""
    if len(raw) > _LICENSE_MAX_CHARS:
        return raw[:_LICENSE_MAX_CHARS].rstrip() + "…"
    return raw or "unknown"


def parse_requires_dist(
    requires_dist: Optional[list[str]],
    python_version: str,
) -> list[Requirement]:
    """Bug 4 (v2): marker evaluation uses target python_version, not host interpreter."""
    reqs: list[Requirement] = []
    if not requires_dist:
        return reqs
    parsed = Version(python_version)
    marker_env = {
        "extra": None,
        "python_version": f"{parsed.major}.{parsed.minor}",
        "python_full_version": str(parsed),
    }
    for raw in requires_dist:
        try:
            req = Requirement(raw)
            if req.marker and req.marker.evaluate(marker_env) is False:
                continue
            reqs.append(req)
        except InvalidRequirement:
            continue
    return reqs


def extract_latest_versions(package_data: dict, limit: int = 10) -> list[str]:
    """Bug 2 (v2): always use unversioned PyPI response to get releases."""
    return sorted_versions(package_data.get("releases", {}))[:limit]


async def fetch_latest_version(
    client: httpx.AsyncClient,
    package: str,
    include_prerelease: bool = False,
) -> dict:
    """
    Lightweight fetch — returns only the latest stable (and optionally
    latest pre-release) version for a single package.

    Much faster than fetch_package_version() because it only reads
    the top-level info block and the releases dict, nothing more.

    Returns a dict with keys:
      package          — normalized name
      latest_stable    — newest non-pre-release version, or None
      latest_prerelease — newest pre-release version if include_prerelease=True
                          and it is newer than latest_stable, else None
      only_prerelease  — True when no stable version exists
      published_on     — ISO date string of latest_stable release, or None
      error            — error string if the package could not be fetched
    """
    data = await fetch_package(client, package)
    if data is None:
        return {"package": normalize_name(package), "error": f"Package '{package}' not found on PyPI."}

    releases = data.get("releases", {})
    info = data.get("info", {})

    stable_versions = sorted_versions({k: v for k, v in releases.items()
                                       if not Version(k).is_prerelease
                                       if _is_parseable(k)})
    pre_versions = sorted_versions({k: v for k, v in releases.items()
                                    if _is_parseable(k)
                                    and Version(k).is_prerelease
                                    and not Version(k).is_devrelease})

    latest_stable = stable_versions[0] if stable_versions else None
    only_prerelease = has_only_prerelease_versions(releases)

    # Effective latest — if no stable, fall back to pre-release
    effective_latest = latest_stable or (pre_versions[0] if pre_versions else None)

    # published_on from upload_time of the first file in that release
    published_on = None
    if effective_latest and releases.get(effective_latest):
        files = releases[effective_latest]
        if files and isinstance(files, list) and files[0].get("upload_time"):
            published_on = files[0]["upload_time"][:10]  # YYYY-MM-DD

    result = {
        "package": normalize_name(package),
        "latest_stable": latest_stable,
        "only_prerelease": only_prerelease,
        "published_on": published_on,
        "pypi_url": f"https://pypi.org/project/{info.get('name', package)}/",
    }

    if pre_versions:
        latest_pre = pre_versions[0]
        # Always expose when no stable exists (powers the fallback display in tools.py).
        # When include_prerelease=True, also expose when it's newer than stable.
        if latest_stable is None:
            result["latest_prerelease"] = latest_pre
        elif include_prerelease and Version(latest_pre) > Version(latest_stable):
            result["latest_prerelease"] = latest_pre
        elif include_prerelease:
            result["latest_prerelease"] = None
    elif include_prerelease:
        result["latest_prerelease"] = None

    return result