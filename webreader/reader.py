"""
Core reader: connect to browser → render page → extract clean content.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    from playwright.async_api import async_playwright, Browser, Page, Playwright
except ImportError:
    raise ImportError(
        "Playwright not installed. Run: pip install playwright && playwright install chromium"
    )

from .extractor import html_to_markdown


@dataclass
class ReadResult:
    """Result of reading a webpage."""

    url: str
    title: str = ""
    markdown: str = ""
    text: str = ""
    html: str = ""
    status_code: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "markdown": self.markdown,
            "text": self.text,
            "status_code": self.status_code,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "error": self.error,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        if self.error:
            return f"ReadResult(url={self.url!r}, error={self.error!r})"
        return (
            f"ReadResult(url={self.url!r}, title={self.title!r}, "
            f"len(markdown)={len(self.markdown)}, "
            f"elapsed={self.elapsed_seconds:.1f}s)"
        )


class WebReader:
    """
    Main reader class. Connects to a real browser via CDP or launches one.
    
    Supports two modes:
    1. CDP mode: Connect to existing browser with --remote-debugging-port (preserves login state)
    2. Launch mode: Launch a fresh browser (no login state, but works everywhere)
    """

    def __init__(
        self,
        cdp_url: Optional[str] = None,
        headless: bool = True,
        timeout: int = 120_000,
        user_agent: Optional[str] = None,
    ):
        self.cdp_url = cdp_url
        self.headless = headless
        self.timeout = timeout
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        )
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    async def _get_browser(self) -> Browser:
        """Get or create browser connection."""
        if self._browser and self._browser.is_connected():
            return self._browser

        self._pw = await async_playwright().start()

        if self.cdp_url:
            # Connect to existing browser (preserves login state!)
            self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
        else:
            # Launch fresh browser
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-web-security",  # for cross-origin in same tab context only
                ],
            )

        return self._browser

    async def read(
        self,
        url: str,
        *,
        wait_for: Optional[str] = None,
        wait_timeout: int = 30_000,
        extract_links: bool = False,
        remove_scripts: bool = True,
        screenshot_path: Optional[str] = None,
    ) -> ReadResult:
        """
        Read a webpage and return clean content.

        Args:
            url: The URL to read.
            wait_for: CSS selector or text to wait for before extracting.
            wait_timeout: Max ms to wait for the selector/text.
            extract_links: Whether to include link URLs in output.
            remove_scripts: Remove <script> tags from extracted HTML.
            screenshot_path: If set, save screenshot to this path.

        Returns:
            ReadResult with markdown, text, and metadata.
        """
        start = time.monotonic()
        result = ReadResult(url=url)

        try:
            browser = await self._get_browser()
            
            # Create a new page context (inherits cookies from browser in CDP mode)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

            # Navigate
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.timeout,
            )

            # Wait for dynamic content
            if wait_for:
                try:
                    if wait_for.startswith("#"):
                        # Wait for text content containing this string
                        await page.wait_for_function(
                            f"document.body.innerText.includes({wait_for[1:]!r})",
                            timeout=wait_timeout,
                        )
                    else:
                        # Wait for CSS selector
                        await page.wait_for_selector(wait_for, timeout=wait_timeout)
                except Exception:
                    pass  # Continue anyway after timeout

            # Extra wait for JS rendering (like Reddit's dynamic content)
            await page.wait_for_timeout(3000)

            result.status_code = response.status if response else 0
            result.title = await page.title()

            # Extract HTML
            html_content = await page.content()

            if remove_scripts:
                # Use JS to remove script/style tags for cleaner extraction
                html_content = await page.evaluate("""() => {
                    const clone = document.documentElement.cloneNode(true);
                    clone.querySelectorAll('script, style, noscript, svg').forEach(el => el.remove());
                    // Remove common clutter
                    clone.querySelectorAll('[aria-hidden="true"], nav footer header [role="navigation"]').forEach(el => el.remove());
                    return clone.outerHTML;
                }""")

            # Convert to Markdown
            result.markdown = html_to_markdown(html_content, url=url)
            result.text = await page.evaluate("() => document.body.innerText")

            # Metadata
            result.metadata.update({
                "final_url": page.url,
                "links_count": len(await page.query_selector_all("a[href]")) if extract_links else 0,
                "images_count": len(await page.query_selector_all("img")),
            })

            if extract_links:
                links = await page.evaluate("""() => {
                    return [...document.querySelectorAll('a[href]')].map(a => ({
                        text: a.innerText.trim(),
                        href: a.href
                    })).filter(l => l.text);
                }""")
                result.metadata["links"] = links[:100]  # Cap at 100 links

            # Screenshot
            if screenshot_path:
                await page.screenshot(path=screenshot_path, full_page=True)
                result.metadata["screenshot"] = screenshot_path

            await page.close()

        except Exception as e:
            result.error = str(e)

        result.elapsed_seconds = time.monotonic() - start
        return result

    async def read_multiple(
        self,
        urls: list[str],
        *,
        concurrency: int = 3,
        **kwargs,
    ) -> list[ReadResult]:
        """Read multiple URLs concurrently."""
        semaphore = asyncio.Semaphore(concurrency)

        async def _read_one(url: str) -> ReadResult:
            async with semaphore:
                return await self.read(url, **kwargs)

        tasks = [_read_one(url) for url in urls]
        return await asyncio.gather(*tasks)

    async def close(self):
        """Clean up resources."""
        if self._browser and self._browser.is_connected():
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


# ─── Convenience: sync wrapper ──────────────────────────────────────

def read_sync(url: str, cdp_url: Optional[str] = None, **kwargs) -> ReadResult:
    """Synchronous convenience wrapper."""
    return asyncio.run(_read_sync_impl(url, cdp_url=cdp_url, **kwargs))


async def _read_sync_impl(url: str, **kwargs) -> ReadResult:
    async with WebReader(**{k: v for k, v in kwargs.items() if k != "url"}) as reader:
        return await reader.read(url)
