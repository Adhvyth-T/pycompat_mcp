"""
models.py — Pydantic input models for all three MCP tools.

Fixes applied:
  - Bug 5: EOL Python version warning via validator
  - Bug 6: Rejects nonsense Python versions like '3.999'
"""

import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict
from packaging.version import Version, InvalidVersion

# Known EOL Python versions (major.minor).
#
# ⚠️  MAINTAINER NOTE — update when a new version reaches EOL:
#   Python 3.10 EOL is scheduled for October 2026.
#   When it passes, add "3.10" to this set.
#   Track the full schedule at: https://devguide.python.org/versions/
EOL_PYTHON_VERSIONS = {
    "2.7", "3.0", "3.1", "3.2", "3.3", "3.4",
    "3.5", "3.6", "3.7", "3.8", "3.9",  # 3.9 EOL: Oct 5 2025
    # "3.10",  # EOL: Oct 2026 — uncomment when it passes
}

# Valid Python minor versions per major (sanity check).
#
# ⚠️  MAINTAINER NOTE — update when a new Python release ships:
#   Python 3.14 is currently in alpha (expected stable ~Oct 2025).
#   When 3.14 reaches RC/final, bump the upper bound below from 14 → 15
#   (range end is exclusive), otherwise users on 3.14 will get a
#   confusing "unknown minor version" validation error.
#   Track releases at: https://www.python.org/downloads/
VALID_PYTHON_MINORS = {
    "2": set(range(0, 8)),   # 2.0–2.7
    "3": set(range(0, 14)),  # 3.0–3.13  ← bump to 15 when 3.14 ships
}


def validate_python_version(v: str) -> str:
    """Shared Python version validator used across multiple models."""
    try:
        parsed = Version(v)
    except InvalidVersion:
        raise ValueError(f"Invalid Python version: {v!r}. Use semver like '3.11' or '3.12.1'.")

    major = str(parsed.major)
    minor = parsed.minor

    if major not in VALID_PYTHON_MINORS:
        raise ValueError(f"Unknown Python major version: {major}. Expected 2 or 3.")

    if minor not in VALID_PYTHON_MINORS[major]:
        raise ValueError(
            f"Unknown Python minor version: {major}.{minor}. "
            f"Valid range for Python {major}: {major}.0–{major}.{max(VALID_PYTHON_MINORS[major])}."
        )

    return v


def eol_warning(python_version: str) -> Optional[str]:
    """Return an EOL warning string if the version is end-of-life, else None."""
    parsed = Version(python_version)
    key = f"{parsed.major}.{parsed.minor}"
    if key in EOL_PYTHON_VERSIONS:
        return (
            f"⚠️  Python {key} is end-of-life and no longer receives security updates. "
            f"Consider upgrading to Python 3.11 or later."
        )
    return None


def normalize_name(name: str) -> str:
    """Normalize a package name per PEP 503."""
    import re
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class ResolveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    packages: list[str] = Field(
        ...,
        description="List of PyPI package names to resolve (e.g. ['fastapi', 'pydantic', 'httpx'])",
        min_length=1,
        max_length=30,
    )
    python_version: str = Field(
        ...,
        description="Target Python version string (e.g. '3.11', '3.12.2')",
        examples=["3.11", "3.12", "3.10.4"],
    )
    max_candidates: int = Field(
        default=5,
        description="How many version candidates to evaluate per package (higher = slower but more thorough)",
        ge=1,
        le=20,
    )

    @field_validator("python_version")
    @classmethod
    def check_python_version(cls, v: str) -> str:
        return validate_python_version(v)

    @field_validator("packages")
    @classmethod
    def check_no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(normalize_name(p) for p in v)) != len(v):
            raise ValueError("Duplicate package names detected.")
        return v


class PackageInfoInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    package: str = Field(
        ...,
        description="PyPI package name (e.g. 'numpy')",
        min_length=1,
        max_length=100,
    )
    version: Optional[str] = Field(
        default=None,
        description="Specific version to fetch. Omit to get the latest.",
    )


class CheckCompatInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    pinned: dict[str, str] = Field(
        ...,
        description=(
            "Dict of package-name → exact version to check "
            "(e.g. {'numpy': '1.26.4', 'pandas': '2.2.1'})"
        ),
    )
    python_version: str = Field(
        ...,
        description="Target Python version string (e.g. '3.11')",
    )

    @field_validator("python_version")
    @classmethod
    def check_python_version(cls, v: str) -> str:
        return validate_python_version(v)

    @field_validator("pinned")
    @classmethod
    def check_no_duplicate_normalized_names(cls, v: dict[str, str]) -> dict[str, str]:
        """Bug 3 fix: catch duplicates like {'Numpy': '1.26.4', 'numpy': '1.26.3'}
        that collapse to the same normalized name and would silently drop one."""
        seen: dict[str, str] = {}
        for raw_name in v:
            norm = normalize_name(raw_name)
            if norm in seen:
                raise ValueError(
                    f"Duplicate package after normalization: '{raw_name}' and '{seen[norm]}' "
                    f"both normalize to '{norm}'. Remove one entry."
                )
            seen[norm] = raw_name
        return v


class LatestVersionsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    packages: list[str] = Field(
        ...,
        description=(
            "List of PyPI package names to look up (e.g. ['numpy', 'pandas', 'fastapi']). "
            "Returns the latest stable version for each. Pre-release versions are included "
            "only when no stable version exists."
        ),
        min_length=1,
        max_length=50,
    )
    include_prerelease: bool = Field(
        default=False,
        description=(
            "If True, includes the latest pre-release version alongside the latest stable "
            "version when they differ (e.g. stable: 2.0.0, prerelease: 2.1.0b1). "
            "Useful for tracking bleeding-edge releases."
        ),
    )

    @field_validator("packages")
    @classmethod
    def check_no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(normalize_name(p) for p in v)) != len(v):
            raise ValueError("Duplicate package names detected.")
        return v