"""Vobiz REST API helper (shared by the batch runner's real dialer)."""

import json
import os

import aiohttp
from loguru import logger


async def make_call(
    session: aiohttp.ClientSession,
    *,
    to_number: str,
    from_number: str,
    answer_url: str,
    timeout: int = 30,
) -> dict:
    """Place an outbound Vobiz call. Returns the parsed JSON response."""
    auth_id = os.getenv("VOBIZ_AUTH_ID")
    auth_token = os.getenv("VOBIZ_AUTH_TOKEN")
    if not auth_id:
        raise ValueError("Missing Vobiz Auth ID (VOBIZ_AUTH_ID)")
    if not auth_token:
        raise ValueError("Missing Vobiz Auth Token (VOBIZ_AUTH_TOKEN)")
    if not from_number:
        raise ValueError("Missing Vobiz caller-ID (VOBIZ_PHONE_NUMBER)")

    headers = {
        "Content-Type": "application/json",
        "X-Auth-ID": auth_id,
        "X-Auth-Token": auth_token,
    }
    data = {
        "to": to_number,
        "from": from_number,
        "answer_url": answer_url,
        "answer_method": "POST",
    }
    url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/"
    logger.info(f"Placing Vobiz call to {to_number} from {from_number}")
    async with session.post(
        url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=timeout)
    ) as response:
        text = await response.text()
        if response.status != 201:
            raise RuntimeError(f"Vobiz API error ({response.status}): {text}")
        return json.loads(text)
