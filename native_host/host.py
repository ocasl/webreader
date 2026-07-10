#!/usr/bin/env python3
"""
webreader Native Messaging Host v2

Dual-mode bridge:
  1. Native Messaging protocol (stdin/stdout) — for extension-initiated reads
  2. HTTP API server (localhost:18789) — for CLI-initiated reads (no CDP needed!)

Architecture:
  CLI ──HTTP POST /read──→ [host.py] ──NM port──→ [extension bg.js]
                                                  │
                                            chrome.debugger.attach(tabId)
                                            Runtime.evaluate(extract)
                                                  │
                                            NM port response ←──┘
                                                  │
  CLI ←──HTTP Response──── [host.py]
"""

from __future__ import annotations

import json
import sys
import os
import asyncio
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# Add webreader package to path
try:
    from webreader.extractor import html_to_markdown, extract_structured_data
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    try:
        from webreader.extractor import html_to_markdown, extract_structured_data
    except ImportError:
        pass  # Minimal mode


# ─── Configuration ──────────────────────────────────────────────

HTTP_PORT = 18789
HTTP_HOST = '127.0.0.1'
REQUEST_TIMEOUT = 120  # seconds


# ─── Native Messaging Protocol ──────────────────────────────────

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


# ─── Extension Bridge (holds the NM port reference) ─────────────

class ExtensionBridge:
    """
    Manages the connection between the extension (via Native Messaging port)
    and external callers (CLI via HTTP).
    
    The extension initiates the NM connection. Once connected, we can forward
    commands from HTTP clients to the extension and relay responses back.
    """
    
    def __init__(self):
        self._port = None  # The chrome.runtime.Port object (we just store a ref)
        self._connected = False
        self._pending_requests: dict[str, asyncio.Future] = {}  # request_id → Future
        self._request_counter = 0
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    def set_port(self, port_ref):
        """Called when extension connects via connectNative()."""
        self._port = port_ref
        self._connected = True
        print(f"[webreader-host] Extension connected", file=sys.stderr)
    
    def on_disconnect(self):
        """Called when extension disconnects."""
        self._connected = False
        self._port = None
        # Fail all pending requests
        for rid, future in self._pending_requests.items():
            if not future.done():
                future.set_result({'type': 'error', 'message': 'Extension disconnected'})
        self._pending_requests.clear()
        print("[webreader-host] Extension disconnected", file=sys.stderr)
    
    async def send_command(self, command: dict, timeout: int = REQUEST_TIMEOUT) -> dict:
        """
        Send a command to the extension via Native Messaging port,
        wait for response.
        
        This is called by the HTTP handler.
        """
        if not self._connected:
            return {'type': 'error', 'message': 'Extension not connected. Is Edge running with webreader installed?'}
        
        # Generate unique request ID
        self._request_counter += 1
        req_id = f"req_{self._request_counter}_{int(time.time()*1000)}"
        
        # Create future for this request
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests[req_id] = future
        
        # Add request_id to command
        cmd_with_id = {**command, 'request_id': req_id}
        
        # Write to extension's stdin (which is our stdout in NM protocol)
        send_message(cmd_with_id)
        
        # Wait for response with timeout
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            return {'type': 'error', 'message': f'Extension timed out after {timeout}s'}
    
    def handle_extension_response(self, msg: dict):
        """
        Called when we receive a response from the extension.
        Matches it to the pending request and resolves the future.
        """
        req_id = msg.get('request_id', '')
        if req_id and req_id in self._pending_requests:
            future = self._pending_requests.pop(req_id)
            if not future.done():
                future.set_result(msg)
        else:
            # Unsolicited message (e.g., popup read) — log and ignore
            print(f"[webreader-host] Unsolicited msg: {msg.get('type')}", file=sys.stderr)


# Global bridge instance
bridge = ExtensionBridge()


# ─── Message Handlers (extension → host) ─────────────────────────

