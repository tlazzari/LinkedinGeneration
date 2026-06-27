"""Image provider abstractions for LinkedIn assets."""

from __future__ import annotations

import base64
import os
import random
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Protocol, Sequence

from openai import OpenAI
import requests
from google import genai


from linkedin_generation.social.logo_overlay import add_logo_to_image

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageProviderConfig:
    """Configuration for sourcing or generating images."""

    provider: str = "openai"
    model: str = "gpt-image-1"
    size: str = "1024x1024"
    curated_library: Sequence[Dict[str, str]] = field(default_factory=list)
    style_hint: str | None = None
    use_animated_gif: bool = False
    gif_num_frames: int = 6
    gif_frame_duration: int = 1000
    aspect_ratio: str | None = None

    @classmethod
    def from_mapping(cls, data: Dict[str, Any]) -> "ImageProviderConfig":
        provider = str(data.get("provider", "openai")).strip().lower()
        curated_raw: Iterable[Any] = data.get("curated_library", []) or []
        curated: list[dict[str, str]] = []
        for entry in curated_raw:
            if isinstance(entry, str):
                curated.append({"url": entry})
            elif isinstance(entry, dict):
                curated.append({k: str(v) for k, v in entry.items() if isinstance(v, (str, int, float))})
        return cls(
            provider=provider,
            model=str(data.get("model", "gpt-image-1")),
            size=str(data.get("size", "1024x1024")),
            curated_library=tuple(curated),
            style_hint=data.get("style_hint"),
            use_animated_gif=bool(data.get("use_animated_gif", False)),
            gif_num_frames=int(data.get("gif_num_frames", 6)),
            gif_frame_duration=int(data.get("gif_frame_duration", 1000)),
            aspect_ratio=data.get("aspect_ratio"),
        )


@dataclass
class ImagePayload:
    """Details about a generated or sourced image."""

    prompt: str
    provider: str
    path: Optional[Path] = None
    url: Optional[str] = None
    alt_text: Optional[str] = None


class ImageProvider(Protocol):
    """Protocol for classes that return marketing-ready imagery."""

    def get_image(self, *, prompt: str, target_dir: Path, alt_text: str | None = None) -> ImagePayload:
        ...


class OpenAIImageProvider:
    """Generate on-brand visuals via OpenAI image API."""

    def __init__(self, config: ImageProviderConfig) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set for OpenAI image generation")
        self.config = config
        self.client = OpenAI(api_key=api_key)

    def get_image(self, *, prompt: str, target_dir: Path, alt_text: str | None = None) -> ImagePayload:
        enhanced_prompt = prompt
        if self.config.style_hint:
            enhanced_prompt = f"{prompt}\nStyle: {self.config.style_hint}."

        response = self.client.images.generate(
            model=self.config.model,
            prompt=enhanced_prompt,
            size=self.config.size,
        )

        image_data = response.data[0].b64_json
        if not image_data:
            raise RuntimeError("OpenAI image API returned empty payload")

        image_bytes = base64.b64decode(image_data)
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"linkedin_image_{response.created}.png"
        path = target_dir / filename
        path.write_bytes(image_bytes)
        return ImagePayload(
            prompt=enhanced_prompt,
            provider="openai",
            path=path,
            alt_text=alt_text,
        )


