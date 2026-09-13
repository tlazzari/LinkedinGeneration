"""Deliver a finished post by email instead of publishing it to LinkedIn.

WHY THIS EXISTS (2026-09-13). Publishing straight to a LinkedIn page needs an
organisation URN and an access token with w_organization_social, obtained by
someone with admin rights on that page who is willing to run an OAuth flow and
hand us the result. Plenty of Bolla tenants will not do that - some cannot,
because the page is administered by someone outside the company. Without a
fallback the whole social feature is unavailable to them.

So: generate exactly as normal - same news, same quality gate, same media - and
send the finished post to the tenant to paste in themselves. The email carries
the post text ready to copy, the first comment separately (the source links
belong there, not in the body - LinkedIn demotes posts with outbound links), the
alt text, and the image or video as an attachment.

Sender must stay a Brevo-verified address (info@tntbearings.com or
tlazzari@seta-capital.com); anything else is rejected by Brevo, not by us.
"""

from __future__ import annotations

import base64
import html
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import requests

logger = logging.getLogger(__name__)

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"

# Brevo caps a message at 10 MB total and base64 inflates by about a third, so
# the raw file has to stay under ~7 MB. A Veo clip can exceed that; when it does
# the post still goes out, with the server path named instead of the file.
MAX_ATTACHMENT_BYTES = 6_500_000

VERIFIED_SENDERS = ("info@tntbearings.com", "tlazzari@seta-capital.com")
DEFAULT_SENDER = "info@tntbearings.com"


def delivery_mode(brand_key: str) -> str:
    """'email' or 'linkedin' for this brand, from its CRM-edited config.

    Read from the config file rather than the Brand dataclass on purpose: Brand
    is frozen and shared, and a tenant preference is data, not code.
    """
    try:
        from .brand_store import load_configs
        config = load_configs().get(brand_key) or {}
    except Exception as exc:                      # never break a scheduled run
        logger.warning("Could not read delivery mode for '%s': %s", brand_key, exc)
        return "linkedin"
    mode = str(config.get("delivery") or "linkedin").strip().lower()
    return "email" if mode == "email" else "linkedin"


def delivery_recipients(brand_key: str) -> List[str]:
    """Who receives the post for this brand. Empty means nobody is configured."""
    try:
        from .brand_store import load_configs
        config = load_configs().get(brand_key) or {}
    except Exception:
        return []
    raw = config.get("notify_email") or ""
    if isinstance(raw, (list, tuple)):
        candidates = [str(x) for x in raw]
    else:
        candidates = str(raw).replace(";", ",").split(",")
    return [c.strip() for c in candidates if "@" in c.strip()]


def _attachment(media_path: Optional[Path]) -> tuple[Optional[Dict[str, str]], str]:
    """(brevo attachment, human note). Never raises - the email matters more."""
    if not media_path:
        return None, "No image or video was produced for this post."
    path = Path(media_path)
    if not path.exists():
        return None, f"Media file is missing on the server ({path})."
    size = path.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        return None, (
            f"The {path.suffix.lstrip('.').upper()} is {size / 1_000_000:.1f} MB, too large to "
            f"attach. It is on the server at {path} - ask for it and it will be sent separately."
        )
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        return None, f"Media file could not be read ({exc})."
    return {"name": path.name, "content": encoded}, ""


def _box(title: str, content: str, mono: bool = True) -> str:
    style = (
        "background:#f6f7f9;border:1px solid #dde1e6;border-radius:6px;padding:14px;"
        "white-space:pre-wrap;word-break:break-word;font-size:14px;line-height:1.55;"
        + ("font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" if mono else "")
    )
    return (
        f'<p style="margin:22px 0 6px;font-size:13px;font-weight:600;color:#444;'
        f'text-transform:uppercase;letter-spacing:.04em">{html.escape(title)}</p>'
        f'<div style="{style}">{html.escape(content)}</div>'
    )


