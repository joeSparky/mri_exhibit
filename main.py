from pathlib import Path

from engine.renderer import Renderer

SCREEN_WIDTH = 1200
SCREEN_HEIGHT = 800


def main() -> None:
    base_dir = Path(__file__).resolve().parent

    renderer = Renderer(
        base_dir=base_dir,
        screen_width=SCREEN_WIDTH,
        screen_height=SCREEN_HEIGHT,
    )
    renderer.run(start_screen="main")


if __name__ == "__main__":
    main()