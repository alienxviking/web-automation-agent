"""The browser tool layer.

This module is a thin, composable wrapper around Playwright that exposes exactly
the capabilities the agent is allowed to use. Each public method maps to one of
the required "tools":

    open_browser        - launch a browser instance
    navigate_to_url     - go to a URL
    take_screenshot     - capture the current viewport
    click_on_screen     - click at pixel coordinates (x, y)
    double_click        - double-click at pixel coordinates (x, y)
    send_keys           - type text / press special keys into the focused element
    scroll              - scroll the page to reveal hidden content

The actions are deliberately coordinate- and keyboard-based (like a human, or a
computer-use agent) rather than CSS selectors, so the "intelligence" of finding
elements lives in the agent, not in brittle hardcoded locators.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from playwright.sync_api import (
    Browser,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from logger import get_logger

log = get_logger()


class BrowserError(RuntimeError):
    """Raised when a browser tool cannot complete its action."""


class BrowserTools:
    """A managed Playwright session exposing the agent's allowed actions."""

    def __init__(
        self,
        headed: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        screenshot_dir: str = "screenshots",
    ) -> None:
        self.headed = headed
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.screenshot_dir = screenshot_dir

        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._shot_count = 0

        os.makedirs(self.screenshot_dir, exist_ok=True)

    # -- context manager so the browser is always cleaned up ------------------

    def __enter__(self) -> "BrowserTools":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise BrowserError("Browser is not open yet. Call open_browser() first.")
        return self._page

    # -- tool: open_browser ---------------------------------------------------

    def open_browser(self) -> str:
        """Launch a Chromium instance and open a single page/tab."""
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=not self.headed)
            context = self._browser.new_context(
                viewport={"width": self.viewport_width, "height": self.viewport_height},
                device_scale_factor=1,  # keep screenshot pixels == click coordinates
            )
            self._page = context.new_page()
            self._page.set_default_timeout(15_000)
            log.info("open_browser -> launched Chromium (headed=%s, %dx%d)",
                     self.headed, self.viewport_width, self.viewport_height)
            return "Browser opened."
        except Exception as exc:  # noqa: BLE001 - surface any launch failure cleanly
            raise BrowserError(f"Failed to open browser: {exc}") from exc

    # -- tool: navigate_to_url ------------------------------------------------

    def navigate_to_url(self, url: str) -> str:
        """Navigate the page to the given URL and wait for it to settle."""
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            # Best-effort wait for network to go quiet; not fatal if it doesn't.
            try:
                self.page.wait_for_load_state("networkidle", timeout=8_000)
            except PlaywrightTimeoutError:
                pass
            time.sleep(0.5)
            log.info("navigate_to_url -> %s", url)
            return f"Navigated to {url}"
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Failed to navigate to {url}: {exc}") from exc

    # -- tool: take_screenshot ------------------------------------------------

    def take_screenshot(self, label: str = "step") -> str:
        """Capture the current viewport to a PNG file and return its path."""
        try:
            self._shot_count += 1
            safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
            path = os.path.join(self.screenshot_dir, f"{self._shot_count:02d}_{safe_label}.png")
            self.page.screenshot(path=path, full_page=False)
            log.info("take_screenshot -> %s", path)
            return path
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Failed to take screenshot: {exc}") from exc

    # -- tool: click_on_screen ------------------------------------------------

    def click_on_screen(self, x: int, y: int) -> str:
        """Move the mouse to (x, y) and perform a single left click."""
        self._guard_coords(x, y)
        try:
            self.page.mouse.move(x, y)
            self.page.mouse.click(x, y)
            time.sleep(0.3)
            log.info("click_on_screen -> (%d, %d)", x, y)
            return f"Clicked at ({x}, {y})"
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Failed to click at ({x}, {y}): {exc}") from exc

    # -- tool: double_click ---------------------------------------------------

    def double_click(self, x: int, y: int) -> str:
        """Double-click at (x, y) — useful for selecting existing text in a field."""
        self._guard_coords(x, y)
        try:
            self.page.mouse.move(x, y)
            self.page.mouse.dblclick(x, y)
            time.sleep(0.3)
            log.info("double_click -> (%d, %d)", x, y)
            return f"Double-clicked at ({x}, {y})"
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Failed to double-click at ({x}, {y}): {exc}") from exc

    # -- tool: send_keys ------------------------------------------------------

    def send_keys(self, text: str = "", press: str = "", clear_first: bool = False) -> str:
        """Type ``text`` into the currently focused element, or press a key.

        - ``text``        free text typed character by character.
        - ``press``       a special key chord, e.g. "Enter", "Tab", "Control+A".
        - ``clear_first`` select-all + delete before typing (clears the field).
        """
        try:
            if clear_first:
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Delete")
            if text:
                self.page.keyboard.type(text, delay=20)
            if press:
                self.page.keyboard.press(press)
            time.sleep(0.2)
            log.info("send_keys -> text=%r press=%r clear_first=%s", text, press, clear_first)
            return f"Sent keys (text={text!r}, press={press!r})"
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Failed to send keys: {exc}") from exc

    # -- tool: scroll ---------------------------------------------------------

    def scroll(self, dy: int = 400, dx: int = 0) -> str:
        """Scroll the page by (dx, dy) pixels. Positive dy scrolls down."""
        try:
            self.page.mouse.wheel(dx, dy)
            time.sleep(0.4)
            log.info("scroll -> dx=%d dy=%d", dx, dy)
            return f"Scrolled by ({dx}, {dy})"
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Failed to scroll: {exc}") from exc

    # -- helpers --------------------------------------------------------------

    def _guard_coords(self, x: int, y: int) -> None:
        if not (0 <= x <= self.viewport_width and 0 <= y <= self.viewport_height):
            raise BrowserError(
                f"Coordinates ({x}, {y}) are outside the {self.viewport_width}x"
                f"{self.viewport_height} viewport."
            )

    def close(self) -> None:
        """Tear down the page, browser, and Playwright session safely."""
        for closer in (
            lambda: self._browser.close() if self._browser else None,
            lambda: self._pw.stop() if self._pw else None,
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001 - cleanup must never raise
                pass
        self._page = None
        self._browser = None
        self._pw = None
        log.info("Browser closed.")
