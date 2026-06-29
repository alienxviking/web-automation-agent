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
        browser_channel: str = "auto",
    ) -> None:
        self.headed = headed
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.screenshot_dir = screenshot_dir
        self.browser_channel = (browser_channel or "auto").lower()

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

    def _channels_to_try(self) -> list:
        """Decide which browser channels to attempt, in order.

        "auto" prefers the user's real installed Chrome, then Edge, then the
        bundled Chromium. A specific channel ("chrome"/"msedge") still falls back
        to bundled Chromium if that browser isn't installed. "" / "chromium" uses
        only the bundled engine.
        """
        if self.browser_channel in ("", "chromium", "bundled"):
            return [None]
        if self.browser_channel == "auto":
            return ["chrome", "msedge", None]
        return [self.browser_channel, None]

    def open_browser(self) -> str:
        """Launch a browser and open a single page/tab.

        Prefers the user's installed Chrome/Edge so real sites (e.g. YouTube)
        behave exactly as they do for a normal user, and opens a visible window
        when ``headed`` is true.
        """
        try:
            self._pw = sync_playwright().start()
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Failed to start Playwright: {exc}") from exc

        last_exc = None
        for channel in self._channels_to_try():
            try:
                kwargs = {"headless": not self.headed}
                if channel:
                    kwargs["channel"] = channel
                self._browser = self._pw.chromium.launch(**kwargs)
                context = self._browser.new_context(
                    viewport={"width": self.viewport_width, "height": self.viewport_height},
                    device_scale_factor=1,  # keep screenshot pixels == click coordinates
                )
                self._page = context.new_page()
                self._page.set_default_timeout(15_000)
                used = channel or "chromium (bundled)"
                log.info("open_browser -> launched %s (headed=%s, %dx%d)",
                         used, self.headed, self.viewport_width, self.viewport_height)
                return f"Browser opened ({used})."
            except Exception as exc:  # noqa: BLE001 - try the next channel
                last_exc = exc
                log.warning("Could not launch '%s' (%s) — trying next option.",
                            channel or "bundled chromium", exc)

        raise BrowserError(f"Failed to open any browser. Last error: {last_exc}")

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
            self._settle()
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
                self._settle()  # Enter may submit a search / navigate
            time.sleep(0.2)
            log.info("send_keys -> text=%r press=%r clear_first=%s", text, press, clear_first)
            return f"Sent keys (text={text!r}, press={press!r})"
        except Exception as exc:  # noqa: BLE001
            raise BrowserError(f"Failed to send keys: {exc}") from exc

    # -- perception helper: list interactive elements ------------------------

    _ELEMENTS_JS = """
    () => {
      const selector = 'a,button,input,textarea,select,[role=button],[role=link],' +
        '[role=textbox],[role=searchbox],[role=combobox],[role=menuitem],[role=tab],' +
        '[contenteditable=""],[contenteditable=true],[onclick]';
      const seen = new Set();
      const out = [];
      for (const el of document.querySelectorAll(selector)) {
        const r = el.getBoundingClientRect();
        if (r.width < 6 || r.height < 6) continue;
        // keep only elements whose centre is inside the viewport
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) continue;
        const st = window.getComputedStyle(el);
        if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
        let label = (el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
          (el.innerText || '').trim() || el.value || el.getAttribute('title') ||
          el.getAttribute('name') || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        const key = tag + '|' + type + '|' + label + '|' + Math.round(cx) + ',' + Math.round(cy);
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ tag, type, label, x: Math.round(cx), y: Math.round(cy) });
      }
      return out;
    }
    """

    def get_interactive_elements(self, limit: int = 45) -> list:
        """Return visible, in-viewport interactive elements with real centres.

        Each item is {tag, type, label, x, y}. Giving these to the model — with
        true click coordinates — makes element selection far more reliable than
        asking it to guess pixels from the screenshot alone.
        """
        try:
            elements = self.page.evaluate(self._ELEMENTS_JS)
        except Exception as exc:  # noqa: BLE001 - perception must never crash the loop
            log.warning("Could not read interactive elements: %s", exc)
            return []
        return elements[:limit]

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

    def _settle(self) -> None:
        """Best-effort wait for the page to finish reacting to an action."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=4_000)
        except PlaywrightTimeoutError:
            pass

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
