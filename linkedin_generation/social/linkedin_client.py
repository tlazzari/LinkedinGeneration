"""LinkedIn publishing utilities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

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


__all__ = ["LinkedInPublisher", "LinkedInPublisherConfig"]
