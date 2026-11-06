"""Validate the committed PNG and SVG assets for the Mijn PWN integration."""

import sys
from pathlib import Path

from defusedxml import ElementTree
from PIL import Image, UnidentifiedImageError

IMAGE_ROOT = Path("custom_components/mijn_pwn/icons")
SVG_TAG = "{http://www.w3.org/2000/svg}svg"


def _validate_png(path: Path) -> None:
    """Check that a PNG file is decodable and has nonzero dimensions."""
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError("File does not contain PNG data")
            if min(image.size) < 1:
                raise ValueError("PNG dimensions must be positive")
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise ValueError(f"Invalid PNG at {path}: {error}") from error


def _validate_svg(path: Path) -> None:
    """Parse an SVG without resolving entities or external resources."""
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError, ValueError) as error:
        raise ValueError(f"Invalid SVG at {path}: {error}") from error

    if root.tag != SVG_TAG:
        raise ValueError(f"Invalid SVG root element at {path}")
    if not (root.get("viewBox") or (root.get("width") and root.get("height"))):
        raise ValueError(f"SVG is missing dimensions at {path}")


def main() -> int:
    """Validate every committed image and return a process exit code."""
    if not IMAGE_ROOT.is_dir():
        print(f"Image directory not found: {IMAGE_ROOT}", file=sys.stderr)
        return 1

    images = sorted(
        path for path in IMAGE_ROOT.rglob("*")
        if path.suffix.lower() in {".png", ".svg"}
    )
    if not images:
        print(f"No PNG or SVG images found in {IMAGE_ROOT}", file=sys.stderr)
        return 1

    failures: list[str] = []
    for path in images:
        try:
            if path.suffix.lower() == ".png":
                _validate_png(path)
            else:
                _validate_svg(path)
        except ValueError as error:
            failures.append(str(error))

    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"Validated {len(images)} images; {len(failures)} invalid")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
