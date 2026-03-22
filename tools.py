"""
tools.py — MCP tool definitions (v3).

All v2 + v3 bug fixes are wired through pypi.py / resolver.py / models.py.

v3 changes surfaced here:
  - Bug 1: pre-release warning shown when a package resolved to a pre-release
  - Bug 2: requirements.txt block suppressed when unresolvable conflicts remain
  - Bug 4: requirements.txt block suppressed when resolved list is empty
  - Bug 5: normalize_requires_python applied before rendering requires_python field
"""

import json

import httpx
from packaging.version import Version

from mcp.server.fastmcp import FastMCP

from models import ResolveInput, PackageInfoInput, CheckCompatInput, LatestVersionsInput, eol_warning
from pypi import (
    fetch_package,
    fetch_package_version,
    fetch_latest_version,
    normalize_name,
    check_python_compat,
    parse_requires_dist,
    normalize_requires_python,
    clean_license,
    extract_latest_versions,
    has_only_prerelease_versions,
)
from resolver import resolve


def register_tools(mcp: FastMCP) -> None:

    # ------------------------------------------------------------------
    # Tool 1: Resolve
    # ------------------------------------------------------------------
    @mcp.tool(
        name="pycompat_resolve",
        annotations={
            "title": "Resolve compatible package versions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def pycompat_resolve(params: ResolveInput) -> str:
        """Resolve the most recent mutually compatible versions for a set of PyPI
        packages and a target Python version.

        Fetches live PyPI metadata, filters by requires_python, performs greedy
        newest-first resolution with constraint propagation for conflicts, and
        returns a resolution report.

        Args:
            params (ResolveInput):
                - packages (list[str]): PyPI package names
                - python_version (str): Target Python version e.g. '3.11'
                - max_candidates (int): Versions to evaluate per package (default 5)

        Returns:
            str: Markdown report — resolved table, requirements.txt block (only
                 when no unresolvable conflicts), EOL/pre-release warnings, errors.
        """
        async with httpx.AsyncClient() as client:
            result = await resolve(
                client,
                params.packages,
                params.python_version,
                params.max_candidates,
            )

        lines = [f"## Dependency resolution — Python {result['python_version']}"]

        # EOL warning
        eol = eol_warning(params.python_version)
        if eol:
            lines += ["", eol]

        # Bug 1 (v3): pre-release warning
        if result.get("prerelease_pkgs"):
            pkgs = ", ".join(f"`{p}`" for p in result["prerelease_pkgs"])
            lines += [
                "",
                f"⚠️  The following package(s) have **only pre-release versions** on PyPI "
                f"and were resolved to a pre-release: {pkgs}",
            ]

        lines += [
            "",
            "| Package | Version | Requires Python | Summary |",
            "|---------|---------|-----------------|---------|",
        ]
        for pkg in result["resolved"]:
            # Bug 5 (v3): normalize requires_python before rendering
            rp = normalize_requires_python(pkg["requires_python"]) or "any"
            pre_tag = " *(pre-release)*" if pkg["package"] in result.get("prerelease_pkgs", []) else ""
            lines.append(
                f"| `{pkg['package']}` | `{pkg['version']}`{pre_tag} "
                f"| `{rp}` | {pkg['summary']} |"
            )

        # Bug 2 + Bug 4 (v3): only emit requirements.txt when valid and non-empty
        if result["resolved"] and result["requirements_valid"]:
            lines += ["", "### 📋 requirements.txt", "```", f"# Python {result['python_version']}"]
            for pkg in result["resolved"]:
                lines.append(f"{pkg['package']}=={pkg['version']}")
            lines.append("```")
        elif result["resolved"] and not result["requirements_valid"]:
            # UX improvement: if any conflict uses an exact-pin (==) specifier,
            # the propagation loop may have simply not fetched that specific version.
            # Suggest increasing max_candidates as the actionable fix.
            has_exact_pin = any(
                c["required"].startswith("==")
                for c in result["conflicts"]
            )
            suppression_hint = (
                " Try increasing `max_candidates` (e.g. to 10) — the required exact "
                "version may not have been fetched in the current candidate window."
                if has_exact_pin else ""
            )
            lines += [
                "",
                "### ⚠️ requirements.txt suppressed",
                f"> Unresolvable conflicts were detected. The version set above would "
                f"produce a broken `pip install`.{suppression_hint}",
            ]

        # Conflicts
        if result["conflicts"]:
            lines += ["", "### ⚠️ Conflicts detected"]
            for c in result["conflicts"]:
                lines.append(
                    f"- **{c['package']}** requires "
                    f"`{c['dep']} {c['required']}` but resolved version is `{c['resolved']}`"
                )

        # Errors
        if result["errors"]:
            lines += ["", "### ❌ Errors"]
            for e in result["errors"]:
                lines.append(f"- {e}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool 2: Package info
    # ------------------------------------------------------------------
    @mcp.tool(
        name="pycompat_package_info",
        annotations={
            "title": "Get package metadata from PyPI",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def pycompat_package_info(params: PackageInfoInput) -> str:
        """Fetch detailed metadata for a single PyPI package.

        All v2 fixes apply. Additionally:
          - Bug 1 (v3): flags pre-release-only packages in the response.
          - Bug 5 (v3): normalizes requires_python before returning.

        Args:
            params (PackageInfoInput):
                - package (str): PyPI package name
                - version (Optional[str]): Specific version; omit for latest

        Returns:
            str: JSON-formatted package metadata
        """
        async with httpx.AsyncClient() as client:
            pkg_data = await fetch_package(client, params.package)
            if pkg_data is None:
                return json.dumps({"error": f"Package '{params.package}' not found on PyPI."})

            latest_versions = extract_latest_versions(pkg_data)
            only_prerelease = has_only_prerelease_versions(pkg_data.get("releases", {}))

            if params.version:
                ver_data, err = await fetch_package_version(
                    client, params.package, params.version
                )
                if err:
                    return json.dumps({"error": err, "latest_versions": latest_versions})
                info = ver_data.get("info", {})
            else:
                info = pkg_data.get("info", {})

        result = {
            "name": info.get("name"),
            "version": info.get("version"),
            "summary": info.get("summary"),
            # Bug 5 (v3): normalize requires_python
            "requires_python": normalize_requires_python(info.get("requires_python")) or "any",
            "license": clean_license(info),
            "author": info.get("author"),
            "home_page": info.get("home_page"),
            "requires_dist": info.get("requires_dist") or [],
            "latest_versions": latest_versions,
            # Bug 1 (v3): surface pre-release-only flag
            "only_prerelease_versions": only_prerelease,
        }
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Tool 3: Check pinned
    # ------------------------------------------------------------------
    @mcp.tool(
        name="pycompat_check_pinned",
        annotations={
            "title": "Check compatibility of pinned package versions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def pycompat_check_pinned(params: CheckCompatInput) -> str:
        """Check whether a set of already-pinned packages are compatible with each
        other and with a target Python version.

        Args:
            params (CheckCompatInput):
                - pinned (dict[str, str]): package-name → exact version
                - python_version (str): Target Python version

        Returns:
            str: Markdown compatibility report with per-package status.
        """
        pinned_norm = {normalize_name(k): v for k, v in params.pinned.items()}
        pkg_meta: dict[str, dict] = {}
        errors: list[str] = []

        async with httpx.AsyncClient() as client:
            for pkg, ver in pinned_norm.items():
                ver_data, err = await fetch_package_version(client, pkg, ver)
                if err:
                    errors.append(err)
                else:
                    pkg_meta[pkg] = ver_data.get("info", {})

        lines = [f"## Compatibility check — Python {params.python_version}"]

        eol = eol_warning(params.python_version)
        if eol:
            lines += ["", eol]

        lines += [
            "",
            "| Package | Version | Python OK | Notes |",
            "|---------|---------|-----------|-------|",
        ]

        all_conflicts: list[str] = []

        for pkg, info in pkg_meta.items():
            ver = pinned_norm[pkg]
            py_ok = check_python_compat(info.get("requires_python"), params.python_version)
            py_icon = "✅" if py_ok else "❌"
            # Bug 5 (v3): normalize requires_python for display
            rp = normalize_requires_python(info.get("requires_python")) or "any"

            dep_notes: list[str] = []
            for req in parse_requires_dist(info.get("requires_dist"), params.python_version):
                dep_norm = normalize_name(req.name)
                if dep_norm not in pinned_norm:
                    continue
                dep_ver = pinned_norm[dep_norm]
                if req.specifier and Version(dep_ver) not in req.specifier:
                    note = f"`{req.name} {req.specifier}` required, got `{dep_ver}`"
                    dep_notes.append(note)
                    all_conflicts.append(f"**{pkg}=={ver}** needs {note}")

            notes = "; ".join(dep_notes) if dep_notes else f"requires_python: `{rp}`"
            lines.append(f"| `{pkg}` | `{ver}` | {py_icon} | {notes} |")

        if all_conflicts:
            lines += ["", "### ⚠️ Conflicts"]
            for c in all_conflicts:
                lines.append(f"- {c}")
            lines += [
                "",
                "> **Note:** This tool detects metadata-declared constraint violations only.",
                "> ABI/runtime-level incompatibilities (e.g. numpy 2.0 C-ABI break) are",
                "> not visible in PyPI metadata and cannot be caught here.",
            ]
        else:
            lines += ["", "### ✅ No cross-package conflicts detected."]

        if errors:
            lines += ["", "### ❌ Errors"]
            for e in errors:
                lines.append(f"- {e}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool 4: Latest versions (bulk, lightweight)
    # ------------------------------------------------------------------
    @mcp.tool(
        name="pycompat_latest_versions",
        annotations={
            "title": "Get latest PyPI version for multiple packages",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def pycompat_latest_versions(params: LatestVersionsInput) -> str:
        """Get the latest stable PyPI version for a list of packages in a single call.

        Faster and lighter than pycompat_package_info — only fetches version numbers
        and publish dates, no full metadata. Supports up to 50 packages per call.

        Automatically falls back to pre-release versions for packages that have no
        stable release (e.g. opentelemetry-instrumentation-* family).

        Use this tool when you want to:
          - Quickly check what the latest version of several packages is
          - Compare current pinned versions against what is available on PyPI
          - Build a dependency overview without needing full metadata

        For full metadata (license, requires_dist, etc.) use pycompat_package_info.
        For compatibility-aware resolution use pycompat_resolve.

        Args:
            params (LatestVersionsInput):
                - packages (list[str]): Up to 50 PyPI package names
                - include_prerelease (bool): Also return latest pre-release when
                  it is newer than the latest stable (default False)

        Returns:
            str: Markdown table with columns:
                 Package | Latest Stable | Pre-release | Published | PyPI Link
        """
        import asyncio

        async with httpx.AsyncClient() as client:
            tasks = [
                fetch_latest_version(client, pkg, params.include_prerelease)
                for pkg in params.packages
            ]
            results = await asyncio.gather(*tasks)

        has_prerelease_col = params.include_prerelease

        # Build header
        if has_prerelease_col:
            lines = [
                "| Package | Latest Stable | Latest Pre-release | Published | PyPI |",
                "|---------|--------------|-------------------|-----------|------|",
            ]
        else:
            lines = [
                "| Package | Latest Stable | Published | PyPI |",
                "|---------|--------------|-----------|------|",
            ]

        errors: list[str] = []

        for r in results:
            if "error" in r:
                errors.append(f"- `{r['package']}`: {r['error']}")
                continue

            pkg = r["package"]
            stable = r.get("latest_stable")
            only_pre = r.get("only_prerelease", False)
            published = r.get("published_on") or "—"
            pypi_url = r.get("pypi_url", "")
            link = f"[↗]({pypi_url})" if pypi_url else "—"

            # Version display — flag pre-release-only packages
            if stable:
                stable_display = f"`{stable}`"
            else:
                fallback = next(
                    (v for v in [r.get("latest_prerelease")] if v), "not found"
                )
                stable_display = f"`{fallback}` *(pre-release only)*"

            if has_prerelease_col:
                pre = r.get("latest_prerelease")
                pre_display = f"`{pre}`" if pre else "—"
                lines.append(
                    f"| `{pkg}` | {stable_display} | {pre_display} | {published} | {link} |"
                )
            else:
                lines.append(
                    f"| `{pkg}` | {stable_display} | {published} | {link} |"
                )

        if errors:
            lines += ["", "### ❌ Not found"]
            lines.extend(errors)

        return "\n".join(lines)