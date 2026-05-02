#!/usr/bin/env python3
"""Helper to exercise LinkedIn scheduler scenarios locally."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_CONFIG = PROJECT_ROOT / "config" / "linkedin_testing.yaml"
HOLIDAY_CONFIG = PROJECT_ROOT / "config" / "holiday_campaign.yaml"

ROTATION_STATE_FILENAME = "scheduler_state.json"
CAMPAIGN_STATE_FILENAME = "campaign_state.json"


def ensure_today_schedule(base_config: Path) -> Path:
    data = yaml.safe_load(base_config.read_text()) or {}
    today = datetime.now().strftime("%A")
    data.setdefault("schedule", {})["slots"] = [
        {"day": today, "time": "09:00"}
    ]
    # ensure we write to a testing output dir
    output_cfg: Dict[str, str] = data.setdefault("output", {})
    output_cfg.setdefault("directory", "linkedin_generation/testing_posts")

    fd, temp_path = tempfile.mkstemp(prefix="linkedin_test_config_", suffix=".yaml")
    os.close(fd)
    Path(temp_path).write_text(yaml.safe_dump(data, sort_keys=False))
    return Path(temp_path)


def rotation_state_path(config_path: Path) -> Path:
    cfg = yaml.safe_load(config_path.read_text())
    output_dir = cfg.get("output", {}).get("directory", "linkedin_generation/linkedin_posts")
    return (PROJECT_ROOT / output_dir).resolve()


def write_rotation_state(output_dir: Path, *, last_post_type: str, last_image_mode: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / ROTATION_STATE_FILENAME).write_text(
        json.dumps({
            "last_post_type": last_post_type,
            "last_image_mode": last_image_mode,
        })
    )
    (output_dir / CAMPAIGN_STATE_FILENAME).write_text(json.dumps({"last_pillar_index": -1}))


def cleanup_outputs(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)


def run_scheduler(config_path: Path, publish: bool, log_level: str) -> int:
    args: List[str] = [
        sys.executable,
        str(PROJECT_ROOT / "linkedin_generation" / "linkedin_post_scheduler.py"),
        "--daily",
        "--campaign-config",
        str(config_path),
        "--holiday-config",
        str(HOLIDAY_CONFIG),
        "--log-level",
        log_level,
    ]
    if publish:
        args.append("--publish")

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(PROJECT_ROOT))
    completed = subprocess.run(args, env=env, check=False)
    return completed.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LinkedIn scheduler testing helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=DEFAULT_BASE_CONFIG, help="Base config to clone")
    common.add_argument("--log-level", default="INFO")

    tech = subparsers.add_parser("technical-video", parents=[common], help="Generate technical post with video imagery")
    tech.add_argument("--publish", action="store_true")

    promo = subparsers.add_parser("promotional-photo", parents=[common], help="Generate promotional post with photo imagery")
    promo.add_argument("--publish", action="store_true")

    cleanup = subparsers.add_parser("cleanup", parents=[common], help="Remove testing outputs and state")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_config: Path = args.config
    if not base_config.exists():
        raise SystemExit(f"Base config {base_config} not found")

    output_dir = rotation_state_path(base_config)

    if args.command == "cleanup":
        cleanup_outputs(output_dir)
        print(f"Removed testing output under {output_dir}")
        return

    temp_config = ensure_today_schedule(base_config)
    try:
        if args.command == "technical-video":
            write_rotation_state(output_dir, last_post_type="promotional", last_image_mode="photo")
        elif args.command == "promotional-photo":
            write_rotation_state(output_dir, last_post_type="technical", last_image_mode="video")
        else:
            raise SystemExit(f"Unknown command {args.command}")

        code = run_scheduler(
            config_path=temp_config,
            publish=getattr(args, "publish", False),
            log_level=args.log_level,
        )
        if code != 0:
            raise SystemExit(code)
        print("Run complete. Inspect artefacts under", output_dir)
    finally:
        if temp_config.exists():
            temp_config.unlink()


if __name__ == "__main__":
    main()
