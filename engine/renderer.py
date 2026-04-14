from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import math
import time
import os
import ctypes
import pygame
import yaml

import sys

if sys.platform.startswith("win"):
    from .usb_gpio import UsbGpio as GpioBackend
else:
    from .rpi_gpio import RpiGpio as GpioBackend


@dataclass
class ButtonSpec:
    text: str
    next_screen: str | None
    rect: pygame.Rect | None = None


class Renderer:
    def __init__(self, base_dir: Path, screen_width: int = 1920, screen_height: int = 1080):
        self.base_dir = base_dir
        self.screens_dir = self.base_dir / "screens"
        self.assets_dir = self.base_dir / "assets"
        self.animals_dir = self.base_dir / "animals"

        self.screen_width = screen_width
        self.screen_height = screen_height

        self.titleFontSize = 120
        self.textFontSize = 60
        self.buttonFontSize = 120
        self.footerFontSize = 28
        self.debugFontSize = 24

        # 1920x1080-tuned layout constants
        self.mainMenuMargin = 32
        self.mainMenuGap = 28
        self.mainMenuCompactGap = 22
        self.mainMenuLeftPanelWidth2Col = 620
        self.mainMenuLeftPanelWidth3Col = 500
        self.mainMenuLeftPanelInnerPad = 20

        self.profileMargin = 28
        self.profileGap = 22
        self.profileLeftWidth = 720
        self.profilePetPanelHeight = 118
        self.profileInfoPanelHeight = 280
        self.profileImageInset = 22
        self.profilePanelRadius = 24
        self.profilePanelBorderWidth = 6

        self.twoPanelMargin = 18
        self.twoPanelGap = 18
        self.twoPanelLeftWidth = 1080
        self.twoPanelButtonDiameter = 112
        self.twoPanelImageInset = 22
        self.twoPanelPanelRadius = 24
        self.twoPanelPanelBorderWidth = 4
        self.twoPanelTextTopPad = 28
        self.twoPanelTextSidePad = 22

        self.scan_audio_file = "scan_sound.wav"
        self.scan_audio_volume = 0.5
        self.scan_audio_fade_in_ms = 250
        self.scan_audio_fade_out_ms = 300

        pygame.init()

        try:
            pygame.mixer.init()
        except Exception as e:
            print(f"Audio init failed: {e}")

        pygame.display.set_caption("MRI Exhibit")
        self.display = pygame.display.set_mode(
            (self.screen_width, self.screen_height),
            pygame.FULLSCREEN,
            # display=1,
        )
        self.clock = pygame.time.Clock()
        self._font_cache: dict[int, pygame.font.Font] = {}
        self.refresh_fonts()

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

        self.animals_data = self.load_animals()
        self.startup_errors: list[str] = []
        self.gpio = GpioBackend()

        if not self.gpio.open():
            self.add_startup_error(self.gpio.last_error or "USB GPIO was not detected at startup.")

    def load_animals(self) -> dict[str, Any]:
        path = self.animals_dir / "animals.yaml"
        if not path.exists():
            return {}

        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict):
            raise ValueError(f"Top level animals YAML must be a mapping: {path}")

        return data

    def build_virtual_animal_screen(self, kind: str, animal_id: str) -> dict[str, Any]:
        animal = self.animals_data.get(animal_id)
        if not isinstance(animal, dict):
            raise ValueError(f"Unknown animal id: {animal_id}")

        pet_name = str(animal.get("pet_name", animal.get("display_name", animal.get("button_text", animal_id.title()))))
        name = str(animal.get("name", animal_id.title()))
        photo = animal.get("photo")
        mri_image = animal.get("mri_image")
        fact_lines = animal.get("fact_lines", [])
        scan_prompt = str(animal.get("scan_prompt", "Scan"))
        scan_duration_s = animal.get("scan_duration_s", 4)
        show_code_entry = bool(animal.get("show_code_entry", False))

        if not isinstance(fact_lines, list):
            fact_lines = []

        body = "\n".join(str(x) for x in fact_lines)

        if kind == "animal":
            return {
                "card_layout": "animal_profile",
                "pet_name": pet_name,
                "title": name,
                "body": body,
                "prompt": "Next",
                "image": photo,
                "fact_lines": [str(x) for x in fact_lines],
                "button": {
                    "text": "Next",
                    "next": f"instruction:{animal_id}",
                },
                "timeout_s": animal.get("animal_timeout_s", 40),
                "timeout_next": "main",
                "show_code_entry": show_code_entry,
            }

        if kind == "instruction":
            return {
                "instruction_layout": True,
                "instruction_text": str(animal.get("instruction_text", "Please put the animal into the MRI")),
                "instruction_video": animal.get("instruction_video"),  # optional
                "instruction_image": animal.get("instruction_image", photo),
                "timeout_s": None,  # no auto timeout
                "show_code_entry": show_code_entry,
                "next_scan": f"scan:{animal_id}",
            }


        if kind == "scan":
            return {
                "split_layout": "vertical",
                "scan_panel": {
                    "body": str(animal.get("scan_body", "Please hold still!")),
                    "image": photo,
                },
                "timeout_s": scan_duration_s,
                "timeout_next": f"result:{animal_id}",
                "show_code_entry": show_code_entry,
            }

        ####################

        if kind == "result":
            return {
                "prescription_layout": True,
                "image": mri_image,
                "title": str(animal.get("prescription_title", "Prescription")),
                "body": str(animal.get("prescription_text", "Help your animal heal!")),
                "timeout_s": animal.get("result_timeout_s", 20),
                "timeout_next": "main",
                "show_code_entry": show_code_entry,
            }

        ############
        raise ValueError(f"Unknown virtual screen kind: {kind}")

    def load_yaml(self, screen_id: str) -> dict[str, Any]:
        if ":" in screen_id:
            kind, animal_id = screen_id.split(":", 1)
            if kind in ("animal", "instruction", "scan", "result"):
                return self.build_virtual_animal_screen(kind, animal_id)

        if screen_id == "diagnostics":
            gpio_ok = self.gpio.is_present()
            gpio_text = f"Light GPIO: {'Present' if gpio_ok else 'Not found'}"
            if gpio_ok and getattr(self.gpio, "port", None):
                gpio_text = f"Light GPIO: Present ({self.gpio.port})"

            return {
                "title": "Diagnostics",
                "body": gpio_text,
                "buttons": [
                    {"text": "GPIO", "next": "diag_gpio_status"},
                    {"text": "Light On", "next": "diag_light_on"},
                    {"text": "Light Off", "next": "diag_light_off"},
                    {"text": "Audio", "next": "diag_play_audio"},
                    {"text": "Errors", "next": "diag_startup_errors"},
                    {"text": "Restart", "next": "diag_restart"},
                    {"text": "Exit App", "next": "diag_exit"},
                    {"text": "Home", "next": "main"},
                ],
                "show_code_entry": True,
                "timeout_s": None,
            }

        if screen_id == "diag_gpio_status":
            gpio_ok = self.gpio.is_present()
            if gpio_ok and getattr(self.gpio, "port", None):
                body = f"Light GPIO is present on {self.gpio.port}."
            else:
                body = self.gpio.last_error or "Light GPIO is not found."

            return {
                "title": "Light GPIO Status",
                "body": body,
                "button": {"text": "Back", "next": "diagnostics"},
                "show_code_entry": True,
                "timeout_s": None,
            }

        if screen_id == "diag_light_on":
            ok = self.gpio.light_on()
            if not ok and self.gpio.last_error:
                self.add_startup_error(self.gpio.last_error)

            return {
                "title": "Light Strip",
                "body": "Light strip ON command sent." if ok else (self.gpio.last_error or "Light strip ON command failed."),
                "button": {"text": "Back", "next": "diagnostics"},
                "show_code_entry": True,
                "timeout_s": None,
            }

        if screen_id == "diag_light_off":
            ok = self.gpio.light_off()
            if not ok and self.gpio.last_error:
                self.add_startup_error(self.gpio.last_error)

            return {
                "title": "Light Strip",
                "body": "Light strip OFF command sent." if ok else (self.gpio.last_error or "Light strip OFF command failed."),
                "button": {"text": "Back", "next": "diagnostics"},
                "show_code_entry": True,
                "timeout_s": None,
            }

        if screen_id == "diag_play_audio":
            ok = self.play_mri_audio_once()
            return {
                "title": "MRI Audio",
                "body": "MRI audio started." if ok else "MRI audio could not be played.",
                "button": {"text": "Back", "next": "diagnostics"},
                "show_code_entry": True,
                "timeout_s": None,
            }

        if screen_id == "diag_startup_errors":
            if self.startup_errors:
                body = "\n\n".join(self.startup_errors)
            else:
                body = "No startup errors were recorded."

            return {
                "title": "Startup Errors",
                "body": body,
                "button": {"text": "Back", "next": "diagnostics"},
                "show_code_entry": True,
                "timeout_s": None,
            }

        if screen_id == "diag_restart":
            return {
                "title": "Restart PC",
                "body": "Press Restart to reboot this PC and relaunch the exhibit.",
                "buttons": [
                    {"text": "Restart", "next": "diag_restart_now"},
                    {"text": "Back", "next": "diagnostics"},
                ],
                "show_code_entry": True,
                "timeout_s": None,
            }

        if screen_id == "diag_restart_now":
            self.restart_pc()
            return {
                "title": "Restarting",
                "body": "The PC is restarting now.",
                "show_code_entry": False,
            }

        if screen_id == "diag_exit":
            return {
                "title": "Exit Application",
                "body": "Press Exit to close the exhibit application and return to Windows.",
                "buttons": [
                    {"text": "Exit", "next": "diag_exit_now"},
                    {"text": "Back", "next": "diagnostics"},
                ],
                "show_code_entry": True,
                "timeout_s": None,
            }

        if screen_id == "diag_exit_now":
            self.shutdown_application()
            return {
                "title": "Exiting",
                "body": "The exhibit application is closing now.",
                "show_code_entry": False,
            }

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

        was_scan_screen = str(self.current_screen_id).startswith("scan:")
        is_scan_screen = str(screen_id).startswith("scan:")

        if was_scan_screen and not is_scan_screen:
            self.stop_scan_audio()
            ok = self.gpio.light_off()
            if not ok:
                msg = self.gpio.last_error or "Failed to turn scan light OFF."
                print(msg)
                self.add_startup_error(msg)

        self.current_screen_id = screen_id
        self.current_screen_data = data
        self.current_buttons = []
        self.code_buffer = ""
        self.screen_start_ms = pygame.time.get_ticks()

        if is_scan_screen:
            self.start_scan_audio()
            ok = self.gpio.light_on()
            if not ok:
                msg = self.gpio.last_error or "Failed to turn scan light ON."
                print(msg)
                self.add_startup_error(msg)

        print(f"\nLoaded screen: {screen_id}")
        print(data)

        self.refresh_watched_files()
    def resolve_animal_buttons(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            animal_id = item.get("animal_id")
            if not animal_id:
                resolved.append(item)
                continue

            animal = self.animals_data.get(str(animal_id))
            if not isinstance(animal, dict):
                print(f"Unknown animal_id in animal_buttons: {animal_id}")
                continue

            resolved.append(
                {
                    "text": str(animal.get("button_text", animal.get("name", animal_id))),
                    "image": animal.get("menu_image"),
                    "next": f"animal:{animal_id}",
                    "show_label": item.get("show_label", True),
                }
            )

        return resolved

    def get_watched_paths(self) -> list[Path]:
        paths: list[Path] = []

        animals_path = self.animals_dir / "animals.yaml"
        if animals_path.exists():
            paths.append(animals_path)

        if ":" not in self.current_screen_id:
            screen_path = self.screens_dir / f"{self.current_screen_id}.yaml"
            if screen_path.exists():
                paths.append(screen_path)

        bg_image = self.current_screen_data.get("bg_image")
        if bg_image:
            image_path = self.assets_dir / str(bg_image)
            if image_path.exists():
                paths.append(image_path)

        image_name = self.current_screen_data.get("image")
        if image_name:
            image_path = self.assets_dir / str(image_name)
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

        corner_cfg = self.current_screen_data.get("corner_button")
        if isinstance(corner_cfg, dict):
            icon_name = corner_cfg.get("icon")
            if icon_name:
                image_path = self.assets_dir / str(icon_name)
                if image_path.exists():
                    paths.append(image_path)

        buttons_cfg = self.current_screen_data.get("buttons")
        if isinstance(buttons_cfg, list):
            buttons_cfg = self.resolve_animal_buttons(buttons_cfg)
            for button_cfg in buttons_cfg:
                if isinstance(button_cfg, dict):
                    image_name = button_cfg.get("image")
                    if image_name:
                        image_path = self.assets_dir / str(image_name)
                        if image_path.exists():
                            paths.append(image_path)

        animal_buttons_cfg = self.current_screen_data.get("animal_buttons")
        if isinstance(animal_buttons_cfg, list):
            resolved = self.resolve_animal_buttons(animal_buttons_cfg)
            for button_cfg in resolved:
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

            self.animals_data = self.load_animals()
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

    def refresh_fonts(self) -> None:
        self._font_cache = {}
        self.font_title = self.get_font(self.titleFontSize)
        self.font_body = self.get_font(self.textFontSize)
        self.font_text = self.font_body
        self.font_button = self.get_font(self.buttonFontSize)
        self.font_footer = self.get_font(self.footerFontSize)
        self.font_debug = self.get_font(self.debugFontSize)

    def get_font(self, size: int) -> pygame.font.Font:
        size = max(8, int(size))
        font = self._font_cache.get(size)
        if font is None:
            font = pygame.font.SysFont(None, size)
            self._font_cache[size] = font
        return font

    def fit_font_to_width(self, text: str, max_width: int, start_size: int, min_size: int = 18) -> pygame.font.Font:
        size = max(min_size, int(start_size))
        while size > min_size:
            font = self.get_font(size)
            if font.size(text)[0] <= max_width:
                return font
            size -= 2
        return self.get_font(min_size)

    def fit_font_to_box(
        self,
        text: str,
        max_width: int,
        max_height: int,
        start_size: int,
        min_size: int = 18,
    ) -> pygame.font.Font:
        size = max(min_size, int(start_size))
        while size > min_size:
            font = self.get_font(size)
            surf = font.render(text, True, (0, 0, 0))
            if surf.get_width() <= max_width and surf.get_height() <= max_height:
                return font
            size -= 2
        return self.get_font(min_size)

    def get_two_panel_layout(self) -> dict[str, pygame.Rect]:
        margin = self.twoPanelMargin
        gap = self.twoPanelGap
        left_w = self.twoPanelLeftWidth
        right_x = margin + left_w + gap
        right_w = self.screen_width - right_x - margin
        button_d = self.twoPanelButtonDiameter
        button_y = self.screen_height - margin - button_d

        home_rect = pygame.Rect(
            right_x + right_w - button_d,
            button_y,
            button_d,
            button_d,
        )
        action_rect = pygame.Rect(
            right_x,
            button_y,
            home_rect.left - gap - right_x,
            button_d,
        )
        image_rect = pygame.Rect(
            margin,
            margin,
            left_w,
            self.screen_height - 2 * margin,
        )
        info_rect = pygame.Rect(
            right_x,
            margin,
            right_w,
            action_rect.top - gap - margin,
        )
        return {
            "image_rect": image_rect,
            "info_rect": info_rect,
            "action_rect": action_rect,
            "home_rect": home_rect,
        }

    def get_profile_layout(self) -> dict[str, pygame.Rect]:
        margin = self.profileMargin
        gap = self.profileGap
        left_w = self.profileLeftWidth
        right_w = self.screen_width - margin * 2 - left_w - gap

        pet_rect = pygame.Rect(margin, margin, left_w, self.profilePetPanelHeight)
        photo_rect = pygame.Rect(
            margin,
            pet_rect.bottom + gap,
            left_w,
            self.screen_height - margin - (pet_rect.bottom + gap),
        )
        info_rect = pygame.Rect(
            pet_rect.right + gap,
            margin,
            right_w,
            self.profileInfoPanelHeight,
        )
        button_rect = pygame.Rect(
            info_rect.left,
            info_rect.bottom + gap,
            right_w,
            self.screen_height - margin - (info_rect.bottom + gap),
        )
        return {
            "pet_rect": pet_rect,
            "photo_rect": photo_rect,
            "info_rect": info_rect,
            "button_rect": button_rect,
        }

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

        try:
            img = pygame.image.load(str(image_path)).convert_alpha()
            src_w, src_h = img.get_size()

            if src_w <= 0 or src_h <= 0 or rect.width <= 0 or rect.height <= 0:
                pygame.draw.rect(self.display, (150, 160, 175), rect, border_radius=10)
                return

            # Preserve aspect ratio and fill the destination rect without distortion.
            # This uses a "cover" fit: scale until the whole rect is filled, then crop
            # the overflow evenly from the center.
            scale = max(rect.width / src_w, rect.height / src_h)
            scaled_w = max(1, int(round(src_w * scale)))
            scaled_h = max(1, int(round(src_h * scale)))

            img = pygame.transform.smoothscale(img, (scaled_w, scaled_h))

            crop_rect = pygame.Rect(
                max(0, (scaled_w - rect.width) // 2),
                max(0, (scaled_h - rect.height) // 2),
                rect.width,
                rect.height,
            )

            self.display.blit(img, rect, crop_rect)
        except Exception as e:
            print(f"Failed to load image {image_path}: {e}")
            pygame.draw.rect(self.display, (150, 160, 175), rect, border_radius=10)


    def draw_image_in_circle(self, image_name: str | None, center: tuple[int, int], diameter: int) -> None:
        circle_rect = pygame.Rect(0, 0, diameter, diameter)
        circle_rect.center = center

        if not image_name:
            pygame.draw.circle(self.display, (150, 160, 175), center, diameter // 2)
            return

        image_path = self.assets_dir / str(image_name)

        try:
            img = pygame.image.load(str(image_path)).convert_alpha()

            src_w, src_h = img.get_size()
            scale = max(diameter / max(1, src_w), diameter / max(1, src_h))
            scaled_size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
            img = pygame.transform.smoothscale(img, scaled_size)

            crop_rect = img.get_rect(center=(scaled_size[0] // 2, scaled_size[1] // 2))
            crop_rect.center = (scaled_size[0] // 2, scaled_size[1] // 2)

            circle_surface = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
            draw_rect = img.get_rect(center=(diameter // 2, diameter // 2))
            circle_surface.blit(img, draw_rect)

            mask = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
            pygame.draw.circle(mask, (255, 255, 255, 255), (diameter // 2, diameter // 2), diameter // 2)
            circle_surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

            self.display.blit(circle_surface, circle_rect.topleft)
        except Exception as e:
            print(f"Failed to load circular image {image_path}: {e}")
            pygame.draw.circle(self.display, (150, 160, 175), center, diameter // 2)

    def draw_scan_circle_screen(self) -> bool:
        if not str(self.current_screen_id).startswith("scan:"):
            return False

        bg_color = self.get_color("bg_color", (20, 40, 70))
        text_color = self.get_color("text_color", (255, 255, 255))
        self.display.fill(bg_color)
        self.current_buttons = []

        scan_panel = self.current_screen_data.get("scan_panel", {})
        if not isinstance(scan_panel, dict):
            scan_panel = {}

        scan_image = scan_panel.get("image")
        scan_body = str(scan_panel.get("body", "Please hold still!")).strip()

        center = (self.screen_width // 2, self.screen_height // 2 - 18)
        image_diameter = min(self.screen_width, self.screen_height) - 190
        image_diameter = max(180, min(260, image_diameter))
        spinner_outer_d = image_diameter + 36
        spinner_inner_d = image_diameter + 12

        # soft glow behind the animal circle
        glow = pygame.Surface((spinner_outer_d + 70, spinner_outer_d + 70), pygame.SRCALPHA)
        glow_rect = glow.get_rect(center=center)
        for i in range(4):
            radius = spinner_outer_d // 2 + 10 + i * 8
            alpha = max(0, 40 - i * 8)
            pygame.draw.circle(
                glow,
                (255, 255, 255, alpha),
                (glow.get_width() // 2, glow.get_height() // 2),
                radius,
            )
        self.display.blit(glow, glow_rect.topleft)

        # rotating spinner ring
        ring_rect = pygame.Rect(0, 0, spinner_outer_d, spinner_outer_d)
        ring_rect.center = center
        arc_rect = ring_rect.inflate(-8, -8)

        t = pygame.time.get_ticks() / 1000.0
        base_angle = t * 1.7
        segment_count = 12
        segment_span = 0.34

        for i in range(segment_count):
            fade = (i + 1) / segment_count
            alpha = int(35 + 190 * fade)
            color = (255, 255, 255, alpha)
            start = base_angle - i * 0.28
            end = start + segment_span

            ring_surface = pygame.Surface((ring_rect.width, ring_rect.height), pygame.SRCALPHA)
            pygame.draw.arc(
                ring_surface,
                color,
                pygame.Rect(4, 4, arc_rect.width, arc_rect.height),
                start,
                end,
                8,
            )
            self.display.blit(ring_surface, ring_rect.topleft)

        # subtle inner ring
        pygame.draw.circle(self.display, (214, 236, 247), center, spinner_inner_d // 2)
        pygame.draw.circle(self.display, (255, 255, 255), center, spinner_inner_d // 2, 3)

        self.draw_image_in_circle(scan_image, center, image_diameter)

        # small caption below
        caption_font = self.font_text
        caption = caption_font.render("Scanning...", True, text_color)
        caption_rect = caption.get_rect(center=(self.screen_width // 2, center[1] + spinner_outer_d // 2 + 34))
        self.display.blit(caption, caption_rect)
        return True







    def draw_scanner_panel(self, rect: pygame.Rect, scan_image: str | None, t: float) -> None:
        panel_face = (214, 220, 188)
        panel_border = (170, 176, 145)
        bay_face = (225, 230, 205)
        beam_color = (255, 50, 50)

        pygame.draw.rect(self.display, panel_face, rect, border_radius=18)
        pygame.draw.rect(self.display, panel_border, rect, width=3, border_radius=18)

        img_box = pygame.Rect(rect.left + 14, rect.top + 14, rect.width - 28, rect.height - 28)
        pygame.draw.rect(self.display, bay_face, img_box, border_radius=12)

        padding = 10
        inner_rect = pygame.Rect(
            img_box.left + padding,
            img_box.top + padding,
            img_box.width - padding * 2,
            img_box.height - padding * 2,
        )
        self.draw_image_into_rect(scan_image, inner_rect)

        is_scan_screen = str(self.current_screen_id).startswith("scan:")

        if is_scan_screen:
            # Strong full-panel pulsing red glow
            pulse = (math.sin(t * 5.0) + 1.0) * 0.5
            glow_alpha = int(45 + pulse * 70)
            bay_glow = pygame.Surface((img_box.width, img_box.height), pygame.SRCALPHA)
            bay_glow.fill((255, 70, 70, glow_alpha))
            self.display.blit(bay_glow, img_box.topleft)

            # Horizontal animated scan bands
            band_spacing = 26
            band_offset = int((t * 80) % band_spacing)
            for y in range(img_box.top - band_spacing, img_box.bottom, band_spacing):
                yy = y + band_offset
                if img_box.top <= yy <= img_box.bottom:
                    pygame.draw.line(
                        self.display,
                        (255, 180, 180),
                        (img_box.left + 8, yy),
                        (img_box.right - 8, yy),
                        2,
                    )

            # Big translucent vertical beam
            beam_margin = 18
            beam_left = img_box.left + beam_margin
            beam_right = img_box.right - beam_margin
            sweep = (math.sin(t * 3.2) + 1.0) * 0.5
            beam_x = int(beam_left + sweep * (beam_right - beam_left))

            beam_width = 46
            beam_height = max(1, img_box.height - 24)
            beam_surface = pygame.Surface((beam_width, beam_height), pygame.SRCALPHA)

            for x in range(beam_width):
                dist = abs(x - beam_width // 2)
                alpha = max(0, 190 - dist * 12)
                pygame.draw.line(
                    beam_surface,
                    (255, 60, 60, alpha),
                    (x, 0),
                    (x, beam_height),
                    1,
                )

            self.display.blit(beam_surface, (beam_x - beam_width // 2, img_box.top + 12))

            # Bright center beam line
            pygame.draw.line(
                self.display,
                beam_color,
                (beam_x, img_box.top + 8),
                (beam_x, img_box.bottom - 8),
                5,
            )

            # Active label inside scanner bay
            scan_font = self.get_font(max(24, self.textFontSize - 6))
            label = scan_font.render("SCANNING...", True, (255, 255, 255))
            label_bg = pygame.Surface((label.get_width() + 20, label.get_height() + 10), pygame.SRCALPHA)
            label_bg.fill((180, 0, 0, 170))
            label_rect = label_bg.get_rect(midtop=(img_box.centerx, img_box.top + 10))
            self.display.blit(label_bg, label_rect)
            self.display.blit(label, label.get_rect(center=label_rect.center))

        else:
            # Menu screen version: milder effect
            beam_margin = 22
            beam_left = img_box.left + beam_margin
            beam_right = img_box.right - beam_margin
            sweep = (math.sin(t * 2.4) + 1.0) * 0.5
            beam_x = int(beam_left + sweep * (beam_right - beam_left))

            glow = pygame.Surface((12, max(1, img_box.height - 44)), pygame.SRCALPHA)
            glow.fill((255, 90, 90, 60))
            self.display.blit(glow, (beam_x - 6, img_box.top + 22))
            pygame.draw.line(
                self.display,
                (220, 50, 50),
                (beam_x, img_box.top + 22),
                (beam_x, img_box.bottom - 22),
                3,
            )





    def draw_scan_complete_overlay(self) -> None:
        elapsed_s = (pygame.time.get_ticks() - self.screen_start_ms) / 1000.0
        timeout_s = self.current_screen_data.get("timeout_s")

        try:
            total_s = float(timeout_s)
        except Exception:
            return

        flash_duration_s = 0.5
        flash_start_s = max(0.0, total_s - flash_duration_s)

        if elapsed_s < flash_start_s:
            return

        progress = (elapsed_s - flash_start_s) / flash_duration_s
        progress = max(0.0, min(1.0, progress))

        # Brighten rapidly near the end
        alpha = int(40 + progress * 180)

        overlay = pygame.Surface((self.screen_width, self.screen_height), pygame.SRCALPHA)
        overlay.fill((255, 255, 255, alpha))
        self.display.blit(overlay, (0, 0))

        # "Scan Complete!" text fades in during the flash
        text_alpha = int(120 + progress * 135)
        text_surface = self.font_title.render("Scan Complete!", True, (255, 255, 255))
        text_surface.set_alpha(text_alpha)
        text_rect = text_surface.get_rect(center=(self.screen_width // 2, self.screen_height - 70))
        self.display.blit(text_surface, text_rect)







    def draw_animal_button(
        self,
        button_cfg: dict[str, Any],
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        index: int = 0,
        animate: bool = False,
        t: float = 0.0,
    ) -> None:
        text = str(button_cfg.get("text", "")).strip()
        next_screen = button_cfg.get("next")
        image_name = button_cfg.get("image")
        show_label = bool(button_cfg.get("show_label", True))

        if not text:
            return

        bounce_y = 0
        shadow_offset_y = 6
        if animate:
            bounce_y = int(round(math.sin(t * 3.2 + index * 0.65) * 2.0))
            shadow_offset_y = 6 + max(0, bounce_y)

        border_radius = max(12, min(18, min(width, height) // 7))
        outer_pad = max(8, min(14, min(width, height) // 12))
        label_band_h = 0
        label_gap = 0

        if show_label:
            label_band_h = max(50, min(80, height // 4))
            label_gap = max(6, min(10, height // 18))

        rect = pygame.Rect(x, y + bounce_y, width, height)
        shadow_rect = rect.move(4, shadow_offset_y)

        pygame.draw.rect(self.display, (5, 12, 22), shadow_rect, border_radius=border_radius)
        pygame.draw.rect(self.display, (242, 242, 242), rect, border_radius=border_radius)

        inner_rect = rect.inflate(-outer_pad, -outer_pad)
        pygame.draw.rect(self.display, (228, 228, 228), inner_rect, border_radius=max(10, border_radius - 4))
        pygame.draw.rect(self.display, (28, 37, 52), rect, width=3, border_radius=border_radius)

        image_bottom_inset = outer_pad + (label_band_h + label_gap if show_label else 0)
        image_rect = pygame.Rect(
            rect.x + outer_pad,
            rect.y + outer_pad,
            width - outer_pad * 2,
            max(20, height - outer_pad - image_bottom_inset),
        )

        self.draw_image_into_rect(image_name, image_rect)

        if show_label:
            label_max_width = width - 18
            label_bottom = rect.bottom - max(6, outer_pad - 1)
            line_gap = 2

            font_size = self.buttonFontSize
            label_font = self.get_font(font_size)
            wrapped_lines = self.wrap_text(text, label_font, label_max_width)

            while font_size > 16:
                wrapped_lines = self.wrap_text(text, label_font, label_max_width)
                too_many_lines = len(wrapped_lines) > 3
                line_height = label_font.get_height()
                total_height = len(wrapped_lines) * line_height + max(0, len(wrapped_lines) - 1) * line_gap
                too_tall = total_height > (label_band_h + 8)

                if not too_many_lines and not too_tall:
                    break

                font_size -= 2
                label_font = self.get_font(font_size)

            line_height = label_font.get_height()
            total_height = len(wrapped_lines) * line_height + max(0, len(wrapped_lines) - 1) * line_gap
            y = label_bottom - total_height

            for line in wrapped_lines:
                surf = label_font.render(line, True, (20, 20, 20))
                surf_rect = surf.get_rect(centerx=rect.centerx, top=y)
                self.display.blit(surf, surf_rect)
                y = surf_rect.bottom + line_gap

        self.current_buttons.append(
            ButtonSpec(
                text=text,
                next_screen=str(next_screen) if next_screen else None,
                rect=rect,
            )
        )
    def draw_buttons(self, buttons_cfg: list[dict[str, Any]], *, t: float = 0.0, animate: bool = False) -> None:
        self.current_buttons = []

        if not buttons_cfg:
            return

        visible_buttons = []
        for button_cfg in buttons_cfg:
            text = str(button_cfg.get("text", "")).strip()
            if text:
                visible_buttons.append(button_cfg)

        count = len(visible_buttons)
        if count == 0:
            return

        if count <= 5:
            rows = 1
            cols = count
        else:
            rows = 2
            cols = (count + 1) // 2

        margin_x = 24
        bottom_margin = 34
        row_gap = 18
        col_gap = 20

        max_total_width = self.screen_width - 2 * margin_x

        button_width = min(210, (max_total_width - col_gap * (cols - 1)) // cols)
        button_width = max(150, button_width)

        button_height = 180
        if rows == 2:
            button_height = 150

        total_height = rows * button_height + (rows - 1) * row_gap
        start_y = self.screen_height - bottom_margin - total_height

        index = 0
        for row in range(rows):
            remaining = count - index
            items_in_row = min(cols, remaining)

            row_width = items_in_row * button_width + (items_in_row - 1) * col_gap
            start_x = (self.screen_width - row_width) // 2
            y = start_y + row * (button_height + row_gap)

            for col in range(items_in_row):
                button_cfg = visible_buttons[index]
                x = start_x + col * (button_width + col_gap)

                self.draw_animal_button(
                    button_cfg,
                    x,
                    y,
                    button_width,
                    button_height,
                    index=index,
                    animate=animate,
                    t=t,
                )
                index += 1
    def draw_split_main_screen(self) -> bool:
        split_layout = self.current_screen_data.get("split_layout")
        if split_layout != "vertical":
            return False

        t = pygame.time.get_ticks() / 1000.0

        buttons_cfg = self.current_screen_data.get("buttons")
        if not isinstance(buttons_cfg, list):
            buttons_cfg = self.current_screen_data.get("animal_buttons", [])
        if not isinstance(buttons_cfg, list):
            buttons_cfg = []
        buttons_cfg = self.resolve_animal_buttons(buttons_cfg)

        count = min(len(buttons_cfg), 6)
        compact_grid = count > 4

        margin = self.mainMenuMargin
        gap = self.mainMenuGap if not compact_grid else self.mainMenuCompactGap
        content_height = self.screen_height - 2 * margin
        usable_total_width = self.screen_width - 2 * margin

        cols = 2 if count <= 4 else 3 if count > 0 else 2

        default_left_panel_width = (
            self.mainMenuLeftPanelWidth2Col if cols == 2 else self.mainMenuLeftPanelWidth3Col
        )

        left_panel_width = int(self.current_screen_data.get("left_panel_width", default_left_panel_width))
        right_panel_width = usable_total_width - left_panel_width - gap

        # Center the full two-panel composition as a single layout.
        content_left = (self.screen_width - (left_panel_width + gap + right_panel_width)) // 2

        left_rect = pygame.Rect(
            content_left,
            margin,
            left_panel_width,
            content_height,
        )

        right_rect = pygame.Rect(
            left_rect.right + gap,
            margin,
            right_panel_width,
            content_height,
        )

        panel_color = self.get_color("bg_color", (20, 40, 70))

        pygame.draw.rect(self.display, panel_color, left_rect, border_radius=18)
        pygame.draw.rect(self.display, panel_color, right_rect, border_radius=18)

        scan_panel = self.current_screen_data.get("scan_panel", {})
        if not isinstance(scan_panel, dict):
            scan_panel = {}

        scan_body = str(scan_panel.get("body", "Scan your animal card\nor touch an animal."))
        scan_image = scan_panel.get("image")

        text_color = self.get_color("text_color", (255, 255, 255))
        body_font = self.font_body if not compact_grid else self.get_font(max(26, self.textFontSize - 8))

        # Left panel content
        image_height_ratio = 0.58 if not compact_grid else 0.52
        image_height = int(left_rect.height * image_height_ratio)

        body_lines = self.wrap_text(scan_body, body_font, left_rect.width - 24)
        body_height = sum(body_font.size(line)[1] + 4 for line in body_lines)

        gap_between = 12
        total_height = image_height + gap_between + body_height

        y = left_rect.top + (left_rect.height - total_height) // 2

        image_rect = pygame.Rect(
            left_rect.left + self.mainMenuLeftPanelInnerPad,
            y,
            left_rect.width - self.mainMenuLeftPanelInnerPad * 2,
            image_height,
        )
        self.draw_scanner_panel(image_rect, scan_image, t)

        y = image_rect.bottom + gap_between

        for line in body_lines:
            surf = body_font.render(line, True, text_color)
            rect = surf.get_rect(centerx=left_rect.centerx, top=y)
            self.display.blit(surf, rect)
            y = rect.bottom + 4

        self.current_buttons = []

        if count == 0:
            return True

        rows = (count + cols - 1) // cols

        inner_margin_x = 18 if not compact_grid else 12
        inner_margin_top = 20 if not compact_grid else 14
        inner_margin_bottom = 18 if not compact_grid else 14
        cell_gap_x = 14 if not compact_grid else 10
        cell_gap_y = 18 if not compact_grid else 10

        usable_width = right_rect.width - 2 * inner_margin_x
        usable_height = right_rect.height - inner_margin_top - inner_margin_bottom

        button_w = (usable_width - cell_gap_x * (cols - 1)) // cols
        button_h = (usable_height - cell_gap_y * (rows - 1)) // rows

        if compact_grid:
            button_h = min(button_h, int(button_w * 1.02))
        else:
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

            items_in_this_row = min(cols, count - row * cols)
            if items_in_this_row < cols:
                row_width = items_in_this_row * button_w + (items_in_this_row - 1) * cell_gap_x
                row_start_x = right_rect.left + (right_rect.width - row_width) // 2
                x = row_start_x + (i % cols) * (button_w + cell_gap_x)
            else:
                x = grid_start_x + col * (button_w + cell_gap_x)

            y = grid_start_y + row * (button_h + cell_gap_y)

            self.draw_animal_button(
                button_cfg,
                x,
                y,
                button_w,
                button_h,
                index=i,
                animate=True,
                t=t,
            )

        return True

    def draw_split_main_screenDelete(self) -> bool:
        split_layout = self.current_screen_data.get("split_layout")
        if split_layout != "vertical":
            return False

        t = pygame.time.get_ticks() / 1000.0

        buttons_cfg = self.current_screen_data.get("buttons")
        if not isinstance(buttons_cfg, list):
            buttons_cfg = self.current_screen_data.get("animal_buttons", [])
        if not isinstance(buttons_cfg, list):
            buttons_cfg = []
        buttons_cfg = self.resolve_animal_buttons(buttons_cfg)

        count = min(len(buttons_cfg), 6)
        compact_grid = count > 4

        margin = 20
        gap = 20 if not compact_grid else 14
        left_panel_width = int(self.current_screen_data.get("left_panel_width", 260 if not compact_grid else 225))

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

        scan_body = str(scan_panel.get("body", "Scan your animal card\nor touch an animal."))
        scan_image = scan_panel.get("image")       

        text_color = self.get_color("text_color", (255, 255, 255))

        body_font = self.font_body if not compact_grid else self.get_font(max(26, self.textFontSize - 8))

        # --- Compute sizes first ---
        image_height_ratio = 0.58 if not compact_grid else 0.52
        image_height = int(left_rect.height * image_height_ratio)

        body_lines = self.wrap_text(scan_body, body_font, left_rect.width - 24)
        body_height = sum(body_font.size(line)[1] + 4 for line in body_lines)

        gap_between = 12
        total_height = image_height + gap_between + body_height

        # --- Center vertically ---
        y = left_rect.top + (left_rect.height - total_height) // 2

        # --- Draw scanner ---
        image_rect = pygame.Rect(
            left_rect.left + 16,
            y,
            left_rect.width - 32,
            image_height,
        )
        self.draw_scanner_panel(image_rect, scan_image, t)

        y = image_rect.bottom + gap_between

        # --- Draw text ---
        for line in body_lines:
            surf = body_font.render(line, True, text_color)
            rect = surf.get_rect(centerx=left_rect.centerx, top=y)
            self.display.blit(surf, rect)
            y = rect.bottom + 4


        self.current_buttons = []

        if count == 0:
            return True

        cols = 2 if count <= 4 else 3
        rows = (count + cols - 1) // cols

        inner_margin_x = 18 if not compact_grid else 12
        inner_margin_top = 20 if not compact_grid else 14
        inner_margin_bottom = 18 if not compact_grid else 14
        cell_gap_x = 14 if not compact_grid else 10
        cell_gap_y = 18 if not compact_grid else 10

        usable_width = right_rect.width - 2 * inner_margin_x
        usable_height = right_rect.height - inner_margin_top - inner_margin_bottom

        button_w = (usable_width - cell_gap_x * (cols - 1)) // cols
        button_h = (usable_height - cell_gap_y * (rows - 1)) // rows

        if compact_grid:
            button_h = min(button_h, int(button_w * 1.02))
        else:
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

            items_in_this_row = min(cols, count - row * cols)
            if items_in_this_row < cols:
                row_width = items_in_this_row * button_w + (items_in_this_row - 1) * cell_gap_x
                row_start_x = right_rect.left + (right_rect.width - row_width) // 2
                x = row_start_x + (i % cols) * (button_w + cell_gap_x)
            else:
                x = grid_start_x + col * (button_w + cell_gap_x)

            y = grid_start_y + row * (button_h + cell_gap_y)

            self.draw_animal_button(button_cfg, x, y, button_w, button_h, index=i, animate=True, t=t)

        return True

        cols = 2 if count <= 4 else 3
        rows = (count + cols - 1) // cols

        inner_margin_x = 18
        inner_margin_top = 20
        inner_margin_bottom = 18
        cell_gap_x = 14
        cell_gap_y = 18

        usable_width = right_rect.width - 2 * inner_margin_x
        usable_height = right_rect.height - inner_margin_top - inner_margin_bottom

        button_w = (usable_width - cell_gap_x * (cols - 1)) // cols
        button_h = (usable_height - cell_gap_y * (rows - 1)) // rows

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

            items_in_this_row = min(cols, count - row * cols)
            if items_in_this_row < cols:
                row_width = items_in_this_row * button_w + (items_in_this_row - 1) * cell_gap_x
                row_start_x = right_rect.left + (right_rect.width - row_width) // 2
                x = row_start_x + (i % cols) * (button_w + cell_gap_x)
            else:
                x = grid_start_x + col * (button_w + cell_gap_x)

            y = grid_start_y + row * (button_h + cell_gap_y)

            self.draw_animal_button(button_cfg, x, y, button_w, button_h, index=i, animate=True, t=t)

        return True

    def draw_scan_action_button(self, rect: pygame.Rect, text: str, *, t: float = 0.0) -> None:
        shadow_rect = rect.move(0, 8)
        pygame.draw.ellipse(self.display, (18, 52, 82), shadow_rect)

        ring_rect = rect.inflate(12, 12)
        pygame.draw.ellipse(self.display, (245, 244, 229), ring_rect)

        center_rect = rect.inflate(-24, -24)
        pulse = (math.sin(t * 4.0) + 1.0) * 0.5
        green = (170 + int(18 * pulse), 220 + int(12 * pulse), 64)
        pygame.draw.ellipse(self.display, green, center_rect)
        pygame.draw.ellipse(self.display, (140, 185, 45), center_rect, width=4)

        segments = 10
        outer_rx = ring_rect.width // 2 - 6
        outer_ry = ring_rect.height // 2 - 6
        cx, cy = ring_rect.center
        seg_w = 16
        seg_h = 7
        for i in range(segments):
            angle = (math.tau / segments) * i + t * 0.15
            x = cx + int(math.cos(angle) * outer_rx * 0.86)
            y = cy + int(math.sin(angle) * outer_ry * 0.86)
            seg = pygame.Rect(0, 0, seg_w, seg_h)
            seg.center = (x, y)
            pygame.draw.ellipse(self.display, (49, 101, 144), seg)

        button_font = self.fit_font_to_box(text, center_rect.width - 18, center_rect.height - 12, self.buttonFontSize, 20)
        label = button_font.render(text, True, (64, 125, 54))
        label_rect = label.get_rect(center=center_rect.center)
        self.display.blit(label, label_rect)

    def draw_animal_profile_screen(self) -> bool:
        if self.current_screen_data.get("card_layout") != "animal_profile":
            return False

        bg_color = (36, 95, 136)
        panel_fill = (214, 236, 247)
        panel_border = (245, 248, 250)
        text_color = (30, 30, 30)

        self.display.fill(bg_color)
        self.current_buttons = []

        layout = self.get_profile_layout()
        pet_rect = layout["pet_rect"]
        photo_rect = layout["photo_rect"]
        info_rect = layout["info_rect"]
        button_rect = layout["button_rect"]

        for rect in (pet_rect, photo_rect, info_rect):
            pygame.draw.rect(self.display, panel_fill, rect, border_radius=self.profilePanelRadius)
            pygame.draw.rect(
                self.display,
                panel_border,
                rect,
                width=self.profilePanelBorderWidth,
                border_radius=self.profilePanelRadius,
            )

        pet_name = self.get_text("pet_name", self.get_text("title", ""))
        pet_font = self.fit_font_to_width(pet_name, pet_rect.width - 32, self.titleFontSize, 34)
        pet_surf = pet_font.render(pet_name, True, text_color)
        pet_text_rect = pet_surf.get_rect(center=pet_rect.center)
        self.display.blit(pet_surf, pet_text_rect)

        image_inner = photo_rect.inflate(-self.profileImageInset, -self.profileImageInset)
        self.draw_image_into_rect(self.current_screen_data.get("image"), image_inner)
        pygame.draw.rect(self.display, panel_border, image_inner, width=4, border_radius=16)

        animal_name = self.get_text("title", "")
        name_font = self.fit_font_to_width(animal_name, info_rect.width - 28, self.titleFontSize, 28)
        name_lines = self.wrap_text(animal_name, name_font, info_rect.width - 28)

        total_height = sum(name_font.size(line)[1] + 2 for line in name_lines)
        y = info_rect.top + (info_rect.height - total_height) // 2

        for line in name_lines:
            surf = name_font.render(line, True, text_color)
            rect = surf.get_rect(centerx=info_rect.centerx, top=y)
            self.display.blit(surf, rect)
            y = rect.bottom + 2

        facts = self.current_screen_data.get("fact_lines")
        if not isinstance(facts, list):
            body_text = self.get_text("body", "")
            facts = [line for line in body_text.splitlines() if line.strip()]

        fact_font = self.font_text
        y += 10
        for raw_line in facts:
            for line in self.wrap_text(str(raw_line), fact_font, info_rect.width - 30):
                surf = fact_font.render(line, True, text_color)
                self.display.blit(surf, (info_rect.left + 16, y))
                y += surf.get_height() + 6

        t = pygame.time.get_ticks() / 1000.0
        self.draw_scan_action_button(button_rect, self.get_text("prompt", "Scan"), t=t)

        button_cfg = self.current_screen_data.get("button")
        next_screen = None
        if isinstance(button_cfg, dict):
            next_screen = button_cfg.get("next")
        self.current_buttons.append(ButtonSpec(text=self.get_text("prompt", "Scan"), next_screen=str(next_screen) if next_screen else None, rect=button_rect))

        show_code_entry = bool(self.current_screen_data.get("show_code_entry", True))
        if show_code_entry:
            code_text = f"Code: {self.code_buffer}_"
            surf = self.font_footer.render(code_text, True, (255, 255, 255))
            rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 10))
            self.display.blit(surf, rect)

        small = self.font_debug.render(f"screen: {self.current_screen_id}", True, (255, 255, 255))
        small_rect = small.get_rect(left=10, bottom=self.screen_height - 10)
        self.display.blit(small, small_rect)

        pygame.display.flip()
        return True


    def draw_round_button(
        self,
        rect: pygame.Rect,
        *,
        fill_color: tuple[int, int, int],
        border_color: tuple[int, int, int],
        text: str = "",
        text_color: tuple[int, int, int] = (20, 20, 20),
        font: pygame.font.Font | None = None,
        icon_kind: str | None = None,
        pulse: bool = False,
    ) -> None:
        draw_rect = rect.copy()

        if pulse:
            t = pygame.time.get_ticks() / 1000.0
            scale = 1.0 + 0.04 * math.sin(t * 4.0)
            new_w = max(10, int(rect.width * scale))
            new_h = max(10, int(rect.height * scale))
            draw_rect = pygame.Rect(0, 0, new_w, new_h)
            draw_rect.center = rect.center

        shadow_rect = draw_rect.move(4, 6)
        pygame.draw.ellipse(self.display, (5, 12, 22), shadow_rect)
        pygame.draw.ellipse(self.display, fill_color, draw_rect)
        pygame.draw.ellipse(self.display, border_color, draw_rect, 3)

        if icon_kind == "home":
            cx, cy = draw_rect.center
            roof = [
                (cx, cy - 18),
                (cx - 20, cy - 2),
                (cx + 20, cy - 2),
            ]
            pygame.draw.polygon(self.display, text_color, roof)
            body_rect = pygame.Rect(cx - 15, cy - 2, 30, 24)
            pygame.draw.rect(self.display, text_color, body_rect, border_radius=4)
            door_rect = pygame.Rect(cx - 5, cy + 8, 10, 14)
            pygame.draw.rect(self.display, fill_color, door_rect, border_radius=2)
            return

        if text:
            max_width = draw_rect.width - 20
            max_height = draw_rect.height - 20

            font = self.fit_font_to_box(text, max_width, max_height, self.buttonFontSize, 12)
            surf = font.render(text, True, text_color)
            surf_rect = surf.get_rect(center=draw_rect.center)
            self.display.blit(surf, surf_rect)

    #############
    def draw_rect_button(
        self,
        rect: pygame.Rect,
        *,
        fill_color: tuple[int, int, int],
        border_color: tuple[int, int, int],
        text: str,
        text_color: tuple[int, int, int] = (20, 20, 20),
        pulse: bool = False,
    ) -> None:
        draw_rect = rect.copy()

        if pulse:
            t = pygame.time.get_ticks() / 1000.0
            grow_x = int(4 * math.sin(t * 4.0))
            grow_y = int(3 * math.sin(t * 4.0))
            draw_rect = pygame.Rect(
                rect.x - grow_x // 2,
                rect.y - grow_y // 2,
                rect.width + grow_x,
                rect.height + grow_y,
            )

        shadow_rect = draw_rect.move(4, 6)
        pygame.draw.rect(self.display, (5, 12, 22), shadow_rect, border_radius=22)
        pygame.draw.rect(self.display, fill_color, draw_rect, border_radius=22)
        pygame.draw.rect(self.display, border_color, draw_rect, width=3, border_radius=22)

        max_width = draw_rect.width - 24
        max_height = draw_rect.height - 20

        font = self.fit_font_to_box(text, max_width, max_height, self.buttonFontSize, 14)
        surf = font.render(text, True, text_color)
        surf_rect = surf.get_rect(center=draw_rect.center)
        self.display.blit(surf, surf_rect)


    ##################

    def draw_animal_detail_screen(self) -> bool:
        if not str(self.current_screen_id).startswith("animal:"):
            return False

        text_color = self.get_color("text_color", (30, 30, 30))
        panel_fill = (200, 225, 240)
        panel_border = (255, 255, 255)
        bg_color = self.get_color("bg_color", (34, 87, 122))
        self.display.fill(bg_color)
        self.current_buttons = []

        pet_name = self.get_text("pet_name", self.get_text("title", ""))
        animal_name = self.get_text("title", "")
        fact_text = self.get_text("body", "")
        image_name = self.current_screen_data.get("image")
        show_code_entry = bool(self.current_screen_data.get("show_code_entry", True))

        layout = self.get_two_panel_layout()
        photo_rect = layout["image_rect"]
        info_rect = layout["info_rect"]
        next_rect = layout["action_rect"]
        home_rect = layout["home_rect"]

        pet_font = self.fit_font_to_width(pet_name, photo_rect.width - 32, self.titleFontSize, 28)
        pet_surf = pet_font.render(pet_name, True, text_color)
        pet_h = max(72, pet_surf.get_height() + 30)

        pet_rect = pygame.Rect(
            photo_rect.left,
            self.twoPanelMargin,
            photo_rect.width,
            pet_h,
        )
        photo_rect = pygame.Rect(
            photo_rect.left,
            pet_rect.bottom + self.twoPanelGap,
            photo_rect.width,
            self.screen_height - self.twoPanelMargin - (pet_rect.bottom + self.twoPanelGap),
        )

        for rect in (pet_rect, photo_rect, info_rect):
            pygame.draw.rect(self.display, panel_fill, rect, border_radius=self.twoPanelPanelRadius)
            pygame.draw.rect(
                self.display,
                panel_border,
                rect,
                width=self.twoPanelPanelBorderWidth,
                border_radius=self.twoPanelPanelRadius,
            )

        # Pet name: single fitted line.
        pet_text_rect = pet_surf.get_rect(center=pet_rect.center)
        self.display.blit(pet_surf, pet_text_rect)

        # Photo
        inner_photo = photo_rect.inflate(-self.twoPanelImageInset, -self.twoPanelImageInset)
        self.draw_image_into_rect(str(image_name) if image_name else None, inner_photo)

        # Animal info
        ####################
        # --- Larger fonts for 1200x800 ---
        name_font = self.font_title
        info_font = self.font_text

        # --- Wrap text ---
        name_lines = self.wrap_text(animal_name, name_font, info_rect.width - 24)

        fact_lines_wrapped: list[str] = []
        for line in fact_text.splitlines():
            wrapped = self.wrap_text(line, info_font, info_rect.width - 28)
            fact_lines_wrapped.extend(wrapped)

        # --- TOP-ALIGNED layout ---
        y = info_rect.top + self.twoPanelTextTopPad

        # --- Draw title ---
        for line in name_lines:
            surf = name_font.render(line, True, text_color)
            rect = surf.get_rect(centerx=info_rect.centerx, top=y)
            self.display.blit(surf, rect)
            y = rect.bottom + 8  # more breathing room

        # --- Space before facts ---
        if fact_lines_wrapped:
            y += 6

        # --- Draw facts ---
        for wrapped in fact_lines_wrapped:
            surf = info_font.render(wrapped, True, text_color)
            rect = surf.get_rect(left=info_rect.x + self.twoPanelTextSidePad, top=y)
            self.display.blit(surf, rect)
            y = rect.bottom + 8

        button_cfg = self.current_screen_data.get("button", {})
        button_text = "Next"
        next_screen = None

        if isinstance(button_cfg, dict):
            button_text = str(button_cfg.get("text", "Next"))
            next_screen = button_cfg.get("next")

        #############
        self.draw_rect_button(
            next_rect,
            fill_color=(160, 220, 60),
            border_color=(255, 255, 255),
            text=button_text,
            text_color=(30, 30, 30),
            pulse=True,
        )
        #############
        self.current_buttons.append(
            ButtonSpec(
                text=button_text,
                next_screen=str(next_screen) if next_screen else None,
                rect=next_rect,
            )
        )
        
        # Home button to the right of Next.
        self.draw_round_button(
            home_rect,
            fill_color=panel_fill,
            border_color=panel_border,
            text_color=(30, 30, 30),
            icon_kind="home",
        )
        self.current_buttons.append(ButtonSpec(text="Home", next_screen="main", rect=home_rect))

        if show_code_entry:
            code_text = f"Code: {self.code_buffer}_"
            surf = self.font_footer.render(code_text, True, (255, 255, 255))
            rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 10))
            self.display.blit(surf, rect)

        pygame.display.flip()
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
        icon_name = corner_cfg.get("icon")
        next_screen = corner_cfg.get("next")
        corner = str(corner_cfg.get("corner", "top_right"))

        width = 58
        height = 58
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
        else:
            x = self.screen_width - width - margin
            y = margin

        rect = pygame.Rect(x, y, width, height)

        raw_bg = corner_cfg.get("bg_color", [30, 30, 40, 170])
        raw_border = corner_cfg.get("border_color", [255, 255, 255, 180])
        raw_text = corner_cfg.get("text_color", [255, 255, 255])

        try:
            bg_color = tuple(raw_bg)
        except Exception:
            bg_color = (30, 30, 40, 170)

        try:
            border_color = tuple(raw_border)
        except Exception:
            border_color = (255, 255, 255, 180)

        try:
            text_color = tuple(raw_text)
        except Exception:
            text_color = (255, 255, 255)

        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.circle(overlay, bg_color, (width // 2, height // 2), width // 2 - 1)
        pygame.draw.circle(overlay, border_color, (width // 2, height // 2), width // 2 - 1, 2)
        self.display.blit(overlay, rect.topleft)

        drew_content = False

        if icon_name:
            image_path = self.assets_dir / str(icon_name)
            try:
                img = pygame.image.load(str(image_path)).convert_alpha()
                icon_size = 30
                img = pygame.transform.smoothscale(img, (icon_size, icon_size))
                img_rect = img.get_rect(center=rect.center)
                self.display.blit(img, img_rect)
                drew_content = True
            except Exception as e:
                print(f"Failed to load corner icon {image_path}: {e}")

        if not drew_content and text:
            font = self.fit_font_to_box(text, rect.width - 14, rect.height - 14, self.buttonFontSize, 18)
            surf = font.render(text, True, text_color)
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
        if self.draw_instruction_screen():
            return

        if self.draw_scan_circle_screen():

            if str(self.current_screen_id).startswith("scan:"):
                self.draw_scan_complete_overlay()

            show_code_entry = bool(self.current_screen_data.get("show_code_entry", True))
            if show_code_entry:
                code_text = f"Code: {self.code_buffer}_"
                surf = self.font_footer.render(code_text, True, text_color)
                rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 16))
                self.display.blit(surf, rect)

            pygame.display.flip()
            return

        ##########
        if self.draw_prescription_screen():
            return

        ##########
        if bool(self.current_screen_data.get("fullscreen_image", False)):
            self.current_buttons = []

            image_name = self.current_screen_data.get("image")
            if image_name:
                image_rect = pygame.Rect(0, 0, self.screen_width, self.screen_height)
                self.draw_image_into_rect(str(image_name), image_rect)

            corner_cfg = self.current_screen_data.get("corner_button", {})

            button_d = 70
            margin = 14

            corner = str(corner_cfg.get("corner", "top_left"))

            if corner == "top_left":
                rect = pygame.Rect(margin, margin, button_d, button_d)
            elif corner == "bottom_left":
                rect = pygame.Rect(margin, self.screen_height - button_d - margin, button_d, button_d)
            elif corner == "bottom_right":
                rect = pygame.Rect(self.screen_width - button_d - margin, self.screen_height - button_d - margin, button_d, button_d)
            else:
                rect = pygame.Rect(self.screen_width - button_d - margin, margin, button_d, button_d)

            self.draw_round_button(
                rect,
                fill_color=(214, 236, 247),      # match profile panel
                border_color=(255, 255, 255),
                text_color=(30, 30, 30),
                icon_kind="home",
            )

            self.current_buttons.append(
                ButtonSpec(
                    text="Home",
                    next_screen=str(corner_cfg.get("next", "main")),
                    rect=rect,
                )
            )
            pygame.display.flip()
            return

        if self.draw_animal_detail_screen():
            return

        if self.draw_split_main_screen():
            if str(self.current_screen_id).startswith("scan:"):
                self.draw_scan_complete_overlay()

            show_code_entry = bool(self.current_screen_data.get("show_code_entry", True))
            if show_code_entry:
                code_text = f"Code: {self.code_buffer}_"
                surf = self.font_footer.render(code_text, True, text_color)
                rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 16))
                self.display.blit(surf, rect)

            pygame.display.flip()
            return

        if self.draw_animal_profile_screen():
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
            t = pygame.time.get_ticks() / 1000.0
            self.draw_buttons(buttons_cfg, t=t, animate=False)
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

        small = self.font_debug.render(f"screen: {self.current_screen_id}", True, text_color)
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
        ##########
    def submit_code(self) -> None:
        code = self.code_buffer.strip().upper()
        self.code_buffer = ""

        if not code:
            return

        if code == "DIAG":
            self.go_to_screen("diagnostics")
            return

        if code == "DISP_OFF":
            self.display_off()
            return

        if code == "DISP_ON":
            self.display_on()
            return

        barcode_map = self.current_screen_data.get("barcode_map", {})
        if isinstance(barcode_map, dict):
            next_screen = barcode_map.get(code)
            if next_screen:
                self.go_to_screen(str(next_screen))
                return

        for animal_id, animal in self.animals_data.items():
            if not isinstance(animal, dict):
                continue
            animal_barcode = str(animal.get("barcode", "")).strip().upper()
            if animal_barcode and animal_barcode == code:
                self.go_to_screen(f"animal:{animal_id}")
                return

        print(f"Unknown code: {code}")


    #############
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


    def start_scan_audio(self) -> None:
        audio_path = self.assets_dir / self.scan_audio_file
        if not audio_path.exists():
            return

        try:
            pygame.mixer.music.load(str(audio_path))
            pygame.mixer.music.set_volume(self.scan_audio_volume)
            pygame.mixer.music.play(-1, fade_ms=self.scan_audio_fade_in_ms)
        except Exception as e:
            print(f"Failed to start scan audio {audio_path}: {e}")

    def stop_scan_audio(self) -> None:
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.fadeout(self.scan_audio_fade_out_ms)
        except Exception as e:
            print(f"Failed to stop scan audio: {e}")

    #############################

    def draw_instruction_screen(self) -> bool:
        if not str(self.current_screen_id).startswith("instruction:"):
            return False

        bg_color = (34, 87, 122)
        panel_fill = (200, 225, 240)
        panel_border = (255, 255, 255)
        text_color = (30, 30, 30)

        self.display.fill(bg_color)
        self.current_buttons = []

        image_name = self.current_screen_data.get("instruction_image")
        instruction_text = self.current_screen_data.get(
            "instruction_text",
            "Please put the animal into the MRI",
        )
        show_code_entry = bool(self.current_screen_data.get("show_code_entry", True))

        layout = self.get_two_panel_layout()
        image_rect = layout["image_rect"]
        text_rect = layout["info_rect"]
        scan_rect = layout["action_rect"]
        home_rect = layout["home_rect"]

        for rect in (image_rect, text_rect):
            pygame.draw.rect(self.display, panel_fill, rect, border_radius=self.twoPanelPanelRadius)
            pygame.draw.rect(
                self.display,
                panel_border,
                rect,
                width=self.twoPanelPanelBorderWidth,
                border_radius=self.twoPanelPanelRadius,
            )

        # Left panel image
        inner_image = image_rect.inflate(-self.twoPanelImageInset, -self.twoPanelImageInset)
        self.draw_image_into_rect(str(image_name) if image_name else None, inner_image)

        # Right panel instruction text
        title_font = self.font_title
        body_font = self.font_text

        title = "How to Scan"
        title_surf = title_font.render(title, True, text_color)
        title_rect = title_surf.get_rect(centerx=text_rect.centerx, top=text_rect.top + self.twoPanelTextTopPad)
        self.display.blit(title_surf, title_rect)

        wrapped_lines = self.wrap_text(instruction_text, body_font, text_rect.width - 32)

        y = title_rect.bottom + 24
        for line in wrapped_lines:
            surf = body_font.render(line, True, text_color)
            rect = surf.get_rect(left=text_rect.left + self.twoPanelTextSidePad, top=y)
            self.display.blit(surf, rect)
            y = rect.bottom + 10

        # Scan button
        ##########
        self.draw_rect_button(
            scan_rect,
            fill_color=(160, 220, 60),
            border_color=(255, 255, 255),
            text="Scan",
            text_color=(30, 30, 30),
            pulse=True,
        )
        ##############
        self.current_buttons.append(
            ButtonSpec(
                text="Scan",
                next_screen=str(self.current_screen_data.get("next_scan", "")) or None,
                rect=scan_rect,
            )
        )

        # Home button
        self.draw_round_button(
            home_rect,
            fill_color=panel_fill,
            border_color=panel_border,
            text_color=(30, 30, 30),
            icon_kind="home",
        )
        self.current_buttons.append(
            ButtonSpec(
                text="Home",
                next_screen="main",
                rect=home_rect,
            )
        )

        if show_code_entry:
            code_text = f"Code: {self.code_buffer}_"
            surf = self.font_footer.render(code_text, True, (255, 255, 255))
            rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 10))
            self.display.blit(surf, rect)

        pygame.display.flip()
        return True

    ####################################

    def add_startup_error(self, message: str) -> None:
        message = str(message).strip()
        if not message:
            return
        if message not in self.startup_errors:
            self.startup_errors.append(message)
        print(f"STARTUP ERROR: {message}")
    def play_mri_audio_once(self) -> bool:
        audio_path = self.assets_dir / self.scan_audio_file
        if not audio_path.exists():
            print(f"Audio file missing: {audio_path}")
            return False

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(str(audio_path))
            pygame.mixer.music.set_volume(self.scan_audio_volume)
            pygame.mixer.music.play(0)
            return True
        except Exception as e:
            print(f"Failed to play MRI audio once: {e}")
            return False

    def shutdown_application(self) -> None:
        print("Shutting down application...")

        try:
            self.stop_scan_audio()
        except Exception:
            pass

        try:
            self.gpio.light_off()
        except Exception:
            pass

        try:
            self.gpio.close()
        except Exception:
            pass

        self.running = False

    def restart_pc(self) -> None:
        print("Restarting PC...")
        self.gpio.close()
        os.system("shutdown /r /t 0")

    def display_off(self) -> None:
        print("Turning display OFF...")
        try:
            HWND_BROADCAST = 0xFFFF
            WM_SYSCOMMAND = 0x0112
            SC_MONITORPOWER = 0xF170
            MONITOR_OFF = 2

            ctypes.windll.user32.SendMessageW(
                HWND_BROADCAST,
                WM_SYSCOMMAND,
                SC_MONITORPOWER,
                MONITOR_OFF,
            )
        except Exception as e:
            print(f"Failed to turn display off: {e}")

    def display_on(self) -> None:
        print("Turning display ON...")
        try:
            # Small synthetic mouse movement usually wakes a monitor
            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [
                    ("dx", ctypes.c_long),
                    ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class INPUT(ctypes.Structure):
                class _INPUT(ctypes.Union):
                    _fields_ = [("mi", MOUSEINPUT)]

                _anonymous_ = ("_input",)
                _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]

            INPUT_MOUSE = 0
            MOUSEEVENTF_MOVE = 0x0001

            extra = ctypes.c_ulong(0)

            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi = MOUSEINPUT(
                dx=1,
                dy=0,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE,
                time=0,
                dwExtraInfo=ctypes.pointer(extra),
            )

            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

            # move back so cursor doesn't drift over repeated wakes
            inp2 = INPUT()
            inp2.type = INPUT_MOUSE
            inp2.mi = MOUSEINPUT(
                dx=-1,
                dy=0,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE,
                time=0,
                dwExtraInfo=ctypes.pointer(extra),
            )

            ctypes.windll.user32.SendInput(1, ctypes.byref(inp2), ctypes.sizeof(INPUT))
        except Exception as e:
            print(f"Failed to turn display on: {e}")

    ###########
    def draw_prescription_screen(self) -> bool:
        if not self.current_screen_data.get("prescription_layout", False):
            return False

        text_color = self.get_color("text_color", (30, 30, 30))
        panel_fill = (200, 225, 240)
        panel_border = (255, 255, 255)
        bg_color = self.get_color("bg_color", (34, 87, 122))
        self.display.fill(bg_color)
        self.current_buttons = []

        title_text = self.get_text("title", "Prescription")
        body_text = self.get_text("body", "")
        image_name = self.current_screen_data.get("image")
        show_code_entry = bool(self.current_screen_data.get("show_code_entry", True))

        layout = self.get_two_panel_layout()
        image_rect = layout["image_rect"]
        info_rect = layout["info_rect"]
        home_rect = layout["home_rect"]

        for rect in (image_rect, info_rect):
            pygame.draw.rect(self.display, panel_fill, rect, border_radius=self.twoPanelPanelRadius)
            pygame.draw.rect(
                self.display,
                panel_border,
                rect,
                width=self.twoPanelPanelBorderWidth,
                border_radius=self.twoPanelPanelRadius,
            )

        inner_image = image_rect.inflate(-self.twoPanelImageInset, -self.twoPanelImageInset)
        self.draw_image_into_rect(str(image_name) if image_name else None, inner_image)

        title_font = self.font_title
        body_font = self.font_text

        title_lines = self.wrap_text(title_text, title_font, info_rect.width - 24)
        y = info_rect.top + self.twoPanelTextTopPad

        for line in title_lines:
            surf = title_font.render(line, True, text_color)
            rect = surf.get_rect(centerx=info_rect.centerx, top=y)
            self.display.blit(surf, rect)
            y = rect.bottom + 8

        if body_text.strip():
            y += 6

        for raw_line in body_text.splitlines():
            wrapped_lines = self.wrap_text(raw_line, body_font, info_rect.width - 28)
            if not wrapped_lines:
                y += body_font.get_height() // 2
                continue

            for line in wrapped_lines:
                surf = body_font.render(line, True, text_color)
                rect = surf.get_rect(left=info_rect.left + self.twoPanelTextSidePad, top=y)
                self.display.blit(surf, rect)
                y = rect.bottom + 8

        self.draw_round_button(
            home_rect,
            fill_color=panel_fill,
            border_color=panel_border,
            text_color=(30, 30, 30),
            icon_kind="home",
        )
        self.current_buttons.append(
            ButtonSpec(
                text="Home",
                next_screen="main",
                rect=home_rect,
            )
        )

        if show_code_entry:
            code_text = f"Code: {self.code_buffer}_"
            surf = self.font_footer.render(code_text, True, (255, 255, 255))
            rect = surf.get_rect(midbottom=(self.screen_width // 2, self.screen_height - 10))
            self.display.blit(surf, rect)

        pygame.display.flip()
        return True





    ############

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

        try:
            self.gpio.light_off()
        except Exception:
            pass

        try:
            self.gpio.close()
        except Exception:
            pass

        pygame.quit()