class OpenRouterImageProvider:
    """Generate visuals via OpenRouter-compatible image models."""

    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, config: ImageProviderConfig) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY must be set for OpenRouter image generation")

        self.config = config
        self.api_key = api_key
        self.base_url = os.getenv("OPENROUTER_BASE_URL", self.DEFAULT_BASE_URL).rstrip("/")
        self.site_url = os.getenv("OPENROUTER_SITE_URL", "https://tntbearings.com")
        self.app_name = os.getenv("OPENROUTER_APP_NAME", "TNT Motion Scheduler")

    def get_image(self, *, prompt: str, target_dir: Path, alt_text: str | None = None) -> ImagePayload:
        enhanced_prompt = prompt
        if self.config.style_hint:
            enhanced_prompt = f"{prompt}\nStyle: {self.config.style_hint}."

        model = self.config.model or "google/gemini-2.5-flash-image-preview"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Referer": self.site_url,
            "X-Title": self.app_name,
            "Accept": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": enhanced_prompt,
                }
            ],
            "modalities": ["image", "text"],
        }

        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=90,
        )

        if response.status_code == 429:
            raise RuntimeError("OpenRouter rate limit hit for image generation")
        if response.status_code >= 500:
            raise RuntimeError(f"OpenRouter image service unavailable ({response.status_code})")
        if response.status_code >= 400:
            logger.error(
                "OpenRouter chat image error (%s): %s",
                response.status_code,
                response.text[:500],
            )
            response.raise_for_status()
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError as exc:
            logger.error(
                "OpenRouter chat response was not JSON (status %s): %s",
                response.status_code,
                response.text[:500],
            )
            raise RuntimeError("OpenRouter image API returned non-JSON payload") from exc

        choices = data.get("choices") or []
        if not choices:
            logger.error("OpenRouter image response missing choices: %s", data)
            raise RuntimeError("OpenRouter image API returned empty response")

        message = choices[0].get("message", {})
        images = message.get("images") or []
        if not images:
            logger.error("OpenRouter image response missing images field: %s", message)
            raise RuntimeError("OpenRouter image API did not include images")

        image_entry = images[0]
        url_info = image_entry.get("image_url", {})
        image_url = url_info.get("url")
        if not image_url:
            logger.error("OpenRouter image entry missing image_url: %s", image_entry)
            raise RuntimeError("OpenRouter image API returned malformed image payload")

        target_dir.mkdir(parents=True, exist_ok=True)

        if image_url.startswith("data:image"):
            header, _, payload_data = image_url.partition(",")
            if not payload_data:
                raise RuntimeError("OpenRouter returned image data URL without payload")
            ext = "png"
            if ";" in header:
                mime_part = header.split(";", 1)[0]
                if "/" in mime_part:
                    ext = mime_part.split("/", 1)[1]
            filename = f"linkedin_image_{message.get('id', 'openrouter')}.{ext}"
            image_bytes = base64.b64decode(payload_data)
            path = target_dir / filename
            path.write_bytes(image_bytes)
        else:
            try:
                download = requests.get(image_url, timeout=60)
                download.raise_for_status()
            except requests.RequestException as exc:
                logger.error("Failed downloading OpenRouter image from %s: %s", image_url, exc)
                raise RuntimeError("Failed to download OpenRouter hosted image") from exc
            filename = f"linkedin_image_{message.get('id', 'openrouter_url')}.png"
            path = target_dir / filename
            path.write_bytes(download.content)

        return ImagePayload(
            prompt=enhanced_prompt,
            provider="openrouter",
            path=path,
            alt_text=alt_text,
        )


class GoogleImagenProvider:
    """Generate imagery or video via Google Imagen/Veo models."""

    IMAGE_MODEL = "imagen-4.0-generate-001"
    VIDEO_MODEL = "veo-3.1-generate-preview"
    GOOGLE_VEO_ENDPOINT = (
        "https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning"
    )
    GOOGLE_VEO_POLL_BASE = "https://generativelanguage.googleapis.com/v1beta/"

    def __init__(self, config: ImageProviderConfig) -> None:
        api_key = (
            os.getenv("GOOGLE_API_KEY")
            or os.getenv("GOOGLE_API_TOKEN")
            or os.getenv("LLM_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY (or GOOGLE_API_TOKEN / LLM_API_KEY) must be set for Google Imagen/Veo generation"
            )
        self.config = config
        self._api_key = api_key
        self._genai_client = genai.Client(api_key=api_key)
        self._video_model = None  # Veo handled via REST

    def _apply_style(self, prompt: str) -> str:
        if self.config.style_hint:
            return f"{prompt}\nStyle hint: {self.config.style_hint}."
        return prompt

    @staticmethod
    def _extract_binary(payload: Any, *, kind: str) -> bytes:
        candidates = (
            "binary",
            "_binary",
            "bytes",
            "_bytes",
            "image_bytes",
            "_image_bytes",
            "video_bytes",
            "_video_bytes",
            "data",
        )
        for attr in candidates:
            value = getattr(payload, attr, None)
            if isinstance(value, (bytes, bytearray)):
                return bytes(value)
        inline = getattr(payload, "inline_data", None)
        if inline is not None:
            inline_data = getattr(inline, "data", None)
            if isinstance(inline_data, (bytes, bytearray)):
                return bytes(inline_data)
            if isinstance(inline, dict):
                maybe = inline.get("data")
                if isinstance(maybe, (bytes, bytearray)):
                    return bytes(maybe)
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        if isinstance(payload, dict):
            for key in ("bytes", "image_bytes", "video_bytes", "data"):
                value = payload.get(key)
                if isinstance(value, (bytes, bytearray)):
                    return bytes(value)
            inline = payload.get("inline_data")
            if isinstance(inline, dict):
                maybe = inline.get("data")
                if isinstance(maybe, (bytes, bytearray)):
                    return bytes(maybe)
        raise RuntimeError(f"Google generative response missing {kind} bytes")

    def get_image(self, *, prompt: str, target_dir: Path, alt_text: str | None = None) -> ImagePayload:
        target_dir.mkdir(parents=True, exist_ok=True)
        prompt_text = self._apply_style(prompt)

        # Use aspect_ratio if specified, otherwise use size
        generate_kwargs = {
            "prompt": prompt_text,
            "number_of_images": 1,
        }
        
        if self.config.aspect_ratio:
            generate_kwargs["aspect_ratio"] = self.config.aspect_ratio
        else:
            generate_kwargs["size"] = self.config.size
        
        response = self._genai_client.models.generate_images(
            model=self.IMAGE_MODEL,
            prompt=prompt_text,
            config={"number_of_images": 1}
        )

        images = getattr(response, "generated_images", None)
        if not images:
            raise RuntimeError("Imagen response did not include images")

        image_data = images[0]
        # New API returns image with .image.image_bytes
        img_obj = getattr(image_data, "image", image_data)
        image_bytes = getattr(img_obj, "image_bytes", None)
        if not image_bytes:
            image_bytes = self._extract_binary(image_data, kind="image")
        filename = f"imagen_{int(time.time() * 1000)}.png"
        path = target_dir / filename
        path.write_bytes(image_bytes)

        return ImagePayload(
            prompt=prompt,
            provider="imagen",
            path=path,
            alt_text=alt_text,
        )

    def get_video(self, *, prompt: str, target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        prompt_text = self._apply_style(prompt)

        response = self._generate_video_via_rest(prompt_text)

        # Veo 2.0 response format:
        # response.generateVideoResponse.generatedSamples[].video.uri
        gen_resp = response.get("generateVideoResponse") or {}
        samples = gen_resp.get("generatedSamples") or []
        if not samples:
            raise RuntimeError("Veo 2.0 response did not include generatedSamples")

        video_entry = samples[0].get("video", {})
        download_url = video_entry.get("uri") or video_entry.get("videoUri")
        if not download_url:
            raise RuntimeError("Veo 2.0 sample did not include a video URI")

        # API key is passed via x-goog-api-key header in _download_video

        import time as _time
        content = self._download_video(download_url)
        filename = f"veo2_{int(_time.time())}.mp4"
        path = target_dir / filename
        path.write_bytes(content)
        return path

    # --- Internal helpers -------------------------------------------------

    def _generate_video_via_rest(self, prompt: str) -> dict[str, Any]:
        """Submit a Veo predictLongRunning job and poll until done (up to 5 min)."""
        import time as _time
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        endpoint = os.getenv("GOOGLE_VEO_ENDPOINT", self.GOOGLE_VEO_ENDPOINT)

        # Veo REST format: model in URL only, not in body
        payload: Dict[str, Any] = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "aspectRatio": "16:9",
            },
        }

        # Submit long-running operation
        response = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        if response.status_code >= 400:
            logger.error("Google Veo error (%s): %s", response.status_code, response.text[:500])
            response.raise_for_status()

        try:
            op = response.json()
        except ValueError as exc:
            raise RuntimeError("Google Veo returned non-JSON payload") from exc

        op_name = op.get("name")
        if not op_name:
            raise RuntimeError(f"Veo did not return an operation name: {op}")

        # Poll until done (up to 5 minutes, 10s intervals)
        poll_base = os.getenv("GOOGLE_VEO_POLL_BASE", self.GOOGLE_VEO_POLL_BASE)
        poll_url = f"{poll_base}{op_name}"
        max_wait = 300
        interval = 10
        elapsed = 0
        while elapsed < max_wait:
            _time.sleep(interval)
            elapsed += interval
            poll_resp = requests.get(poll_url, headers=headers, timeout=30)
            poll_resp.raise_for_status()
            op_data = poll_resp.json()
            if op_data.get("done"):
                return op_data.get("response", {})
            logger.info("Veo operation in progress... (%ds elapsed)", elapsed)

        raise RuntimeError(f"Veo operation timed out after {max_wait}s")


    def _download_video(self, url: str) -> bytes:
        try:
            headers = {"x-goog-api-key": self._api_key}
            response = requests.get(url, headers=headers, timeout=180, allow_redirects=True)
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to download Veo video from {url}") from exc


class CuratedLibraryImageProvider:
    """Selects from a curated library of pre-approved industrial visuals."""

    def __init__(self, config: ImageProviderConfig) -> None:
        if not config.curated_library:
            raise ValueError("Curated image provider requires at least one library entry")
        self.config = config

    def get_image(self, *, prompt: str, target_dir: Path, alt_text: str | None = None) -> ImagePayload:
        choice = random.choice(list(self.config.curated_library))
        url = choice.get("url")
        if not url:
            raise RuntimeError("Curated library entry is missing a 'url' field")
        alt = alt_text or choice.get("alt_text")
        return ImagePayload(
            prompt=prompt,
            provider="curated",
            url=url,
            alt_text=alt,
        )



class GeminiImageProvider:
    """Generate images using Google Gemini's native image generation API via REST."""

    def __init__(self, config: ImageProviderConfig) -> None:
        api_key = (
            os.getenv("GOOGLE_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("LLM_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY (or GEMINI_API_KEY / LLM_API_KEY) must be set for Gemini image generation"
            )
        self.config = config
        self._api_key = api_key
        # Use gemini-2.0-flash-exp for image generation
        model_name = config.model or "gemini-2.0-flash-exp"
        # Strip any prefix like 'google/' if present
        if '/' in model_name:
            model_name = model_name.split('/')[-1]
        self._model_name = model_name
        self._base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def get_image(self, *, prompt: str, target_dir: Path, alt_text: str | None = None) -> ImagePayload:
        import time
        target_dir.mkdir(parents=True, exist_ok=True)

        # Apply style hint if configured
        full_prompt = prompt
        if self.config.style_hint:
            full_prompt = f"{prompt}. Style: {self.config.style_hint}"

        # Add instruction to generate image
        generation_prompt = f"Generate a professional image for the following: {full_prompt}"

        url = f"{self._base_url}/{self._model_name}:generateContent?key={self._api_key}"
        
        payload = {
            "contents": [{
                "parts": [{"text": generation_prompt}]
            }],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"]
            }
        }

        try:
            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()

            # Extract image from response
            image_data = None
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    inline_data = part.get("inlineData")
                    if inline_data and inline_data.get("mimeType", "").startswith("image/"):
                        image_data = inline_data.get("data")
                        break

            if not image_data:
                raise RuntimeError("Gemini response did not contain an image")

            # Decode base64
            image_bytes = base64.b64decode(image_data)

            # Save to file
            filename = f"gemini_{int(time.time())}.png"
            output_path = target_dir / filename
            output_path.write_bytes(image_bytes)

            logger.info(f"Generated Gemini image: {output_path} ({len(image_bytes)} bytes)")

            return ImagePayload(
                prompt=prompt,
                provider="gemini",
                path=output_path,
                url=None,
                alt_text=alt_text,
            )

        except requests.exceptions.HTTPError as e:
            error_msg = str(e)
            try:
                error_data = e.response.json()
                error_msg = error_data.get("error", {}).get("message", str(e))
            except:
                error_msg = re.sub(r'[?&]key=[^&\s]+', '?key=REDACTED', str(e))
            logger.error(f"Gemini API error: {error_msg}")
            raise RuntimeError(f"Gemini image generation failed: {error_msg}") from e
        except Exception as exc:
            safe_msg = re.sub(r'[?&]key=[^&\s]+', '?key=REDACTED', str(exc))
            logger.error(f"Gemini image generation failed: {safe_msg}")
            raise RuntimeError(f"Gemini image generation failed: {safe_msg}") from exc


def create_image_provider(config: ImageProviderConfig) -> ImageProvider:
    normalized = (config.provider or "").strip().lower()
    
    # Create base provider
    base_provider = None
    if normalized in {"openrouter", "or"}:
        base_provider = OpenRouterImageProvider(config)
    elif normalized in {"openai", "dalle", "dall-e"}:
        base_provider = OpenAIImageProvider(config)
    elif normalized in {"curated", "library"}:
        base_provider = CuratedLibraryImageProvider(config)
    elif normalized in {"gemini", "gemini-flash", "gemini-2.0"}:
        base_provider = GeminiImageProvider(config)
    elif normalized in {"google", "imagen", "google-imagen", "google_imagen"}:
        base_provider = GoogleImagenProvider(config)
    else:
        raise ValueError(f"Unsupported image provider: {config.provider}")
    
    # Wrap with AnimatedGIFProvider if GIF generation is enabled
    if config.use_animated_gif:
        logger.info(f"Wrapping {config.provider} with AnimatedGIFProvider (frames={config.gif_num_frames}, duration={config.gif_frame_duration}ms)")
        return AnimatedGIFProvider(
            base_provider=base_provider,
            num_frames=config.gif_num_frames,
            frame_duration=config.gif_frame_duration
        )
    
    return base_provider


__all__ = [
    "ImageProviderConfig",
    "ImagePayload",
    "ImageProvider",
    "OpenAIImageProvider",
    "CuratedLibraryImageProvider",
    "OpenRouterImageProvider",
    "GoogleImagenProvider",
    "GeminiImageProvider",
    "create_image_provider",
]


class ReplicateVideoProvider:
    """Generate videos using Replicate's Veo 2 model."""
    
    MODEL = "google/veo-2"
    API_BASE = "https://api.replicate.com/v1"
    
    def __init__(self, config: ImageProviderConfig) -> None:
        api_key = os.getenv("REPLICATE_API_TOKEN") or os.getenv("REPLICATE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "REPLICATE_API_TOKEN must be set for Replicate video generation"
            )
        self.config = config
        self._api_key = api_key
        
    def _apply_style(self, prompt: str) -> str:
        if self.config.style_hint:
            return f"{prompt}. Style: {self.config.style_hint}"
        return prompt
    
    def get_video(self, *, prompt: str, target_dir: Path) -> Path:
        """Generate a video using Replicate Veo 2."""
        import time
        target_dir.mkdir(parents=True, exist_ok=True)
        prompt_text = self._apply_style(prompt)
        
        logger.info("Generating video with Replicate Veo 2: %s", prompt[:100])
        
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        
        # Create prediction
        payload = {
            "version": "af8ebddc406d877a89d631dbbcba24b31692e0f9819639299b1d5def12dd7c95",
            "input": {
                "prompt": prompt_text,
                "duration": 8,  # 8 seconds for LinkedIn
                "aspect_ratio": "16:9",  # Landscape for LinkedIn
            }
        }
        
        url = f"{self.API_BASE}/predictions"
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            
            if response.status_code >= 500:
                raise RuntimeError(f"Replicate service unavailable ({response.status_code})")
            if response.status_code >= 400:
                logger.error("Replicate error (%s): %s", response.status_code, response.text[:500])
                response.raise_for_status()
            
            prediction = response.json()
            prediction_id = prediction.get("id")
            
            if not prediction_id:
                raise RuntimeError("Replicate response missing prediction ID")
            
            logger.info("Replicate prediction created: %s", prediction_id)
            
            # Poll for completion (videos take 2-3 minutes)
            max_wait = 300  # 5 minutes max
            start_time = time.time()
            poll_interval = 5  # Start with 5 second intervals
            
            while time.time() - start_time < max_wait:
                time.sleep(poll_interval)
                
                status_response = requests.get(
                    f"{url}/{prediction_id}",
                    headers=headers,
                    timeout=30
                )
                status_response.raise_for_status()
                prediction = status_response.json()
                
                status = prediction.get("status")
                
                if status == "succeeded":
                    output = prediction.get("output")
                    if not output:
                        raise RuntimeError("Replicate succeeded but returned no output")
                    
                    # Download video
                    video_url = output if isinstance(output, str) else output[0]
                    logger.info("Downloading video from: %s", video_url)
                    
                    video_response = requests.get(video_url, timeout=180)
                    video_response.raise_for_status()
                    
                    filename = f"replicate_veo2_{prediction_id}.mp4"
                    path = target_dir / filename
                    path.write_bytes(video_response.content)
                    
                    logger.info("Replicate Veo 2 video generated successfully: %s", path)
                    return path
                    
                elif status == "failed":
                    error = prediction.get("error", "Unknown error")
                    raise RuntimeError(f"Replicate prediction failed: {error}")
                
                elif status in ["starting", "processing"]:
                    # Increase poll interval gradually
                    poll_interval = min(poll_interval + 2, 15)
                    continue
                    
                else:
                    logger.warning("Unknown Replicate status: %s", status)
                    continue
            
            raise RuntimeError("Replicate video generation timed out after 5 minutes")
            
        except Exception as exc:
            logger.error("Replicate Veo 2 video generation failed: %s", exc)
            raise RuntimeError(f"Failed to generate video with Replicate: {exc}") from exc


class AnimatedGIFProvider:
    """Generate animated GIFs from multiple AI-generated images."""

    def __init__(self, base_provider, num_frames: int = 6, frame_duration: int = 1000):
        """
        Initialize with a base image provider.

        Args:
            base_provider: The image provider to use for generating frames
            num_frames: Number of frames to generate (default: 6)
            frame_duration: Duration of each frame in milliseconds (default: 1000ms = 1s)
        """
        self.base_provider = base_provider
        self.num_frames = num_frames
        self.frame_duration = frame_duration

    def get_image(self, *, prompt: str, target_dir: Path, alt_text: str) -> Dict[str, Any]:
        """Generate an animated GIF by creating multiple image variations."""
        from PIL import Image
        import io
        import time

        target_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Generating animated GIF with {self.num_frames} frames")

        # Frame variations - different angles and perspectives
        frame_variations = [
            "extreme close-up macro shot, shallow depth of field, no text, no logos, no branding",
            "top-down view from above, dramatic shadows, no text, no logos, no branding",
            "45-degree side angle, industrial context, no text, no logos, no branding",
            "rotating perspective, dynamic angle, no text, no logos, no branding",
            "detail shot showing texture and finish, no text, no logos, no branding",
            "wide contextual shot, professional setting, no text, no logos, no branding",
            "low angle view, emphasizing scale, no text, no logos, no branding",
            "high contrast lighting, metallic highlights, no text, no logos, no branding",
        ]

        # Generate individual frames
        frames = []
        frame_paths = []

        for i in range(min(self.num_frames, len(frame_variations))):
            variation = frame_variations[i]
            frame_prompt = f"{prompt}. {variation}"

            logger.info(f"Generating frame {i+1}/{self.num_frames}: {variation[:50]}...")

            try:
                # Generate frame using base provider
                frame_result = self.base_provider.get_image(
                    prompt=frame_prompt,
                    target_dir=target_dir,
                    alt_text=f"{alt_text} - frame {i+1}"
                )

                # Get frame path
                frame_path = frame_result.path
                frame_paths.append(frame_path)

                # CRITICAL: TNT Motion logo overlay - DO NOT REMOVE unless expressly commanded
                # This adds the TNT Motion logo to each GIF frame BEFORE loading into memory
                logo_path = Path(__file__).parent.parent.parent / "assets" / "tnt_motion_logo.png"
                if logo_path.exists():
                    add_logo_to_image(
                        frame_path,
                        logo_path,
                        position="bottom-right",
                        logo_width_percent=0.12,
                        margin_percent=0.02
                    )

                # Load the image WITH logo into frames
                with Image.open(frame_path) as img:
                    # Convert to RGB if needed (GIFs don't support RGBA well)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    frames.append(img.copy())

                logger.info(f"  Frame {i+1} generated with logo: {frame_path}")

            except Exception as exc:
                logger.warning(f"Failed to generate frame {i+1}: {exc}")
                # Continue with fewer frames if some fail
                if not frames:
                    raise  # But fail if we have no frames at all
                continue

        if not frames:
            raise RuntimeError("Failed to generate any frames for animated GIF")

        logger.info(f"Successfully generated {len(frames)} frames")

        # Create animated GIF
        gif_filename = f"animated_{int(time.time())}.gif"
        gif_path = target_dir / gif_filename

        # Ensure all frames are same size
        width, height = frames[0].size
        resized_frames = []
        for frame in frames:
            if frame.size != (width, height):
                frame = frame.resize((width, height), Image.Resampling.LANCZOS)
            resized_frames.append(frame)

        # Save as animated GIF
        resized_frames[0].save(
            gif_path,
            save_all=True,
            append_images=resized_frames[1:],
            duration=self.frame_duration,
            loop=0,  # Infinite loop
            optimize=True,
        )

        file_size = gif_path.stat().st_size
        total_duration = len(resized_frames) * self.frame_duration / 1000

        logger.info(f"Animated GIF created: {gif_path}")
        logger.info(f"  Frames: {len(resized_frames)}, Duration: {total_duration}s, Size: {file_size/1024:.1f}KB")

        # Return result as ImagePayload
        return ImagePayload(
            prompt=prompt,
            provider=f"animated_gif_{self.base_provider.__class__.__name__}",
            path=gif_path,
            url=None,
            alt_text=alt_text,
        )
