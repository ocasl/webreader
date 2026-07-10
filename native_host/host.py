#!/usr/bin/env python3
"""
webreader Native Messaging Host

Bridges the Chrome/Edge extension with the Python backend.
Receives messages from extension → processes them → sends results back.

This is the "glue" that makes the browser extension work seamlessly.
"""

from __future__ import annotations

import json
import sys
import os
import asyncio

# Add the webreader package to path (when installed)
try:
    from webreader.reader import WebReader, ReadResult
    from webreader.extractor import html_to_markdown, extract_structured_data
    from webreader.browser import discover_browsers, check_cdp_available
except ImportError:
    # Fallback: try parent directory (for development)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    try:
        from webreader.reader import WebReader, ReadResult
        from webreader.extractor import html_to_markdown, extract_structured_data
        from webreader.browser import discover_browsers, check_cdp_available
    except ImportError as e:
        # Minimal mode — just echo back what we get
        pass


def read_message():
    """Read a message from stdin (Native Messaging protocol)."""
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) == 0:
        return None
    
    length = int.from_bytes(raw_length, byteorder='little')
    message = sys.stdin.buffer.read(length)
    return json.loads(message.decode('utf-8'))


def send_message(message: dict):
    """Send a message to stdout (Native Messaging protocol)."""
    encoded = json.dumps(message, ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(len(encoded).to_bytes(4, byteorder='little'))
    sys.stdout.buffer.flush()
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


async def handle_read_url(msg: dict) -> dict:
    """Handle a 'read_url' command from the extension."""
    url = msg.get('url', '')
    
    if not url:
        return {'type': 'error', 'message': 'No URL provided'}
    
    # Find a connected browser
    devices = discover_browsers()
    cdp_url = None
    if devices:
        cdp_url = devices[0].cdp_url
    
    if not cdp_url:
        return {
            'type': 'read_result',
            'url': url,
            'markdown': '',
            'text': '',
            'source': 'no_browser',
            'error': 'No browser available. Start Edge with --remote-debugging-port=9222',
        }
    
    async with WebReader(cdp_url=cdp_url, headless=False, timeout=120000) as reader:
        result = await reader.read(url)

        return {
            'type': 'read_result',
            'url': result.url,
            'title': result.title,
            'markdown': result.markdown,
            'text': result.text,
            'status_code': result.status_code,
            'elapsed_seconds': result.elapsed_seconds,
            'structured': extract_structured_data(result.markdown, url=url),
            'error': result.error,
        }


def handle_read_content(msg: dict) -> dict:
    """
    Handle a 'read' command where the extension already extracted 
    the HTML/text content. Just convert to clean markdown.
    """
    raw_html = msg.get('html', '')
    raw_text = msg.get('text', '')
    title = msg.get('title', '')
    url = msg.get('url', '')

    if raw_html:
        markdown = html_to_markdown(raw_html, url=url)
    else:
        markdown = raw_text or ''

    return {
        'type': 'read_result',
        'url': url,
        'title': title,
        'markdown': markdown,
        'text': raw_text,
        'status_code': 200,
        'elapsed_seconds': 0.1,
        'structured': extract_structured_data(markdown, url=url),
        'source': 'native_host_processed',
    }


# ─── Message Dispatch Table ────────────────────────────────────────

HANDLERS = {
    'read_url': handle_read_url,
    'read': handle_read_content,
    'ping': lambda m: {'type': 'pong', 'version': '0.1.0'},
    'status': lambda m: {
        'type': 'status', 
        'browsers': [d.to_dict() for d in discover_browsers()],
        'python_ok': True,
    },
}


async def main():
    """Main loop: read message → handle → send response."""
    while True:
        msg = read_message()
        
        if msg is None:
            break  # EOF / stdin closed
        
        msg_type = msg.get('type', '')
        handler = HANDLERS.get(msg_type)

        if handler:
            try:
                if asyncio.iscoroutinefunction(handler):
                    response = await handler(msg)
                else:
                    response = handler(msg)
                
                send_message(response)
            
            except Exception as e:
                send_message({
                    'type': 'error',
                    'message': str(e),
                    'request_type': msg_type,
                })
        else:
            send_message({
                'type': 'error',
                'message': f'Unknown message type: {msg_type}',
            })


if __name__ == '__main__':
    # Run the async main loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
