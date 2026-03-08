from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import time

import pygame
import yaml


@dataclass
class ButtonSpec:
    text: str
    next_screen: str | None
    rect: pygame.Rect | None = None


class Renderer:
    def __init__(self, base_dir: Path, screen_width: int = 800, screen_height: int = 480):
        self.base_dir = base_dir
        self.screens_dir = self.base_dir / "screens"
        self.assets_dir = self.base_dir / "assets"

        self.screen_width = screen_width
        self.screen_height = screen_height

        pygame.init()
        pygame.display.set_caption("MRI Exhibit")
        self.display = pygame.display.set_mode((self.screen_width, self.screen_height))
        self.clock = pygame.time.Clock()

        self.font_title = pygame.font.SysFont(None, 52)
        self.font_body = pygame.font.SysFont(None, 30)
        self.font_button = pygame.font.SysFont(None, 32)
        self.font_footer = pygame.font.SysFont(None, 26)
        self.font_small = pygame.font.SysFont(None, 22)

        self.running = True
        self.current_screen_id = ""
        self.current_screen_data: dict[str, Any] = {}
        self.current_buttons: list[ButtonSpec] = []
        self.code_buffer = ""
        self.screen_start_ms = 0

        self.hot_reload_enabled = True
        self.last_reload_check = 0.0
        self.reload_check_interval_s = 0.5
        self.watched_files_mtime: dict[Path, float] = {}

    def load_yaml(self, screen_id: str) -> dict[str, Any]:
        path = self.screens_dir / f"{screen_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Could not find screen file: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict):
            raise ValueError(f"Top level YAML must be a mapping: {path}")

        return data

    def load_screen(self, screen_id: str) -> None:
        data = self.load_yaml(screen_id)

        self.current_screen_id = screen_id
        self.current_screen_data = data
        self.current_buttons = []
        self.code_buffer = ""
        self.screen_start_ms = pygame.time.get_ticks()

        print(f"\nLoaded screen: {screen_id}")
        print(data)

        self.refresh_watched_files()

    def get_watched_paths(self) -> list[Path]:
        paths: list[Path] = []

        screen_path = self.screens_dir / f"{self.current_screen_id}.yaml"
        if screen_path.exists():
            paths.append(screen_path)

        bg_image = self.current_screen_data.get("bg_image")
        if bg_image:
            image_path = self.assets_dir / str(bg_image)
            if image_path.exists():
                paths.append(image_path)

        split_layout = self.current_screen_data.get("split_layout")
        if split_layout == "vertical":
            scan_panel = self.current_screen_data.get("scan_panel", {})
            if isinstance(scan_panel, dict):
                scan_image = scan_panel.get("image")
                if scan_image:
                    image_path = self.assets_dir / str(scan_image)
                    if image_path.exists():
                        paths.append(image_path)

        buttons_cfg = self.current_screen_data.get("buttons")
        if isinstance(buttons_cfg, list):
            for button_cfg in buttons_cfg:
                if isinstance(button_cfg, dict):
                    image_name = button_cfg.get("image")
                    if image_name:
                        image_path = self.assets_dir / str(image_name)
                        if image_path.exists():
                            paths.append(image_path)

        return paths

    def refresh_watched_files(self) -> None:
        self.watched_files_mtime = {}
        for path in self.get_watched_paths():
            try:
                self.watched_files_mtime[path] = path.stat().st_mtime
            except OSError:
                pass

    def check_hot_reload(self) -> None:
        if not self.hot_reload_enabled:
            return

        now = time.monotonic()
        if now - self.last_reload_check < self.reload_check_interval_s:
            return
        self.last_reload_check = now

        watched_paths = self.get_watched_paths()
        if not watched_paths:
            return

        changed = False

        for path in watched_paths:
            try:
                current_mtime = path.stat().st_mtime
            except OSError:
                continue

            old_mtime = self.watched_files_mtime.get(path)
            if old_mtime is None:
                self.watched_files_mtime[path] = current_mtime
            elif current_mtime != old_mtime:
                changed = True
                break

        if changed:
            current_code_buffer = self.code_buffer
            current_start_ms = self.screen_start_ms
            screen_id = self.current_screen_id

            print(f"Hot reload detected for screen '{screen_id}'")

            self.load_screen(screen_id)
            self.code_buffer = current_code_buffer
            self.screen_start_ms = current_start_ms
            self.refresh_watched_files()

    def get_color(self, key: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
        raw = self.current_screen_data.get(key)
        if isinstance(raw, list) and len(raw) == 3:
            try:
                return int(raw[0]), int(raw[1]), int(raw[2])
            except Exception:
                return default
        return default

    def get_text(self, key: str, default: str = "") -> str:
        value = self.current_screen_data.get(key, default)
        if value is None:
            return ""
        return str(value)

    def wrap_text(self, text: str, font: pygame.font.Font, max_width: int) -> list[str]:
        if not text:
            return []

        lines: list[str] = []
        paragraphs = text.splitlines()

        for paragraph in paragraphs:
            if not paragraph.strip():
                lines.append("")
                continue

            words = paragraph.split()
            current = words[0]

            for word in words[1:]:
                candidate = current + " " + word
                if font.size(candidate)[0] <= max_width:
                    current = candidate
                else:
                    lines.append(current)
                    current = word

            lines.append(current)

        return lines

    def draw_centered_lines(
        self,
        text: str,
        font: pygame.font.Font,
        color: tuple[int, int, int],
        top_y: int,
        max_width: int,
        line_gap: int = 8,
    ) -> int:
        center_x = self.screen_width // 2
        lines = self.wrap_text(text, font, max_width)
        y = top_y

        for line in lines:
            surf = font.render(line, True, color)
            rect = surf.get_rect(midtop=(center_x, y))
            self.display.blit(surf, rect)
            y = rect.bottom + line_gap

        return y

    def try_draw_background_image(self) -> None:
        bg_image = self.current_screen_data.get("bg_image")
        if not bg_image:
            return

        image_path = self.assets_dir / str(bg_image)
        if not image_path.exists():
            print(f"Background image not found: {image_path}")
            return

        try:
            image = pygame.image.load(str(image_path)).convert()
            image = pygame.transform.smoothscale(image, (self.screen_width, self.screen_height))
            self.display.blit(image, (0, 0))
        except Exception as e:
            print(f"Failed to load background image {image_path}: {e}")

    def draw_image_into_rect(self, image_name: str | None, rect: pygame.Rect) -> None:
        if not image_name:
            pygame.draw.rect(self.display, (150, 160, 175), rect, border_radius=10)
            return

        image_path = self.assets_dir / str(image_name)
        if not image_path.exists():
            pygame.draw.rect(self.display, (150, 160, 175), rect, border_radius=10)
            return

        try:
            img = pygame.image.load(str(image_path)).convert_alpha()
            img = pygame.transform.smoothscale(img, (rect.width, rect.height))
            self.display.blit(img, rect)
        except Exception as e:
            print(f"Failed to load image {image_path}: {e}")
            pygame.draw.rect(self.display, (150, 160, 175), rect, border_radius=10)

    def draw_animal_button(self, button_cfg: dict[str, Any], x: int, y: int, width: int, height: int) -> None:
        text = str(button_cfg.get("text", "")).strip()
        next_screen = button_cfg.get("next")
        image_name = button_cfg.get("image")
        show_label = bool(button_cfg.get("show_label", True))

        if not text:
            return

        rect = pygame.Rect(x, y, width, height)

        pygame.draw.rect(self.display, (225, 230, 235), rect, border_radius=16)
        pygame.draw.rect(self.display, (35, 35, 35), rect, width=2, border_radius=16)

        if show_label:
            image_rect = pygame.Rect(x + 10, y + 10, width - 20, height - 50)
        else:
            image_rect = pygame.Rect(x + 10, y + 10, width - 20, height - 20)

        self.draw_image_into_rect(image_name, image_rect)

        if show_label:
            surf = self.font_button.render(text, True, (20, 20, 20))
            surf_rect = surf.get_rect(midbottom=(rect.centerx, rect.bottom - 10))
            self.display.blit(surf, surf_rect)

        self.current_buttons.append(
            ButtonSpec(
                text=text,
                next_screen=str(next_screen) if next_screen else None,
                rect=rect,
            )
        )

    def draw_buttons(self, buttons_cfg: list[dict[str, Any]]) -> None:
        self.current_buttons = []

        if not buttons_cfg:
            return

        count = len(buttons_cfg)

        button_width = 210
        button_height = 180
        gap = 20

        total_width = count * button_width + (count - 1) * gap
        start_x = (self.screen_width - total_width) // 2
        y = self.screen_height - button_height - 35

        for i, button_cfg in enumerate(buttons_cfg):
            text = str(button_cfg.get("text", "")).strip()

            if not text:
                continue

            x = start_x + i * (button_width + gap)
            self.draw_animal_button(button_cfg, x, y, button_width, button_height)

    def draw_split_main_screen(self) -> bool:
        split_layout = self.current_screen_data.get("split_layout")
        if split_layout != "vertical":
            return False

        left_panel_width = int(self.current_screen_data.get("left_panel_width", 260))
        gap = 20
        margin = 20

        left_rect = pygame.Rect(
            margin,
            margin,
            left_panel_width,
            self.screen_height - 2 * margin,
        )

        right_rect = pygame.Rect(
            left_rect.right + gap,
            margin,
            self.screen_width - (left_rect.right + gap) - margin,
            self.screen_height - 2 * margin,
        )


        panel_color = self.get_color("bg_color", (20, 40, 70))

        pygame.draw.rect(self.display, panel_color, left_rect, border_radius=18)
        pygame.draw.rect(self.display, panel_color, right_rect, border_radius=18)

        scan_panel = self.current_screen_data.get("scan_panel", {})
        if not isinstance(scan_panel, dict):
            scan_panel = {}

        scan_title = str(scan_panel.get("title", "Scan Your Card"))
        scan_body = str(scan_panel.get("body", "Scan your animal card\nor touch an animal."))
        scan_image = scan_panel.get("image")

        text_color = self.get_color("text_color", (255, 255, 255))

        y = left_rect.top + 20

        title_surf = self.font_title.render(scan_title, True, text_color)
        title_rect = title_surf.get_rect(midtop=(left_rect.centerx, y))
        self.display.blit(title_surf, title_rect)
        y = title_rect.bottom + 15

        image_height = int(left_rect.height * 0.45)

        image_rect = pygame.Rect(
            left_rect.left + 20,
            y,
            left_rect.width - 40,
            image_height,
        )

        # soft shadow
        shadow_rect = image_rect.move(4, 4)
        pygame.draw.rect(self.display, (0, 0, 0, 60), shadow_rect, border_radius=12)

        # white card background
        pygame.draw.rect(self.display, (245, 245, 245), image_rect, border_radius=12)

        # border
        pygame.draw.rect(self.display, (200, 200, 200), image_rect, width=2, border_radius=12)

        # inset image
        padding = 12
        inner_rect = pygame.Rect(
            image_rect.left + padding,
            image_rect.top + padding,
            image_rect.width - padding * 2,
            image_rect.height - padding * 2,
        )

        self.draw_image_into_rect(scan_image, inner_rect)

        y = image_rect.bottom + 15

        body_lines = self.wrap_text(scan_body, self.font_body, left_rect.width - 30)
        for line in body_lines:
            surf = self.font_body.render(line, True, text_color)
            rect = surf.get_rect(centerx=left_rect.centerx, top=y)
            self.display.blit(surf, rect)
            y = rect.bottom + 6

        buttons_cfg = self.current_screen_data.get("buttons", [])
        if not isinstance(buttons_cfg, list):
            buttons_cfg = []

        self.current_buttons = []

        count = len(buttons_cfg)
        if count == 0:
            return True

        # Layout policy:
        # 1-4 animals  -> 2 columns
        # 5-6 animals  -> 3 columns
        cols = 2 if count <= 4 else 3
        rows = (count + cols - 1) // cols

        inner_margin_x = 18
        inner_margin_top = 20
        inner_margin_bottom = 18
        cell_gap_x = 14
        cell_gap_y = 14

        usable_width = right_rect.width - 2 * inner_margin_x
        usable_height = right_rect.height - inner_margin_top - inner_margin_bottom

        button_w = (usable_width - cell_gap_x * (cols - 1)) // cols
        button_h = (usable_height - cell_gap_y * (rows - 1)) // rows

        # Keep the buttons visually square-ish and child-friendly
        button_size = min(button_w, button_h)
        button_w = button_size
        button_h = button_size

        total_grid_width = cols * button_w + (cols - 1) * cell_gap_x
        total_grid_height = rows * button_h + (rows - 1) * cell_gap_y

        grid_start_x = right_rect.left + (right_rect.width - total_grid_width) // 2
        grid_start_y = right_rect.top + (right_rect.height - total_grid_height) // 2

        for i, button_cfg in enumerate(buttons_cfg[:6]):
            row = i // cols
            col = i % cols

            # For the last row, center it if it is not full
            items_in_this_row = min(cols, count - row * cols)
            if items_in_this_row < cols:
                row_width = items_in_this_row * button_w + (items_in_this_row - 1) * cell_gap_x
                row_start_x = right_rect.left + (right_rect.width - row_width) // 2
                x = row_start_x + (i % cols) * (button_w + cell_gap_x)
            else:
                x = grid_start_x + col * (button_w + cell_gap_x)

            y = grid_start_y + row * (button_h + cell_gap_y)

            self.draw_animal_button(button_cfg, x, y, button_w, button_h)

        return True

    def draw_single_button(self, button_cfg: dict[str, Any]) -> None:
        text = str(button_cfg.get("text", "")).strip()
        next_screen = button_cfg.get("next")
        if not text:
            self.current_buttons = []
            return

        width = 220
        height = 58
        x = (self.screen_width - width) // 2
        y = self.screen_height - 110

        rect = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.display, (235, 235, 235), rect, border_radius=10)
        pygame.draw.rect(self.display, (35, 35, 35), rect, width=2, border_radius=10)

        surf = self.font_button.render(text, True, (20, 20, 20))
        surf_rect = surf.get_rect(center=rect.center)
        self.display.blit(surf, surf_rect)

        self.current_buttons = [
            ButtonSpec(
                text=text,
                next_screen=str(next_screen) if next_screen else None,
                rect=rect,
            )
        ]

    def draw_corner_button(self) -> None:
        corner_cfg = self.current_screen_data.get("corner_button")
        if not isinstance(corner_cfg, dict):
            return

        text = str(corner_cfg.get("text", "")).strip()
        next_screen = corner_cfg.get("next")
        corner = str(corner_cfg.get("corner", "top_right"))

        if not text:
            return

        width = 56
        height = 44
        margin = 12

        if corner == "top_left":
            x = margin
            y = margin
        elif corner == "bottom_left":
            x = margin
            y = self.screen_height - height - margin
        elif corner == "bottom_right":
            x = self.screen_width - width - margin
            y = self.screen_height - height - margin
        else:  # top_right default
            x = self.screen_width - width - margin
            y = margin

        rect = pygame.Rect(x, y, width, height)

        # Semi-transparent button surface
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(overlay, (255, 255, 255, 70), overlay.get_rect(), border_radius=10)
        pygame.draw.rect(overlay, (255, 255, 255, 140), overlay.get_rect(), width=1, border_radius=10)
        self.display.blit(overlay, rect.topleft)

        surf = self.font_button.render(text, True, (255, 255, 255))
        surf_rect = surf.get_rect(center=rect.center)
        self.display.blit(surf, surf_rect)

        self.current_buttons.append(
            ButtonSpec(
                text=text,
                next_screen=str(next_screen) if next_screen else None,
                rect=rect,
            )
        )

    def draw_screen(self) -> None:
        bg_color = self.get_color("bg_color", (20, 40, 70))
        text_color = self.get_color("text_color", (255, 255, 255))

        self.display.fill(bg_color)
        self.try_draw_background_image()

        if bool(self.current_screen_data.get("fullscreen_image", False)):
            self.current_buttons = []

            image_name = self.current_screen_data.get("image")
            if image_name:
                image_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)
                self.draw_image_into_rect(str(image_name), image_rect)

            self.draw_corner_button()

            pygame.display.flip()
            return

        if self.draw_split_main_screen():
            show_code_entry = bool(self.current_screen_data.get("show_code_entry", True))
            if show_code_entry:
                code_text = f"Code: {self.code_buffer}_"
                surf = self.font_footer.render(code_text, True, text_color)
                rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 16))
                self.display.blit(surf, rect)

            # small = self.font_small.render(f"screen: {self.current_screen_id}", True, text_color)
            # small_rect = small.get_rect(left=10, bottom=self.screen_height - 10)
            # self.display.blit(small, small_rect)

            pygame.display.flip()
            return

        title = self.get_text("title", self.current_screen_id)
        body = self.get_text("body", "")
        prompt = self.get_text("prompt", "")
        footer = self.get_text("footer", "")
        show_code_entry = bool(self.current_screen_data.get("show_code_entry", True))

        y = 50
        y = self.draw_centered_lines(
            title,
            self.font_title,
            text_color,
            y,
            max_width=self.screen_width - 80,
            line_gap=6,
        )
        y += 28

        if body:
            y = self.draw_centered_lines(
                body,
                self.font_body,
                text_color,
                y,
                max_width=self.screen_width - 100,
                line_gap=6,
            )
            y += 18

        if prompt:
            y = self.draw_centered_lines(
                prompt,
                self.font_body,
                text_color,
                y,
                max_width=self.screen_width - 100,
                line_gap=6,
            )
            y += 20

            image_name = self.current_screen_data.get("image")
            if image_name:
                image_rect = pygame.Rect(
                    (self.screen_width - 280) // 2,
                    y,
                    280,
                    180,
                )
                self.draw_image_into_rect(str(image_name), image_rect)
                y = image_rect.bottom + 20

        barcode_map = self.current_screen_data.get("barcode_map", {})
        if isinstance(barcode_map, dict) and barcode_map:
            hint = "Try: " + ", ".join(barcode_map.keys())
            y = self.draw_centered_lines(
                hint,
                self.font_body,
                text_color,
                y,
                max_width=self.screen_width - 100,
                line_gap=6,
            )

        buttons_cfg = self.current_screen_data.get("buttons")
        if isinstance(buttons_cfg, list) and buttons_cfg:
            self.draw_buttons(buttons_cfg)
        else:
            button_cfg = self.current_screen_data.get("button")
            if isinstance(button_cfg, dict):
                self.draw_single_button(button_cfg)
            else:
                self.current_buttons = []

        if footer:
            surf = self.font_footer.render(footer, True, text_color)
            rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 70))
            self.display.blit(surf, rect)

        if show_code_entry:
            code_text = f"Code: {self.code_buffer}_"
            surf = self.font_footer.render(code_text, True, text_color)
            rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 16))
            self.display.blit(surf, rect)

        small = self.font_small.render(f"screen: {self.current_screen_id}", True, text_color)
        small_rect = small.get_rect(left=10, bottom=self.screen_height - 10)
        self.display.blit(small, small_rect)

        pygame.display.flip()

    def go_to_screen(self, screen_id: str | None) -> None:
        if screen_id:
            self.load_screen(screen_id)

    def handle_button_press(self, button: ButtonSpec | None = None) -> None:
        if button and button.next_screen:
            self.go_to_screen(button.next_screen)
            return

        if self.current_buttons:
            first = self.current_buttons[0]
            if first.next_screen:
                self.go_to_screen(first.next_screen)

    def submit_code(self) -> None:
        code = self.code_buffer.strip().upper()
        self.code_buffer = ""

        if not code:
            return

        barcode_map = self.current_screen_data.get("barcode_map", {})
        if isinstance(barcode_map, dict):
            next_screen = barcode_map.get(code)
            if next_screen:
                self.go_to_screen(str(next_screen))
                return

        print(f"Unknown code: {code}")

    def check_timeout(self) -> None:
        timeout_s = self.current_screen_data.get("timeout_s")
        timeout_next = self.current_screen_data.get("timeout_next")

        if timeout_s is None or not timeout_next:
            return

        try:
            timeout_ms = int(float(timeout_s) * 1000)
        except Exception:
            return

        now = pygame.time.get_ticks()
        if now - self.screen_start_ms >= timeout_ms:
            self.go_to_screen(str(timeout_next))

    def handle_keydown(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_ESCAPE:
            self.running = False
            return

        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.code_buffer.strip():
                self.submit_code()
            else:
                self.handle_button_press()
            return

        if event.key == pygame.K_SPACE:
            self.handle_button_press()
            return

        if event.key == pygame.K_BACKSPACE:
            self.code_buffer = self.code_buffer[:-1]
            return

        if event.unicode and event.unicode.isprintable():
            if event.unicode.isalnum() or event.unicode in ("-", "_"):
                self.code_buffer += event.unicode.upper()

    def handle_mouse_down(self, pos: tuple[int, int]) -> None:
        for button in self.current_buttons:
            if button.rect and button.rect.collidepoint(pos):
                self.handle_button_press(button)
                return

    def run(self, start_screen: str = "main") -> None:
        self.load_screen(start_screen)

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False

                elif event.type == pygame.KEYDOWN:
                    self.handle_keydown(event)

                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.handle_mouse_down(event.pos)

                elif event.type == pygame.FINGERDOWN:
                    x = int(event.x * self.screen_width)
                    y = int(event.y * self.screen_height)
                    self.handle_mouse_down((x, y))

            self.check_hot_reload()
            self.check_timeout()
            self.draw_screen()
            self.clock.tick(30)

        pygame.quit()