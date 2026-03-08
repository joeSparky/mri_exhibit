from pathlib import Path
import argparse

from PIL import Image, ImageDraw, ImageFont

SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480


def safe_filename(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in text.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:80] if cleaned else "image"


def load_font(size: int):
    candidates = [
        "arial.ttf",
        "DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for font_path in candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int):
    for size in range(120, 11, -2):
        font = load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= max_width and height <= max_height:
            return font, width, height

    font = load_font(12)
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return font, width, height


def make_image(text: str, output_path: Path):
    img = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), (28, 36, 52))
    draw = ImageDraw.Draw(img)

    margin_x = 40
    margin_y = 40
    max_width = SCREEN_WIDTH - 2 * margin_x
    max_height = SCREEN_HEIGHT - 2 * margin_y

    font, text_width, text_height = fit_font(draw, text, max_width, max_height)

    x = (SCREEN_WIDTH - text_width) / 2
    y = (SCREEN_HEIGHT - text_height) / 2

    subtle_text_color = (150, 165, 185)
    draw.text((x, y), text, fill=subtle_text_color, font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)
    print(f"Created: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate a PNG with centered text.")
    parser.add_argument("text", help='Text to place on the image, e.g. "CAT MRI"')
    parser.add_argument("-o", "--output", help="Optional output filename.")
    args = parser.parse_args()

    text = args.text.strip()
    if not text:
        raise SystemExit("Text cannot be empty.")

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path("assets") / f"{safe_filename(text)}.png"

    make_image(text, output_path)


if __name__ == "__main__":
    main()