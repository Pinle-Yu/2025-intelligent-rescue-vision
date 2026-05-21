import argparse
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "assets"
def create_icon() -> None:
    size = 256
    image = Image.new("RGB", (size, size), "#f7f8fa")
    draw = ImageDraw.Draw(image)

    # Camera body and lens.
    draw.rounded_rectangle((34, 58, 222, 198), radius=20, fill="#20252b")
    draw.rounded_rectangle((70, 38, 132, 72), radius=8, fill="#20252b")
    draw.ellipse((85, 75, 171, 161), fill="#f7f8fa")
    draw.ellipse((99, 89, 157, 147), fill="#3b82a0")
    draw.ellipse((116, 106, 140, 130), fill="#d8edf3")

    # Six detection classes used by the model.
    colors = ("#d94040", "#e7bd32", "#242424", "#3f6fd9", "#d94040", "#3f6fd9")
    for index, color in enumerate(colors):
        x = 50 + index * 27
        draw.rounded_rectangle((x, 170, x + 18, 188), radius=3, fill=color)

    image.save(ASSETS_DIR / "app.png", optimize=True)


def prepare_device_photo(source_photo: Path) -> None:
    with Image.open(source_photo) as photo:
        photo = photo.convert("RGB")
        # Keep the MaixCAM display and robot hardware, while removing the phone watermark.
        cropped = photo.crop((260, 160, 2760, 3400))
        cropped.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        cropped.save(ASSETS_DIR / "device-demo.jpg", quality=86, optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare README images for this project.")
    parser.add_argument("source_photo", type=Path, help="Path to the original device photo")
    args = parser.parse_args()

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    create_icon()
    prepare_device_photo(args.source_photo)
