import asyncio
import ssl
from urllib.parse import urlsplit


async def certificate_status(url: str) -> str:
    """Check the source certificate without changing download behavior."""
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        return "not_applicable"
    host = parsed.hostname
    if not host:
        return "unavailable"
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host,
                parsed.port or 443,
                ssl=ssl.create_default_context(),
                server_hostname=host,
                happy_eyeballs_delay=0.25,
            ),
            timeout=5,
        )
        return "valid"
    except ssl.SSLCertVerificationError:
        return "invalid"
    except (TimeoutError, OSError, ssl.SSLError):
        return "unavailable"
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass
