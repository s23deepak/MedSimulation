"""Fetch publisher figures referenced by a case and cache validated raster files."""

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
from PIL import Image, UnidentifiedImageError

from . import imaging

# Only publisher-controlled origins. Each redirect is checked before the request.
PUBLISHERS = frozenset(
    {
        "cdn.ncbi.nlm.nih.gov",
        "pmc.ncbi.nlm.nih.gov",
        "www.ncbi.nlm.nih.gov",
        "journals.plos.org",
        "www.frontiersin.org",
        "public-pages-files-2025.frontiersin.org",
        "onlinelibrary.wiley.com",
        "analyticalsciencejournals.onlinelibrary.wiley.com",
        "media.springernature.com",
        "link.springer.com",
        "bmcmededuc.biomedcentral.com",
        "ars.els-cdn.com",
        "www.sciencedirect.com",
        "www.thelancet.com",
        "www.panafrican-med-journal.com",
        "www.systems.panafrican-med-journal.com",
    }
)
MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
MAX_BYTES = 24 * 1024 * 1024
MAX_PIXELS = 32_000_000


class FigureUnavailable(ValueError):
    pass


def _is_publisher_url(value: str) -> bool:
    try:
        url = httpx.URL(value)
    except (ValueError, httpx.InvalidURL):
        return False
    return (
        url.scheme == "https"
        and url.host in PUBLISHERS
        and url.port in (None, 443)
        and not url.userinfo
    )


def publisher_figure_url(study: dict) -> str:
    value = study.get("file_path") or ""
    if value.startswith("http://"):
        value = "https://" + value[len("http://") :]
    if "screenshot" in (study.get("notes") or "").lower() or not _is_publisher_url(value):
        return ""
    return value


def cached_figure(url: str) -> tuple[Path, str]:
    if not _is_publisher_url(url):
        raise FigureUnavailable("Unsupported figure source")
    directory = imaging.IMAGING_DIR / "figures"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    for fmt, media_type in MEDIA_TYPES.items():
        target = directory / f"{key}.{fmt.lower()}"
        if target.is_file():
            return target, media_type

    with NamedTemporaryFile(dir=directory, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with httpx.Client(timeout=12, trust_env=False) as client:
            for _ in range(5):
                with client.stream("GET", url) as response:
                    if response.is_redirect and response.next_request:
                        url = str(response.next_request.url)
                        if not _is_publisher_url(url):
                            raise FigureUnavailable("Unsupported figure redirect")
                        continue
                    response.raise_for_status()
                    size = 0
                    with temporary.open("wb") as output:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise FigureUnavailable("Figure exceeds size limit")
                            output.write(chunk)
                    break
            else:
                raise FigureUnavailable("Too many figure redirects")
        with Image.open(temporary, formats=list(MEDIA_TYPES)) as image:
            fmt = image.format
            if image.width * image.height > MAX_PIXELS:
                raise FigureUnavailable("Figure exceeds pixel limit")
            image.verify()
        with Image.open(temporary) as image:
            image.load()
        target = directory / f"{key}.{fmt.lower()}"
        os.replace(temporary, target)
        return target, MEDIA_TYPES[fmt]
    except (
        httpx.HTTPError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
    ) as exc:
        raise FigureUnavailable("Figure could not be loaded") from exc
    finally:
        temporary.unlink(missing_ok=True)
