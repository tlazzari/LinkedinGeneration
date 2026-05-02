#!/usr/bin/env python3
"""Utility to print LinkedIn organisation URNs for the authenticated administrator."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Iterable, Tuple

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List LinkedIn organisation URNs available to the provided access token",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path(".env"),
        help="Path to a dotenv file containing LINKEDIN_ACCESS_TOKEN",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Override access token (takes precedence over .env)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw JSON payload in addition to the summary table",
    )
    return parser.parse_args()


def load_token(env_path: Path, override: str | None) -> str:
    if override:
        return override.strip()

    token = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if token:
        return token.strip()

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            if "=" not in cleaned:
                continue
            key, value = cleaned.split("=", 1)
            if key.strip() == "LINKEDIN_ACCESS_TOKEN":
                return value.strip()

    raise SystemExit("LinkedIn access token not found. Use --token or set LINKEDIN_ACCESS_TOKEN.")


def call_endpoint(url: str, token: str, params: Dict[str, str] | None = None) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    response = requests.get(url, headers=headers, params=params or {}, timeout=30)
    if response.status_code == 401:
        raise SystemExit("Token rejected with 401 Unauthorized. Generate a fresh access token.")
    if response.status_code == 403:
        raise SystemExit(
            "Access denied (403). Ensure the token includes r_liteprofile, r_organization_social, "
            "and rw_organization_admin permissions."
        )
    response.raise_for_status()
    return response


def fetch_identity(token: str) -> Tuple[str, Dict[str, str]]:
    response = call_endpoint("https://api.linkedin.com/v2/me", token)
    data = response.json()
    first = data.get("localizedFirstName")
    if not first:
        first_localized = data.get("firstName", {}).get("localized", {})
        if isinstance(first_localized, dict):
            first = next(iter(first_localized.values()), "")
    last = data.get("localizedLastName")
    if not last:
        last_localized = data.get("lastName", {}).get("localized", {})
        if isinstance(last_localized, dict):
            last = next(iter(last_localized.values()), "")
    display_name = " ".join(part for part in [str(first or ""), str(last or "")] if part and part.strip()).strip() or "(unknown)"
    return display_name, data


def fetch_org_acls(token: str) -> Dict[str, Dict[str, str]]:
    params = {
        "q": "roleAssignee",
        "role": "ADMINISTRATOR",
        "state": "APPROVED",
    }
    response = call_endpoint("https://api.linkedin.com/v2/organizationAcls", token, params)
    payload = response.json()
    result: Dict[str, Dict[str, str]] = {}
    for element in payload.get("elements", []):
        org = element.get("organization")
        if not org:
            continue
        result[org] = {}
    return result


def fetch_org_details(token: str, org_urn: str) -> Dict[str, str]:
    org_id = org_urn.split(":")[-1]
    url = f"https://api.linkedin.com/v2/organizations/{org_id}"
    response = call_endpoint(url, token)
    data = response.json()
    name = data.get("localizedName") or ""
    vanity = data.get("vanityName") or ""
    return {
        "name": str(name),
        "vanity": str(vanity),
    }


def main() -> None:
    args = parse_args()
    token = load_token(args.env, args.token)

    display_name, identity_raw = fetch_identity(token)
    orgs = fetch_org_acls(token)
    for urn in list(orgs.keys()):
        try:
            orgs[urn] = fetch_org_details(token, urn)
        except SystemExit:
            raise
        except Exception as exc:  # pragma: no cover - network/API issues
            orgs[urn]["name"] = "(lookup failed)"
            orgs[urn]["error"] = str(exc)

    print(f"Authenticated as: {display_name}")
    if not orgs:
        print("No administrator organisations returned.")
    else:
        print("Administrator organisations:")
        for urn, meta in orgs.items():
            label = meta.get("name") or meta.get("vanity") or "(unnamed)"
            vanity = meta.get("vanity")
            suffix = f" (vanity: {vanity})" if vanity else ""
            print(f"  - {urn}: {label}{suffix}")

    if args.raw:
        print("\nRaw /me payload:\n" + json.dumps(identity_raw, indent=2))
        org_raw = call_endpoint(
            "https://api.linkedin.com/v2/organizationAcls",
            token,
            {
                "q": "roleAssignee",
                "role": "ADMINISTRATOR",
                "state": "APPROVED",
            },
        ).json()
        print("\nRaw organizationAcls payload:\n" + json.dumps(org_raw, indent=2))


if __name__ == "__main__":
    main()
