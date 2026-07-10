"""
CLI entry point: webreader command-line interface v2

Two modes:
  - CDP mode (default): Connect via Playwright --remote-debugging-port (legacy)
  - Extension mode (--mode ext): Via browser extension + Native Host HTTP bridge
    No --remote-debugging-port needed! Just needs Edge running with extension installed.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import re
from pathlib import Path

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


# ─── Extension Mode: HTTP → Host → Extension → Debugger ─────────

def check_extension_host_running() -> bool:
    """Check if the webreader native host + HTTP server is running."""
    try:
        import urllib.request
        req = urllib.request.urlopen("http://127.0.0.1:18789/health", timeout=2)
        data = json.loads(req.read().decode())
        return data.get('extension_connected', False)
    except Exception:
        return False


def read_via_extension(url: str, timeout: int = 120, **opts) -> dict:
    """
    Read a URL through the extension mode pipeline.
    
    Flow: CLI → HTTP POST localhost:18789/read → host.py 
          → NM port → background.js → chrome.debugger → page content
          → NM response → host.py → HTTP response → CLI
    """
    import urllib.request
    import urllib.error
    
    payload = json.dumps({
        "url": url,
        "options": {
            "timeout": timeout,
            **{k: v for k, v in opts.items() if v is not None},
        }
    }).encode('utf-8')
    
    req = urllib.request.Request(
        "http://127.0.0.1:18789/read",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    
    start = time.monotonic()
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            result['elapsed_seconds'] = time.monotonic() - start
            return result
    except urllib.error.URLError as e:
        return {
            "type": "error",
            "message": f"Cannot reach webreader host at localhost:18789. Is Edge running with the webreader extension?",
            "hint": "Start Edge normally (no special args needed), ensure webreader extension is loaded.",
            "elapsed_seconds": time.monotonic() - start,
        }
    except Exception as e:
        return {
            "type": "error",
            "message": str(e),
            "elapsed_seconds": time.monotonic() - start,
        }


# ─── Version ────────────────────────────────────────────────────

def print_version(ctx, param, value):
    if not value:
        return
    console.print(f"[bold green]webreader[/bold green] v{__version__}")
    console.print("Read the web through your own browser.")
    ctx.exit()


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, callback=print_version, expose_value=False, is_eager=True)
@click.option("--mode", "-m", type=click.Choice(["cdp", "ext", "auto"]), default="auto",
              help="Mode: cdp=Playwright(CDP), ext=Extension(Debugger), auto=detect best")
@click.pass_context
def main(ctx, mode):
    """[bold green]webreader[/bold green] — Read the web through your own browser.
    
    Open-source dokobot alternative. Uses your real browser's login sessions
    to read pages behind login walls (Reddit, X/Twitter, etc.)
    
    [dim]Modes:[/dim]
      [cyan]ext[/cyan] (recommended): Uses browser extension + chrome.debugger. No --remote-debugging-port!
      [cyan]cdp[/cyan]: Legacy Playwright CDP connection. Requires --remote-debugging-port or `webreader launch`
      [cyan]auto[/cyan]: Try ext first, fall back to cdp
    
    [dim]Examples:[/dim]
      webreader read https://www.reddit.com/r/Xiaohongshu/hot/          [dim](auto mode)[/dim]
      webreader read https://x.com/search?q=AI -m ext                  [dim](force extension)[/dim]
      webreader list                                                    [dim](show browsers)[/dim]
      webreader launch                                                  [dim](start Edge+CDP)[/dim]
    """
    # Store mode for subcommands to access
    ctx.ensure_object(dict)
    ctx.obj['mode'] = mode
    
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


@main.command()
@click.argument("url")
@click.option("--device", "-d", default=None, help="Device ID or CDP URL (CDP mode only).")
@click.option("--timeout", "-t", type=int, default=120, help="Timeout in seconds (default: 120).")
@click.option("--output", "-o", type=click.Path(), default=None, help="Save output to file.")
@click.option("--format", "fmt", type=click.Choice(["text", "markdown", "json"]), default="markdown",
              help="Output format (default: markdown).")
@click.option("--screenshot", type=click.Path(), default=None, help="Save screenshot of page.")
@click.option("--wait-for", "-w", default=None, help="CSS selector or #text to wait for.")
@click.option("--no-color", is_flag=True, help="Disable colored output.")
@click.option("--links/--no-links", default=False, help="Include extracted links in output.")
@click.pass_context
def read(ctx, url, device, timeout, output, fmt, screenshot, wait_for, no_color, links):
    """Read a webpage and print its content."""
    
    mode = ctx.obj.get('mode', 'auto') or 'auto'
    
    # Auto-detect: prefer extension mode if host is running
    if mode == 'auto':
        if check_extension_host_running():
            mode = 'ext'
            console.print("[dim]Using extension mode (chrome.debugger)[/dim]")
        else:
            mode = 'cdp'
            console.print("[dim]Using CDP mode (Playwright)[/dim]")
    
    # ═══ Extension Mode ═══
    if mode == 'ext':
        result = read_via_extension(url, timeout=timeout)
        
        if result.get('error') and not result.get('markdown'):
            console.print(f"\n[red]❌ Error: {result.get('message') or result['error']}[/red]")
            
            hint = result.get('hint', '')
            if hint:
                console.print(f"[yellow]{hint}[/yellow]")
            
            sys.exit(1)
        
        title = result.get('title', '')
        markdown = result.get('markdown', '')
        text = result.get('text', '')
        elapsed = result.get('elapsed_seconds', 0)
        
        # Output formatting
        if fmt == "json":
            output_data = {
                "url": url,
                "title": title,
                "markdown": markdown,
                "text": text,
                "source": result.get('source', ''),
                "elapsed_seconds": round(elapsed, 2),
                "structured": extract_structured_data(markdown, url=url) if markdown else {},
                "error": result.get('error'),
            }
            text_out = json.dumps(output_data, ensure_ascii=False, indent=2)
        elif fmt == "text":
            text_out = text
        else:
            text_out = markdown
        
        if output:
            Path(output).write_text(text_out, encoding='utf-8')
            console.print(f"[green]✅ Saved to {output}[/green] ({len(text_out):,} chars)")
        elif no_color or fmt in ('json', 'text'):
            console.print(text_out)
        else:
            console.print()
            console.print(Panel(
                f"[bold]{title or 'No title'}[/bold]\n[dim]{url}[/dim]",
                title=f"📄 Page ({len(markdown):,} chars, {elapsed:.1f}s, source={result.get('source','?')})",
                border_style="green",
            ))
            console.print(RichMarkdown(text_out))
        
        return
    
    # ═══ CDP Mode (legacy) ═══
    _read_cdp_mode(url, device, timeout, output, fmt, screenshot, wait_for, no_color, links)


def _read_cdp_mode(url, device, timeout, output, fmt, screenshot, wait_for, no_color, links):
    """Original Playwright CDP-based reading."""
    
    # Determine CDP URL
    cdp_url = None
    
    if device:
        if device.startswith("http"):
            cdp_url = device
        else:
            devices = discover_browsers()
            for d in devices:
                if d.id.startswith(device) or device in d.name.lower():
                    cdp_url = d.cdp_url
                    console.print(f"[dim]Using device: {d.name}[/dim]")
                    break
            
            if not cdp_url and device.isdigit():
                port = int(device)
                if check_cdp_available(port):
                    cdp_url = f"http://localhost:{port}"
    
    if not cdp_url:
        devices = discover_browsers()
        if devices:
            cdp_url = devices[0].cdp_url
            console.print(f"[dim]Auto-detected browser: {devices[0].name}[/dim]")
        else:
            console.print("[yellow]⚠ No browser with CDP found.[/yellow]")
            console.print("[dim]Try: webreader launch  (starts Edge with remote debugging)[/dim]")
            console.print("[dim]Or use: webreader read <url> -m ext  (extension mode, no CDP needed!)[/dim]")
            
            if click.confirm("\nAuto-launch Edge with debugging?", default=True):
                proc, url = launch_edge_with_cdp()
                if url:
                    cdp_url = url
    
    if not cdp_url:
        console.print("[red]❌ No browser available. Cannot read page.[/red]")
        console.print("[yellow]💡 Tip: Use '-m ext' for extension mode (no --remote-debugging-port)[/yellow]")
        sys.exit(1)

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

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
        console.print(f"[green]✅ Saved to {output}[/green] ({len(text):,} chars)")
    elif no_color or fmt == "json" or fmt == "text":
        console.print(text)
    else:
        console.print()
        console.print(Panel(
            f"[bold]{result.title or 'No title'}[/bold]\n[dim]{result.metadata.get('final_url', url)}[/dim]",
            title=f"📄 Page ({len(result.markdown):,} chars, {result.elapsed_seconds:.1f}s)",
            border_style="green",
        ))
        console.print(RichMarkdown(text))


@main.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="JSON format.")
@click.pass_context
def list_cmd(ctx, as_json):
    """List available browser devices / extension status."""
    mode = ctx.obj.get('mode', 'auto') or 'auto'
    
    # Show extension status
    ext_running = check_extension_host_running()
    
    table = Table(title="🌐 WebReader Status", border_style="blue")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")
    
    # Extension mode status
    ext_status = "[green]✅ Connected[/green]" if ext_running else "[yellow]⚠ Not running[/yellow]"
    ext_detail = "localhost:18789 — chrome.debugger mode (no CDP)" if ext_running else "Start Edge + reload extension"
    table.add_row("Extension Bridge", ext_status, ext_detail)
    
    # CDP mode devices
    devices = discover_browsers()
    if devices:
        for d in devices:
            port = d.cdp_url.rsplit(":", 1)[-1] if ":" in d.cdp_url else "?"
            table.add_row(f"  CDP: {d.name}", f"[green]{d.status}[/green]", f"port {port}")
    else:
        table.add_row("  CDP Browser", "[yellow]None active[/yellow]", "Run: webreader launch")
    
    console.print(table)
    
    if not ext_running and not devices:
        console.print("\n[dim]To use extension mode (recommended):[/dim]")
        console.print("  1. Open Edge normally (ensure webreader extension is loaded)")
        console.print("  2. The extension will auto-connect to the native host")
        console.print("  3. Run: webreader read <url> -m ext")


@main.command()
@click.option("--port", "-p", type=int, default=9222, help="CDP port (default: 9222).")
@click.option("--profile", default=None, help="Custom user data directory path.")
@click.option("--headless", is_flag=True, help="Launch in headless mode (no visible window).")
def launch(port, profile, headless):
    """Launch Edge with remote debugging enabled (CDP mode only)."""
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
            proc.wait()
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping browser...[/yellow]")
            proc.terminate()


@main.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--mode", "-m", "cmd_mode", type=click.Choice(["cdp", "ext"]), default=None,
              help="Force mode for batch reads.")
@click.option("--output-dir", "-o", type=click.Path(), default=None, help="Directory to save outputs.")
@click.option("--concurrency", "-c", type=int, default=3, help="Max concurrent reads (default: 3).")
@click.pass_context
def batch(ctx, urls, cmd_mode, output_dir, concurrency):
    """Read multiple URLs in batch."""
    mode = cmd_mode or ctx.obj.get('mode', 'auto') or 'auto'
    
    if mode != 'cdp' and check_extension_host_running():
        mode = 'ext'
    
    if mode == 'ext':
        # Extension mode batch: sequential (debugger can only attach one-at-a-time per tab)
        results = []
        for url in urls:
            console.print(f"\n[cyan]▶ Reading: {url[:60]}...[/cyan]")
            r = read_via_extension(url, timeout=120)
            results.append(r)
            status = "[green]✅[/green]" if not r.get('error') and r.get('markdown') else f"[red]❌[/red]"
            console.print(f"  {status} {(r.get('markdown') or '')[:60]}...")
        
        # Summary
        table = Table(title="📊 Batch Results (extension mode)", border_style="blue")
        table.add_column("#", style="cyan")
        table.add_column("URL", max_width=50, style="white")
        table.add_column("Size", justify="right")
        table.add_column("Time", justify="right")
        table.add_column("Status")

        ok = 0
        for i, r in enumerate(results, 1):
            md = r.get('markdown', '') or ''
            err = r.get('error', '')
            size = f"{len(md):,}"
            t = f"{r.get('elapsed_seconds', 0):.1f}s"
            st = "[green]✅[/green]" if md and not err else f"[red]❌ {err[:30]}[/red]"
            if md and not err: ok += 1
            table.add_row(str(i), urls[i][:50], size, t, st)

        console.print(table)
        total_chars = sum(len((r.get('markdown') or '')) for r in results if not r.get('error'))
        console.print(f"\n[green]{ok}/{len(results)}[/green] pages read successfully, "
                      f"[bold]{total_chars:,}[/bold] total characters.")
        return
    
    # CDP mode batch (original logic)
    cdp_url = None
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
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', r.url.split("//")[1].split("/")[0]) if "//" in r.url else f"page_{i}"
            out_path = Path(output_dir) / f"{safe_name}.md"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(r.markdown, encoding='utf-8')
            r.metadata["saved_to"] = str(out_path)

    console.print(table)

    total_chars = sum(len(r.markdown) for r in results if not r.error)
    ok_count = sum(1 for r in results if not r.error)
    console.print(f"\n[green]{ok_count}/{len(results)}[/green] pages read successfully, "
                  f"[bold]{total_chars:,}[/bold] total characters.")


@main.command()
def status():
    """Show system status and diagnostics."""
    table = Table(title="🔧 System Status", border_style="blue")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")

    # Python
    import platform
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

    # Extension bridge (NEW!)
    ext_ok = check_extension_host_running()
    if ext_ok:
        table.add_row("Extension Bridge", "[green]✅ Running[/green]", "localhost:18789 — debugger mode available")
    else:
        table.add_row("Extension Bridge", "[yellow]⚠ Not running[/yellow]", "Open Edge with webreader extension loaded")

    # CDP ports
    active_ports = []
    for p in DEFAULT_CDP_PORTS:
        if check_cdp_available(p):
            active_ports.append(p)
    
    if active_ports:
        ports_str = ", ".join(f"[green]{p}[/green]" for p in active_ports)
        table.add_row("CDP Ports", f"[green]Active: {ports_str}[/green]", "")
    else:
        table.add_row("CDP Ports", "[yellow]None active[/yellow]", "Run: webreader launch (or use -m ext)")

    console.print(table)
    
    # Recommended mode
    if ext_ok:
        console.print("\n[green bold]💡 Recommended: use -m ext for best experience[/green bold]")
    elif active_ports:
        console.print("\n[dim]💡 CDP available. For extension mode, open Edge with webreader extension[/dim]")


DEFAULT_CDP_PORTS = [9222, 9229, 9333, 9220]


if __name__ == "__main__":
    main()
