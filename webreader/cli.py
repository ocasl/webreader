"""
CLI entry point: webreader command-line interface.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import click
from rich.console import Console
from rich.markdown import Markdown as RichMarkdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.syntax import Syntax

from . import __version__
from .reader import WebReader, ReadResult, read_sync
from .browser import (
    discover_browsers,
    launch_edge_with_cdp,
    find_edge_executable,
    format_device_table,
    check_cdp_available,
)
from .extractor import extract_structured_data

console = Console()


def print_version(ctx, param, value):
    if not value:
        return
    console.print(f"[bold green]webreader[/bold green] v{__version__}")
    console.print("Read the web through your own browser.")
    ctx.exit()


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, callback=print_version, expose_value=False, is_eager=True)
@click.pass_context
def main(ctx):
    """[bold green]webreader[/bold green] — Read the web through your own browser.
    
    Open-source dokobot alternative. Uses your real browser's login sessions
    to read pages behind login walls (Reddit, X/Twitter, etc.)
    
    [dim]Examples:[/dim]
      webreader read https://www.reddit.com/r/Xiaohongshu/hot/
      webreader read https://x.com/search?q=AI --device local-9222 --timeout 150
      webreader list
      webreader launch
    """
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@main.command()
@click.argument("url")
@click.option("--device", "-d", default=None, help="Device ID or CDP URL (auto-detect if omitted).")
@click.option("--timeout", "-t", type=int, default=120, help="Timeout in seconds (default: 120).")
@click.option("--output", "-o", type=click.Path(), default=None, help="Save output to file.")
@click.option("--format", "fmt", type=click.Choice(["text", "markdown", "json"]), default="markdown",
              help="Output format (default: markdown).")
@click.option("--screenshot", type=click.Path(), default=None, help="Save screenshot of page.")
@click.option("--wait-for", "-w", default=None, help="CSS selector or #text to wait for.")
@click.option("--no-color", is_flag=True, help="Disable colored output.")
@click.option("--links/--no-links", default=False, help="Include extracted links in output.")
def read(url, device, timeout, output, fmt, screenshot, wait_for, no_color, links):
    """Read a webpage and print its content."""
    
    # Determine CDP URL
    cdp_url = None
    
    if device:
        # Could be a direct URL like http://localhost:9222
        if device.startswith("http"):
            cdp_url = device
        else:
            # Try to resolve device ID
            devices = discover_browsers()
            for d in devices:
                if d.id.startswith(device) or device in d.name.lower():
                    cdp_url = d.cdp_url
                    console.print(f"[dim]Using device: {d.name}[/dim]")
                    break
            
            if not cdp_url and device.isdigit():
                # Treat as port number
                port = int(device)
                if check_cdp_available(port):
                    cdp_url = f"http://localhost:{port}"
    
    if not cdp_url:
        # Auto-discover
        devices = discover_browsers()
        if devices:
            cdp_url = devices[0].cdp_url
            console.print(f"[dim]Auto-detected browser: {devices[0].name}[/dim]")
        else:
            console.print("[yellow]⚠ No browser with CDP found.[/yellow]")
            console.print("[dim]Try: webreader launch  (starts Edge with remote debugging)[/dim]")
            console.print("[dim]Or start Edge manually: msedge --remote-debugging-port=9222[/dim]")
            
            # Offer to auto-launch
            if click.confirm("\nAuto-launch Edge with debugging?", default=True):
                proc, url = launch_edge_with_cdp()
                if url:
                    cdp_url = url
    
    if not cdp_url:
        console.print("[red]❌ No browser available. Cannot read page.[/red]")
        sys.exit(1)

    # Read the page
    async def _do_read():
        async with WebReader(
            cdp_url=cdp_url,
            headless=False,
            timeout=timeout * 1000,
        ) as reader:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task(f"Reading {url[:60]}...", total=None)

                result = await reader.read(
                    url,
                    wait_for=wait_for,
                    extract_links=links,
                    screenshot_path=screenshot,
                )
                progress.update(task, completed=True)

            return result

    result = asyncio.run(_do_read())

    # Output
    if result.error:
        console.print(f"\n[red]❌ Error: {result.error}[/red]")
        sys.exit(1)

    if fmt == "json":
        output_data = result.to_dict()
        output_data["structured"] = extract_structured_data(result.markdown, url=url)
        text = json.dumps(output_data, ensure_ascii=False, indent=2)
    elif fmt == "text":
        text = result.text
    else:
        text = result.markdown

    # Print or save
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        console.print(f"[green]✅ Saved to {output}[/green] ({len(text):,} chars)")
    elif no_color or fmt == "json" or fmt == "text":
        console.print(text)
    else:
        # Rich markdown rendering
        console.print()
        console.print(Panel(
            f"[bold]{result.title or 'No title'}[/bold]\n[dim]{result.metadata.get('final_url', url)}[/dim]",
            title=f"📄 Page ({len(result.markdown):,} chars, {result.elapsed_seconds:.1f}s)",
            border_style="green",
        ))
        console.print(RichMarkdown(text))


@main.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="JSON format.")
def list_cmd(as_json):
    """List available browser devices."""
    devices = discover_browsers()

    if as_json:
        console.print(json.dumps([d.to_dict() for d in devices], indent=2))
    else:
        table = Table(title="🌐 Browser Devices", border_style="blue")
        table.add_column("ID", style="cyan", max_width=34)
        table.add_column("Name", style="white")
        table.add_column("Type", style="magenta")
        table.add_column("CDP Port", style="yellow")
        table.add_column("Status")

        for d in devices:
            port = d.cdp_url.rsplit(":", 1)[-1] if ":" in d.cdp_url else "?"
            status_style = "green" if d.status == "active" else "red"
            table.add_row(d.id[:34], d.name, d.browser_type, port, f"[{status_style}]{d.status}[/{status_style}]")

        if not devices:
            console.print("[yellow]No browsers detected.[/yellow]")
            console.print("\n[dim]Start a browser with remote debugging:[/dim]")
            console.print("  Edge:   msedge --remote-debugging-port=9222")
            console.print("  Chrome: chrome --remote-debugging-port=9222")
            console.print("\n[dim]Or use: webreader launch[/dim]")
        else:
            console.print(table)


@main.command()
@click.option("--port", "-p", type=int, default=9222, help="CDP port (default: 9222).")
@click.option("--profile", default=None, help="Custom user data directory path.")
@click.option("--headless", is_flag=True, help="Launch in headless mode (no visible window).")
def launch(port, profile, headless):
    """Launch Edge with remote debugging enabled."""
    edge_path = find_edge_executable()
    if not edge_path:
        console.print("[red]Microsoft Edge not installed![/red]")
        console.print("[dim]Download: https://www.microsoft.com/edge[/dim]")
        sys.exit(1)

    console.print(f"[dim]Edge path: {edge_path}[/dim]")
    proc, cdp_url = launch_edge_with_cdp(
        port=port,
        user_data_dir=profile,
        headless=headless,
    )

    if cdp_url:
        info = ""
        try:
            from .browser import get_cdp_browser_info
            bi = get_cdp_browser_info(port)
            if bi:
                info = f" v{bi.get('Browser', '').split('/')[-1]}"
        except Exception:
            pass
        
        console.print(f"\n[green]✅ Browser ready at {cdp_url}{info}[/green]")
        console.print("[dim]Press Ctrl+C to stop the browser.[/dim]")
        
        try:
            # Keep running until interrupted
            proc.wait()
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping browser...[/yellow]")
            proc.terminate()


@main.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--device", "-d", default=None, help="Device ID or CDP URL.")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Directory to save outputs.")
@click.option("--concurrency", "-c", type=int, default=3, help="Max concurrent reads (default: 3).")
def batch(urls, device, output_dir, concurrency):
    """Read multiple URLs in batch."""
    
    cdp_url = None
    if device and device.startswith("http"):
        cdp_url = device
    else:
        devices = discover_browsers()
        if devices:
            cdp_url = devices[0].cdp_url
        else:
            console.print("[red]❌ No browser available. Run: webreader launch[/red]")
            sys.exit(1)

    async def _do_batch():
        async with WebReader(cdp_url=cdp_url) as reader:
            results = await reader.read_multiple(list(urls), concurrency=concurrency)
            return results

    results = asyncio.run(_do_batch())

    # Summary table
    table = Table(title="📊 Batch Results", border_style="blue")
    table.add_column("#", style="cyan")
    table.add_column("URL", max_width=50, style="white")
    table.add_column("Title", max_width=30)
    table.add_column("Size", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Status")

    for i, r in enumerate(results, 1):
        status = "[green]✅[/green]" if not r.error else f"[red]❌ {r.error[:30]}[/red]"
        size = f"{len(r.markdown):,}"
        table.add_row(str(i), r.url[:50], r.title[:30], size, f"{r.elapsed_seconds:.1f}s", status)

        if output_dir:
            import os
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', r.url.split("//")[1].split("/")[0]) if "//" in r.url else f"page_{i}"
            out_path = os.path.join(output_dir, f"{safe_name}.md")
            os.makedirs(output_dir, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(r.markdown)
            r.metadata["saved_to"] = out_path

    console.print(table)

    total_chars = sum(len(r.markdown) for r in results if not r.error)
    ok = sum(1 for r in results if not r.error)
    console.print(f"\n[green]{ok}/{len(results)}[/green] pages read successfully, "
                  f"[bold]{total_chars:,}[/bold] total characters.")


@main.command()
def status():
    """Show system status and diagnostics."""
    table = Table(title="🔧 System Status", border_style="blue")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")

    # Python version
    import sys
    table.add_row("Python", f"[green]{sys.version.split()[0]}[/green]", platform.platform())

    # Playwright
    try:
        from playwright.__version__ import __version__ as pw_ver
        table.add_row("Playwright", f"[green]v{pw_ver}[/green]", "Installed")
    except ImportError:
        table.add_row("Playwright", "[red]Not installed[/red]", "Run: pip install playwright && playwright install chromium")

    # markdownify
    try:
        from markdownify import __version__ as mf_ver
        table.add_row("markdownify", f"[green]v{mf_ver}[/green]", "Installed")
    except ImportError:
        table.add_row("markdownify", "[yellow]Optional[/yellow]", "Will use fallback converter")

    # Edge
    edge_path = find_edge_executable()
    if edge_path:
        table.add_row("Edge", f"[green]Found[/green]", edge_path)
    else:
        table.add_row("Edge", "[red]Not found[/red]", "Please install Microsoft Edge")

    # CDP ports
    active_ports = []
    for p in DEFAULT_CDP_PORTS if 'DEFAULT_CDP_PORTS' in dir() else [9222]:
        if check_cdp_available(p):
            active_ports.append(p)
    
    if active_ports:
        ports_str = ", ".join(f"[green]{p}[/green]" for p in active_ports)
        table.add_row("CDP Ports", f"[green]Active: {ports_str}[/green]", "")
    else:
        table.add_row("CDP Ports", "[yellow]None active[/yellow]", "Run: webreader launch")

    console.print(table)


# Import here to avoid circular dependency
DEFAULT_CDP_PORTS = [9222, 9229, 9333, 9220]


if __name__ == "__main__":
    main()
