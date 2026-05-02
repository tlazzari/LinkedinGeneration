"""Logo overlay utility for adding TNT Motion branding to generated images."""

from pathlib import Path
from PIL import Image
import logging

logger = logging.getLogger(__name__)


def add_logo_to_image(
    image_path: Path,
    logo_path: Path,
    position: str = "bottom-right",
    logo_width_percent: float = 0.15,
    margin_percent: float = 0.02,
    opacity: float = 1.0
) -> None:
    """
    Add TNT Motion logo overlay to an image.

    Args:
        image_path: Path to the image to add logo to (will be modified in-place)
        logo_path: Path to the logo PNG file
        position: Logo position - "bottom-right", "bottom-left", "top-right", "top-left"
        logo_width_percent: Logo width as percentage of image width (default: 0.15 = 15%)
        margin_percent: Margin from edges as percentage of image width (default: 0.02 = 2%)
        opacity: Logo opacity 0.0-1.0 (default: 1.0 = fully opaque)
    """
    try:
        # Load the main image
        with Image.open(image_path) as main_img:
            # Convert to RGBA if needed to support transparency
            if main_img.mode != 'RGBA':
                main_img = main_img.convert('RGBA')

            # Load and resize logo
            with Image.open(logo_path) as logo_img:
                # Convert logo to RGBA
                if logo_img.mode != 'RGBA':
                    logo_img = logo_img.convert('RGBA')

                # Calculate logo size
                img_width, img_height = main_img.size
                logo_width = int(img_width * logo_width_percent)

                # Maintain aspect ratio
                logo_aspect = logo_img.width / logo_img.height
                logo_height = int(logo_width / logo_aspect)

                # Resize logo
                logo_resized = logo_img.resize((logo_width, logo_height), Image.Resampling.LANCZOS)

                # Apply opacity if needed
                if opacity < 1.0:
                    # Create a new image with adjusted alpha
                    alpha = logo_resized.split()[3]  # Get alpha channel
                    alpha = alpha.point(lambda p: int(p * opacity))
                    logo_resized.putalpha(alpha)

                # Calculate position
                margin = int(img_width * margin_percent)

                if position == "bottom-right":
                    x = img_width - logo_width - margin
                    y = img_height - logo_height - margin
                elif position == "bottom-left":
                    x = margin
                    y = img_height - logo_height - margin
                elif position == "top-right":
                    x = img_width - logo_width - margin
                    y = margin
                elif position == "top-left":
                    x = margin
                    y = margin
                else:
                    raise ValueError(f"Invalid position: {position}")

                # Paste logo onto main image
                main_img.paste(logo_resized, (x, y), logo_resized)

                # Save back (convert to RGB if saving as JPEG)
                if image_path.suffix.lower() in ['.jpg', '.jpeg']:
                    main_img = main_img.convert('RGB')

                main_img.save(image_path)

                logger.info(f"Added logo to {image_path.name} at position {position}")

    except Exception as exc:
        logger.error(f"Failed to add logo to {image_path}: {exc}")
        # Don't raise - image generation should continue even if logo overlay fails
