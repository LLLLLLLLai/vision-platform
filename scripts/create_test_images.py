from pathlib import Path

from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = PROJECT_ROOT / "test_images"


def create_image(path: Path, offset: int, color: tuple[int, int, int]) -> None:
    image = Image.new("RGB", (320, 240), (235, 239, 245))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (70 + offset, 55, 250 + offset, 185),
        radius=18,
        fill=color,
        outline=(45, 55, 70),
        width=6,
    )
    draw.ellipse(
        (135 + offset, 95, 185 + offset, 145),
        fill=(250, 250, 250),
        outline=(45, 55, 70),
        width=4,
    )
    image.save(path)


def create_different_image(path: Path) -> None:
    image = Image.new("RGB", (320, 240), (28, 35, 48))
    draw = ImageDraw.Draw(image)
    for row in range(4):
        for column in range(6):
            x = 28 + column * 48
            y = 28 + row * 48
            draw.rectangle(
                (x, y, x + 24, y + 18),
                fill=(45, 185, 110),
                outline=(210, 240, 225),
                width=2,
            )
    draw.line((18, 215, 300, 22), fill=(245, 118, 66), width=8)
    image.save(path)


if __name__ == "__main__":
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    create_image(TEST_DIR / "reference.png", 0, (235, 177, 52))
    create_image(TEST_DIR / "similar.png", 4, (230, 172, 48))
    create_different_image(TEST_DIR / "different.png")
    print(f"Test images created in: {TEST_DIR}")
