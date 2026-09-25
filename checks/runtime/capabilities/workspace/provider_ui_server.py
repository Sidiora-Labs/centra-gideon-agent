import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import asyncio
import json

from checks.runtime.capabilities.workspace.test_provider_terminal import (
    ProviderTerminal,
)
from gideon.interfaces.dashboard.token_auth import generate_token


async def main():
    fixture = ProviderTerminal("test_profiles_native_tool_and_real_executable")
    await fixture.asyncSetUp()
    print(
        json.dumps(
            {
                "url": fixture.url,
                "repo": str(fixture.repo),
                "token": generate_token("provider-browser"),
            }
        ),
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await fixture.asyncTearDown()


if __name__ == "__main__":
    asyncio.run(main())
