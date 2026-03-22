# test_server.py
import asyncio
from server import pycompat_resolve, pycompat_check_pinned, pycompat_package_info
from server import ResolveInput, CheckCompatInput, PackageInfoInput

async def main():
    # Test 1: Resolve a stack
    print("=== RESOLVE ===")
    result = await pycompat_resolve(ResolveInput(
        packages=["fastapi", "pydantic", "httpx"],
        python_version="3.11"
    ))
    print(result)

    # Test 2: Check pinned versions
    print("\n=== CHECK PINNED ===")
    result = await pycompat_check_pinned(CheckCompatInput(
        pinned={"numpy": "1.24.0", "pandas": "2.2.1"},
        python_version="3.10"
    ))
    print(result)

    # Test 3: Single package info
    print("\n=== PACKAGE INFO ===")
    result = await pycompat_package_info(PackageInfoInput(package="langchain"))
    print(result)

asyncio.run(main())