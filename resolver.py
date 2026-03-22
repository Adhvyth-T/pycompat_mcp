"""
resolver.py — Core dependency resolution and conflict detection.

Bug 2 (v3) fix: When the greedy resolver detects a conflict, it attempts
constraint propagation — scanning alternate candidate versions of the
conflicting dep to find one that satisfies the pinned specifier.
If no valid combo is found, conflicts are reported and the requirements.txt
block is suppressed in tools.py to prevent emitting a broken install set.
"""

from collections import defaultdict
from typing import Optional

import httpx
from packaging.version import Version, InvalidVersion
from packaging.specifiers import SpecifierSet

from pypi import (
    fetch_package,
    fetch_package_version,
    normalize_name,
    check_python_compat,
    parse_requires_dist,
    sorted_versions,
    has_only_prerelease_versions,
)


async def resolve(
    client: httpx.AsyncClient,
    packages: list[str],
    python_version: str,
    max_candidates: int = 5,
) -> dict:
    """
    Resolve compatible versions for a list of PyPI packages.

    Returns a dict with keys:
      python_version     — echoed back
      resolved           — list of {package, version, requires_python, summary, home_page}
      conflicts          — list of {package, dep, required, resolved}
      errors             — list of human-readable error strings
      prerelease_pkgs    — list of package names that resolved to a pre-release
      requirements_valid — bool: False when conflicts remain after propagation
    """
    # ------------------------------------------------------------------
    # Phase 1: Gather candidates per package
    # ------------------------------------------------------------------
    candidates: dict[str, list[str]] = {}
    dep_cache: dict[str, dict[str, list]] = defaultdict(dict)
    errors: list[str] = []
    prerelease_pkgs: list[str] = []

    for pkg in packages:
        norm = normalize_name(pkg)
        pkg_data = await fetch_package(client, pkg)

        if pkg_data is None:
            errors.append(f"{pkg}: not found on PyPI.")
            continue

        # Bug 1 (v3): sorted_versions now falls back to pre-releases
        releases = pkg_data.get("releases", {})
        if has_only_prerelease_versions(releases):
            prerelease_pkgs.append(norm)

        all_versions = sorted_versions(releases)
        viable: list[str] = []

        for v in all_versions:
            ver_data, err = await fetch_package_version(client, pkg, v)
            if err or ver_data is None:
                continue
            info = ver_data.get("info", {})
            if check_python_compat(info.get("requires_python"), python_version):
                reqs = parse_requires_dist(info.get("requires_dist"), python_version)
                dep_cache[norm][v] = reqs
                viable.append(v)
            if len(viable) >= max_candidates:
                break

        if viable:
            candidates[norm] = viable
        else:
            errors.append(f"{pkg}: no version compatible with Python {python_version}.")

    # ------------------------------------------------------------------
    # Phase 2: Greedy pick — newest version per package
    # ------------------------------------------------------------------
    resolved: dict[str, str] = {}
    for norm, vers in candidates.items():
        resolved[norm] = vers[0]

    # ------------------------------------------------------------------
    # Phase 3: Conflict detection + constraint propagation (Bug 2 v3)
    #
    # For each conflict found, try to pick an alternate candidate version
    # of the dep that satisfies the constraint.  Repeat until stable or
    # no more candidates.
    # ------------------------------------------------------------------
    def _find_conflicts(res: dict[str, str]) -> list[dict]:
        found = []
        for norm, ver in res.items():
            for req in dep_cache[norm].get(ver, []):
                dep_norm = normalize_name(req.name)
                if dep_norm not in res:
                    continue
                dep_ver = res[dep_norm]
                if req.specifier and Version(dep_ver) not in req.specifier:
                    found.append({
                        "package": norm,
                        "dep": req.name,
                        "dep_norm": dep_norm,
                        "required": str(req.specifier),
                        "resolved": dep_ver,
                    })
        return found

    MAX_PROPAGATION_ROUNDS = 5
    for _ in range(MAX_PROPAGATION_ROUNDS):
        conflicts = _find_conflicts(resolved)
        if not conflicts:
            break
        improved = False
        for c in conflicts:
            dep_norm = c["dep_norm"]
            spec = SpecifierSet(c["required"])
            for alt_ver in candidates.get(dep_norm, []):
                try:
                    if Version(alt_ver) in spec:
                        resolved[dep_norm] = alt_ver
                        improved = True
                        break
                except InvalidVersion:
                    continue
        if not improved:
            break  # no progress — remaining conflicts are unresolvable

    final_conflicts = _find_conflicts(resolved)
    requirements_valid = len(final_conflicts) == 0

    # ------------------------------------------------------------------
    # Phase 4: Enrich with display metadata
    # ------------------------------------------------------------------
    summary_list: list[dict] = []
    for norm, ver in resolved.items():
        ver_data, _ = await fetch_package_version(client, norm, ver)
        info = ver_data.get("info", {}) if ver_data else {}
        summary_list.append({
            "package": norm,
            "version": ver,
            "requires_python": info.get("requires_python") or "any",
            "summary": (info.get("summary") or "")[:80],
            "home_page": info.get("home_page") or "",
        })

    return {
        "python_version": python_version,
        "resolved": summary_list,
        "conflicts": final_conflicts,
        "errors": errors,
        "prerelease_pkgs": prerelease_pkgs,
        "requirements_valid": requirements_valid,
    }