async def handle_read_url(msg: dict) -> dict:
    """Handle 'read_url' from extension — legacy mode, uses Playwright CDP."""
    url = msg.get('url', '')
    if not url:
        return {'type': 'error', 'message': 'No URL provided'}
    
    # Try to find browser via CDP as fallback
    try:
        from webreader.browser import discover_browsers
        devices = discover_browsers()
        cdp_url = devices[0].cdp_url if devices else None
        
        if not cdp_url:
            return {
                'type': 'read_result',
                'url': url,
                'markdown': '',
                'text': '',
                'source': 'no_browser',
                'error': 'No CDP browser found. Use extension mode instead (no --remote-debugging-port needed)',
            }
        
        from webreader.reader import WebReader
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
    except Exception as e:
        return {'type': 'error', 'message': str(e)}


def handle_read_content(msg: dict) -> dict:
    """
    Handle 'read' where extension already extracted HTML/text.
    Just convert to clean markdown using Python extractor.
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


# Handler dispatch table (for messages FROM extension)
EXTENSION_HANDLERS = {
    'read_url': handle_read_url,
    'read': handle_read_content,
    '_connected': lambda m: (bridge.set_port(m.get('port_ref', True)), {'type': 'ack'})[1],
}


# ─── HTTP API Server ─────────────────────────────────────────────

class WebReaderHandler(BaseHTTPRequestHandler):
    """HTTP handler for CLI → Host communication."""
    
    def log_message(self, format, *args):
        # Quiet logs
        pass
    
    def do_POST(self):
        """Handle POST /read — CLI sends URL to read."""
        if self.path != '/read':
            self.send_error(404, "Not Found")
            return
        
        # Read body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            data = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            self._json_response({'error': 'Invalid JSON'}, 400)
            return
        
        url = data.get('url', '')
        options = data.get('options', {})
        timeout = int(options.get('timeout', REQUEST_TIMEOUT))
        
        if not url:
            self._json_response({'error': 'Missing "url" field'}, 400)
            return
        
        print(f"[webreader-http] READ request: {url[:80]}...", file=sys.stderr)
        
        # Forward command to extension via bridge
        async def _do_request():
            result = await bridge.send_command({
                'type': 'cli_read_url',
                'url': url,
                'options': options,
            }, timeout=timeout)
            return result
        
        # Run async in new event loop (handler runs in thread)
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(_do_request())
            loop.close()
        except Exception as e:
            result = {'type': 'error', 'message': str(e)}
        
        self._json_response(result)
    
    def do_GET(self):
        """Health check / status endpoint."""
        if self.path == '/health':
            self._json_response({
                'status': 'ok',
                'extension_connected': bridge.is_connected,
                'version': '0.2.0',
            })
        elif self.path == '/status':
            self._json_response({
                'status': 'ok',
                'extension_connected': bridge.is_connected,
                'pending_requests': len(bridge._pending_requests),
                'version': '0.2.0',
            })
        else:
            self.send_error(404, "Not Found")
    
    def _json_response(self, data: dict, status: int = 200):
        """Send JSON response."""
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread."""
    daemon_threads = True


def start_http_server():
    """Start the HTTP server in a background thread."""
    server = ThreadedHTTPServer((HTTP_HOST, HTTP_PORT), WebReaderHandler)
    
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    print(f"[webreader-host] HTTP API listening on {HTTP_HOST}:{HTTP_PORT}", file=sys.stderr)
    return server


# ─── Main Loop (Native Messaging stdin/stdout) ───────────────────

async def main():
    """Main loop: read NM messages from extension → handle → respond."""
    
    # Start HTTP server for CLI communication
    start_http_server()
    
    print("[webreader-host] Native Messaging Host started (v0.2.0)", file=sys.stderr)
    print("[webreader-host] Waiting for extension connection...", file=sys.stderr)
    
    while True:
        msg = read_message()
        
        if msg is None:
            break  # EOF / stdin closed → extension closed the pipe
        
        msg_type = msg.get('type', '')
        
        # Special: extension just connected and is saying hi
        if msg_type == 'hello' or msg_type == '_connected':
            bridge.set_port(True)
            send_message({'type': 'welcome', 'version': '0.2.0', 'mode': 'dual'})
            continue
        
        # Response from extension (has request_id) — route to pending future
        if 'request_id' in msg:
            bridge.handle_extension_response(msg)
            continue
        
        # Regular command from extension (legacy read/read_url)
        handler = EXTENSION_HANDLERS.get(msg_type)
        
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        # Normal: extension disconnected
        pass
