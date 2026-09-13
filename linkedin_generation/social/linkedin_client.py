"""LinkedIn publishing utilities."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"


@dataclass(frozen=True)
class LinkedInPublisherConfig:
    """Configuration required for LinkedIn publishing."""

    access_token: str
    owner_urn: str
    visibility: str = "PUBLIC"


class LinkedInPublisher:
    """Minimal client for publishing image posts to a LinkedIn organisation page."""

    def __init__(self, config: LinkedInPublisherConfig) -> None:
        if not config.access_token:
            raise ValueError("LinkedIn access token is required")
        if not config.owner_urn:
            raise ValueError("LinkedIn organisation/member URN is required")
        self.config = config

    def publish_post(
        self,
        *,
        text: str,
        headline: str,
        alt_text: str,
        image_path: Path,
    ) -> Dict[str, Any]:
        """Publish an image post and return LinkedIn response metadata."""
        # Checked before registering an upload slot with LinkedIn: a None here
        # used to reach image_path.read_bytes() and surface as an AttributeError
        # halfway through publishing, having already burned the registration.
        if image_path is None or not Path(image_path).exists():
            raise ValueError(
                f"publish_post requires an existing image file, got {image_path!r}"
            )
        asset_info = self._register_image_upload()
        upload_url = asset_info["uploadUrl"]
        asset = asset_info["asset"]

        self._upload_image(upload_url=upload_url, image_path=image_path)
        post_response = self._create_share(
            asset=asset,
            text=text,
            headline=headline,
            alt_text=alt_text,
        )

        share_urn = post_response.get("id")
        permalink = None
        if share_urn:
            permalink = f"https://www.linkedin.com/feed/update/{share_urn}"

        logging.info("LinkedIn post published with asset %s and share URN %s", asset, share_urn)

        return {
            "asset": asset,
            "share_urn": share_urn,
            "permalink": permalink,
            "response": post_response,
        }

    def publish_video_post(
        self,
        *,
        text: str,
        headline: str,
        alt_text: str,
        video_path: Path,
    ) -> Dict[str, Any]:
        """Upload an MP4 and publish it as a native LinkedIn video post."""
        asset_info = self._register_video_upload()
        upload_url = asset_info["uploadUrl"]
        asset = asset_info["asset"]

        self._upload_video(upload_url=upload_url, video_path=video_path)
        post_response = self._create_video_share(
            asset=asset,
            text=text,
            headline=headline,
            alt_text=alt_text,
        )

        share_urn = post_response.get("id")
        permalink = f"https://www.linkedin.com/feed/update/{share_urn}" if share_urn else None
        logging.info("LinkedIn video post published: asset=%s share_urn=%s", asset, share_urn)

        return {
            "asset": asset,
            "share_urn": share_urn,
            "permalink": permalink,
            "response": post_response,
        }

        # --- Internal helpers -------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def _register_image_upload(self) -> Dict[str, Any]:
        payload = {
            "registerUploadRequest": {
                "owner": self.config.owner_urn,
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }
                ],
            }
        }
        url = f"{LINKEDIN_API_BASE}/assets?action=registerUpload"
        response = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        response.raise_for_status()
        data = response.json()
        value: Dict[str, Any] = data.get("value", {})
        upload_info: Optional[Dict[str, Any]] = value.get("uploadMechanism", {}).get(
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        )
        if not upload_info:
            raise RuntimeError("LinkedIn registerUpload response missing upload URL")
        upload_url = upload_info.get("uploadUrl")
        asset = value.get("asset")
        if not upload_url or not asset:
            raise RuntimeError("LinkedIn registerUpload response missing asset/uploadUrl")
        return {
            "asset": asset,
            "uploadUrl": upload_url,
        }

    def _upload_image(self, *, upload_url: str, image_path: Path) -> None:
        binary = image_path.read_bytes()
        headers = {
            "Content-Type": "application/octet-stream",
        }
        response = requests.put(upload_url, data=binary, headers=headers, timeout=60)
        response.raise_for_status()

    def _create_share(
        self,
        *,
        asset: str,
        text: str,
        headline: str,
        alt_text: str,
    ) -> Dict[str, Any]:
        payload = {
            "author": self.config.owner_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text.strip()},
                    "shareMediaCategory": "IMAGE",
                    "media": [
                        {
                            "status": "READY",
                            "description": {"text": alt_text[:300]},
                            "media": asset,
                            "title": {"text": headline[:200]},
                        }
                    ],
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": self.config.visibility,
            },
        }
        url = f"{LINKEDIN_API_BASE}/ugcPosts"
        response = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def _register_video_upload(self) -> Dict[str, Any]:
        payload = {
            "registerUploadRequest": {
                "owner": self.config.owner_urn,
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-video"],
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }
                ],
            }
        }
        url = f"{LINKEDIN_API_BASE}/assets?action=registerUpload"
        response = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        response.raise_for_status()
        data = response.json()
        value: Dict[str, Any] = data.get("value", {})
        upload_info: Optional[Dict[str, Any]] = value.get("uploadMechanism", {}).get(
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        )
        if not upload_info:
            raise RuntimeError("LinkedIn registerUpload (video) response missing upload URL")
        upload_url = upload_info.get("uploadUrl")
        asset = value.get("asset")
        if not upload_url or not asset:
            raise RuntimeError("LinkedIn registerUpload (video) response missing asset/uploadUrl")
        return {"asset": asset, "uploadUrl": upload_url}

    def _upload_video(self, *, upload_url: str, video_path: Path) -> None:
        binary = video_path.read_bytes()
        response = requests.put(
            upload_url,
            data=binary,
            headers={"Content-Type": "application/octet-stream"},
            timeout=180,
        )
        response.raise_for_status()

    def _create_video_share(
        self,
        *,
        asset: str,
        text: str,
        headline: str,
        alt_text: str,
    ) -> Dict[str, Any]:
        payload = {
            "author": self.config.owner_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "VIDEO",
                    "media": [
                        {
                            "status": "READY",
                            "media": asset,
                            "title": {"text": headline},
                            "description": {"text": alt_text},
                        }
                    ],
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": self.config.visibility
            },
        }
        url = f"{LINKEDIN_API_BASE}/ugcPosts"
        response = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        response.raise_for_status()
        return response.json()


    def comment_on_post(self, *, share_urn: str, text: str) -> Optional[Dict[str, Any]]:
        """Add a comment to a published post — used for the source link.

        The source article link is published here rather than in the post body:
        LinkedIn suppresses the reach of posts carrying an outbound link, and the
        first comment is the standard way round it (decided 2026-09-13).

        Returns the API response, or None if commenting failed. A failed comment
        must NEVER fail the run: the post itself is already live, and losing the
        link is far better than a stack trace after publishing.
        """
        if not share_urn or not text.strip():
            return None
        payload = {
            "actor": self.config.owner_urn,
            "object": share_urn,
            "message": {"text": text.strip()[:1250]},
        }
        # The URN has to be path-encoded; an unescaped ':' is read as a path
        # separator and the call 404s.
        encoded = quote(share_urn, safe="")
        url = f"{LINKEDIN_API_BASE}/socialActions/{encoded}/comments"

        # A post is NOT commentable the instant ugcPosts returns its URN. The
        # first live run (2026-09-13) commented 391 ms after publishing and got
        # 404 "Received error ... from domain authorization endpoint"; the exact
        # same call replayed a minute later returned 201. So back off and retry,
        # and treat 404/403 as "not propagated yet" rather than as fatal.
        delays = (3, 8, 20, 40)
        last = ""
        for attempt, delay in enumerate(delays, start=1):
            time.sleep(delay)
            try:
                response = requests.post(
                    url, json=payload, headers=self._headers(), timeout=30
                )
                if response.status_code in (200, 201):
                    logging.info(
                        "Posted source link as first comment on %s (attempt %d)",
                        share_urn, attempt,
                    )
                    return response.json()
                last = f"{response.status_code} {response.text[:200]}"
                if response.status_code not in (403, 404, 429, 500, 503):
                    break
                logging.info(
                    "Comment not accepted yet on %s (%s) - retrying",
                    share_urn, response.status_code,
                )
            except Exception as exc:
                last = str(exc)
        logging.warning(
            "Could not post the source-link comment on %s after %d attempts: %s",
            share_urn, len(delays), last[:300],
        )
        return None

    def delete_post(self, urn: str) -> int:
        """Delete a published post by its share/ugcPost URN.

        LinkedIn requires the URN percent-encoded in the path. Deletion is
        permanent: the URN cannot be reused and any engagement is lost.
        Returns the HTTP status (204 on success).
        """
        from urllib.parse import quote
        url = f"{LINKEDIN_API_BASE}/ugcPosts/{quote(urn, safe='')}"
        response = requests.delete(url, headers=self._headers(), timeout=30)
        if response.status_code not in (200, 204):
            response.raise_for_status()
        return response.status_code


__all__ = ["LinkedInPublisher", "LinkedInPublisherConfig"]
