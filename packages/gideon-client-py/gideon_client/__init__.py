"""gideon-client — async Python client for the Gideon Gateway.

Usage::

    from gideon_client import GideonClient

    async with GideonClient(app_name="my-app") as mc:
        ok = await mc.ping()
        status = await mc.get_status()
        await mc.send_message("session-1", "hello")
"""
from gideon_client.client import GideonClient
from gideon_client.errors import GideonError, ErrorCode

__all__ = ["GideonClient", "GideonError", "ErrorCode"]
