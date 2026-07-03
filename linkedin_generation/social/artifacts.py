"""Shared artifact helpers for per-brand LinkedIn schedulers.

`slugify` and `save_artifacts` are identical across brands (the only prior
difference was a parameter name and whether image_prompt was recorded — both
harmonised here). Brand schedulers import these instead of redefining them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from .image_providers import ImagePayload


def slugify(text: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    while "--" in clean:
        clean = clean.replace("--", "-")
    return clean.strip("-") or "post"


def save_artifacts(
    *,
    output_dir: Path,
    post,
    image: ImagePayload,
    extra_metadata: Optional[Dict[str, str]] = None,
) -> Path:
    timestamp = post.created_at.strftime("%Y%m%d_%H%M")
    slug = slugify(post.pillar_name)
    base_path = output_dir / f"{timestamp}_{slug}"
    output_dir.mkdir(parents=True, exist_ok=True)

    copy_path = base_path.with_suffix(".txt")
    copy_path.write_text(post.as_text)

    metadata = post.as_mapping()
    metadata["copy_file"] = copy_path.name

    if image.path:
        metadata["image_file"] = image.path.name
    if image.url:
        metadata["image_url"] = image.url
    if image.provider:
        metadata["image_provider"] = image.provider
    if image.prompt:
        metadata["image_prompt"] = image.prompt
    if image.alt_text:
        metadata["image_alt_text"] = image.alt_text

    if extra_metadata:
        metadata.update(extra_metadata)

    metadata_path = base_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, indent=2))

    return metadata_path


__all__ = ["slugify", "save_artifacts"]
