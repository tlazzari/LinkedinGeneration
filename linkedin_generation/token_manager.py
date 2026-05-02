# CRITICAL: Token auto-renewal module - DO NOT REMOVE unless expressly commanded
# This module handles automatic LinkedIn token refresh before expiration
#
"""LinkedIn OAuth Token Manager for TNT Motion."""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOKEN_STATE_FILE = PROJECT_ROOT / "config" / "linkedin_token_state.json"

# CLIENT_ID and CLIENT_SECRET are now read inside functions after load_dotenv()
# See refresh_linkedin_token() and ensure_valid_token()


def load_token_state() -> dict:
    """Load token state from JSON file."""
    if TOKEN_STATE_FILE.exists():
        with open(TOKEN_STATE_FILE) as f:
            return json.load(f)
    return {}


def save_token_state(state: dict) -> None:
    """Save token state to JSON file."""
    TOKEN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def update_env_file(key: str, value: str) -> None:
    """Update a single key in the .env file."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        env_file.write_text(f"{key}={value}\n")
        return

    content = env_file.read_text()
    pattern = rf"^{re.escape(key)}=.*$"

    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
    else:
        content = content.rstrip() + f"\n{key}={value}\n"

    env_file.write_text(content)


def refresh_linkedin_token(refresh_token: str) -> Optional[dict]:
    """Refresh the access token using the refresh token."""
    client_id = os.getenv("LINKEDIN_CLIENT_ID", "77dnaeeaexwjkv")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "")
    if not client_secret:
        logging.error("LINKEDIN_CLIENT_SECRET not set in environment")
        return None
    token_url = "https://www.linkedin.com/oauth/v2/accessToken"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        response = requests.post(token_url, data=data, timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            logging.error(f"Token refresh failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logging.error(f"Token refresh request failed: {e}")
        return None


def ensure_valid_token() -> Optional[str]:
    """
    Check if token needs refresh and refresh if necessary.
    Returns the valid access token or None if refresh fails.
    """
    load_dotenv(PROJECT_ROOT / ".env", override=True)

    access_token = os.getenv("LINKEDIN_ACCESS_TOKEN", "").strip().strip("'")
    refresh_token = os.getenv("LINKEDIN_REFRESH_TOKEN", "").strip().strip("'")

    if not access_token:
        logging.error("No LINKEDIN_ACCESS_TOKEN found in .env")
        return None

    if not refresh_token:
        logging.warning("No LINKEDIN_REFRESH_TOKEN found - cannot auto-refresh")
        return access_token

    state = load_token_state()
    expires_at_str = state.get("expires_at")

    should_refresh = False

    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            days_until_expiry = (expires_at - datetime.now()).days

            if days_until_expiry < 7:
                logging.info(f"Token expires in {days_until_expiry} days, refreshing...")
                should_refresh = True
            else:
                logging.info(f"Token valid for {days_until_expiry} more days")
        except ValueError:
            logging.warning("Could not parse token expiration date")
            should_refresh = True
    else:
        logging.info("No token expiration info, testing token validity...")
        test_url = "https://api.linkedin.com/v2/me"
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = requests.get(test_url, headers=headers, timeout=10)
            if resp.status_code == 401:
                logging.info("Token expired (401), refreshing...")
                should_refresh = True
            elif resp.status_code == 200:
                logging.info("Token is valid")
                expires_at = datetime.now() + timedelta(days=60)
                save_token_state({
                    "expires_at": expires_at.isoformat(),
                    "last_check": datetime.now().isoformat(),
                })
        except Exception as e:
            logging.warning(f"Token test failed: {e}")

    if should_refresh:
        token_data = refresh_linkedin_token(refresh_token)

        if not token_data:
            logging.error("Token refresh failed! Manual re-authorization may be required.")
            return access_token

        new_access_token = token_data.get("access_token")
        new_refresh_token = token_data.get("refresh_token", refresh_token)
        expires_in = token_data.get("expires_in", 5184000)

        if new_access_token:
            update_env_file("LINKEDIN_ACCESS_TOKEN", f"'{new_access_token}'")
            if new_refresh_token and new_refresh_token != refresh_token:
                update_env_file("LINKEDIN_REFRESH_TOKEN", f"'{new_refresh_token}'")

            expires_at = datetime.now() + timedelta(seconds=expires_in)
            save_token_state({
                "expires_at": expires_at.isoformat(),
                "last_refresh": datetime.now().isoformat(),
                "expires_in_seconds": expires_in,
            })

            logging.info(f"Token refreshed successfully. New expiration: {expires_at}")
            return new_access_token

    return access_token