def build_email(
    *,
    display_name: str,
    post_text: str,
    headline: str,
    alt_text: str,
    first_comment: str = "",
    media_note: str = "",
    media_name: str = "",
) -> tuple[str, str]:
    """(subject, html body) for a post the recipient will publish by hand."""
    subject = f"LinkedIn post ready to publish — {display_name}: {headline[:70]}"

    h = ['<div style="font-family:Segoe UI,Arial,sans-serif;max-width:680px;margin:0 auto">']
    h.append(
        '<div style="background:#1B2A4A;color:#fff;padding:18px 20px;border-radius:8px 8px 0 0">'
        f'<h2 style="margin:0;font-size:18px">Your LinkedIn post is ready</h2>'
        f'<p style="margin:6px 0 0;font-size:13px;opacity:.85">{html.escape(display_name)}</p></div>'
    )
    h.append('<div style="background:#fff;border:1px solid #ddd;border-top:0;'
             'border-radius:0 0 8px 8px;padding:20px">')
    # Only promise a second block when there IS one: a post with no sources that
    # still says "then post the first comment" sends the reader looking for
    # something that is not in the email.
    intro = ('Copy the text below into LinkedIn and attach the file from this email.')
    if first_comment:
        intro += (
            ' Then post the second block as the <b>first comment</b> — the source links '
            'go there rather than in the post itself, because LinkedIn shows a post to '
            'fewer people when it carries a link to another site.'
        )
    h.append(
        f'<p style="font-size:14px;color:#333;line-height:1.6;margin:0">{intro}</p>'
    )
    h.append(_box("1. Post text — copy this into LinkedIn", post_text))
    if first_comment:
        h.append(_box("2. First comment — post this straight after", first_comment))
    if media_name:
        h.append(
            f'<p style="margin:22px 0 6px;font-size:13px;font-weight:600;color:#444;'
            f'text-transform:uppercase;letter-spacing:.04em">'
            f'{3 if first_comment else 2}. Attached media</p>'
            f'<p style="font-size:14px;color:#333;margin:0">'
            f'<code>{html.escape(media_name)}</code> is attached to this email.</p>'
        )
    if media_note:
        h.append(f'<p style="font-size:13px;color:#8a6d3b;margin:10px 0 0">'
                 f'{html.escape(media_note)}</p>')
    if alt_text:
        h.append(_box("Image description (accessibility)", alt_text, mono=False))
    h.append(
        '<p style="font-size:12px;color:#888;margin-top:22px;border-top:1px solid #eee;'
        'padding-top:12px">Written and checked automatically, then sent for you to publish. '
        'Nothing has been posted to LinkedIn.</p>'
    )
    h.append("</div></div>")
    return subject, "".join(h)


def send_post_email(
    *,
    display_name: str,
    recipients: Sequence[str],
    post_text: str,
    headline: str,
    alt_text: str = "",
    first_comment: str = "",
    media_path: Optional[Path] = None,
    sender: str = DEFAULT_SENDER,
    api_key: Optional[str] = None,
    dry_run: bool = False,
) -> bool:
    """Send the finished post for manual publishing. False on any failure.

    Never raises: a delivery problem must not take down a scheduled run that has
    already done the expensive work of writing the post and rendering the media.
    """
    if not recipients:
        logger.error("Email delivery requested but no recipient is configured")
        return False
    if sender not in VERIFIED_SENDERS:
        logger.warning("Sender %s is not Brevo-verified; falling back to %s",
                       sender, DEFAULT_SENDER)
        sender = DEFAULT_SENDER

    attachment, media_note = _attachment(media_path)
    subject, body = build_email(
        display_name=display_name,
        post_text=post_text,
        headline=headline,
        alt_text=alt_text,
        first_comment=first_comment,
        media_note=media_note,
        media_name=attachment["name"] if attachment else "",
    )

    if dry_run:
        logger.info("DRY RUN - would email '%s' to %s", subject, ", ".join(recipients))
        print(subject)
        print(body)
        return True

    key = api_key or os.getenv("BREVO_API_KEY", "")
    if not key:
        logger.error("BREVO_API_KEY is not set - cannot deliver the post by email")
        return False

    payload: Dict[str, object] = {
        "sender": {"email": sender, "name": display_name or "Social"},
        "to": [{"email": r} for r in recipients],
        "subject": subject,
        "htmlContent": body,
    }
    if attachment:
        payload["attachment"] = [attachment]

    try:
        response = requests.post(
            BREVO_ENDPOINT,
            json=payload,
            headers={"api-key": key, "Content-Type": "application/json",
                     "accept": "application/json"},
            timeout=60,
        )
        if response.status_code not in (200, 201, 202):
            logger.error("Brevo rejected the post email: %s %s",
                         response.status_code, response.text[:300])
            return False
        logger.info("Post emailed to %s for manual publishing", ", ".join(recipients))
        return True
    except Exception as exc:
        logger.error("Could not email the post: %s", exc)
        return False


__all__ = [
    "MAX_ATTACHMENT_BYTES",
    "VERIFIED_SENDERS",
    "DEFAULT_SENDER",
    "delivery_mode",
    "delivery_recipients",
    "build_email",
    "send_post_email",
]
