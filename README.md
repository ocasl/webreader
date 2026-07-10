# 🌐 webreader

> **Read the web through your own browser.**

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-green.svg)](https://www.python.org/)
[![CLI + Extension](https://img.shields.io/badge/CLI%20%2B%20Extension-purple.svg)](#installation)

**Open-source alternative to [dokobot](https://dokobot.ai)'s `--local` mode.**  
One command, zero config, inherits your real browser's login sessions — Reddit behind login walls, X/Twitter feeds, paywalled content, anything you can see in your browser.

---

## ✨ What Makes webreader Different

| Feature | webreader | dokobot | Crawl4AI | Nanobrowser | Jina Reader |
|---|---|---|---|---|---|
| **Uses your real logged-in browser** | ✅ | ✅ | ❌ needs cookie injection | ✅ (extension) | ❌ |
| **Clean Markdown output** | ✅ LLM-ready | ✅ | ✅ | ❌ Agent mode | ✅ |
| **CLI one-liner** | ✅ `webreader read <URL>` | ✅ | ✅ Python API | ❌ extension only | ✅ curl |
| **Browser extension (click to read)** | ✅ | ❌ | ❌ | ✅ | ❌ |
| **100% open source** | ✅ MIT | ⚠️ source unavailable | ✅ | ✅ Apache 2.0 | - |
| **Free forever** | ✅ | ✅ local mode | ✅ | ✅ | ✅ |
| **Batch reading** | ✅ | ❌ | ✅ | ❌ | ❌ |
| **Screenshot capture** | ✅ | ❌ | ✅ | ❌ | ❌ |
| **JSON structured output** | ✅ | ❌ | ✅ | ❌ | ❌ |

### 🎯 The Sweet Spot Nobody Else Covers

**No other tool combines all three:**
1. 🔄 **Bridge your real browser** (keeps cookies / login sessions / family IP)
2. 📝 **Return clean Markdown/Text** (LLM-ready, not raw HTML)
3. 🖱️ **Zero-config UX** (install extension → click icon → get content)

---

## 🚀 Quick Start

```bash
# 1. Install
pip install -e .

# 2. Start Edge with remote debugging (one-time)
webreader launch

# 3. Read any page (even behind login walls!)
webreader read https://www.reddit.com/r/Xiaohongshu/hot/
```

That's it. No API keys, no cloud services, no subscription fees.

## 📦 Installation

### Option A: CLI Only (pip install)

```bash
# Install from PyPI (when published)
pip install webreader

# Or from source:
git clone https://github.com/YOUR_USER/webreader.git
cd webreader
pip install -e .
playwright install chromium   # One-time: download browser binary
```

### Option B: CLI + Browser Extension (Recommended)

The extension gives you a **🌐 button in your toolbar** — click it on any page and instantly extract clean content.

```bash
# Windows:
scripts\install.bat

# Mac/Linux:
./scripts/install.sh
```

Then load the unpacked extension:
1. Open `chrome://extensions` or `edge://extensions`
2. Enable **Developer mode** (toggle in top-right)
3. Click **Load unpacked**
4. Select the `extension/` folder

Done! Now click the 🌐 icon on any page.

---

## 📖 Usage

### CLI Commands

```bash
# Read a single page (auto-detects browser)
webreader read https://example.com

# Read with specific device/port
webreader read https://reddit.com -d local-9222

# Save to file
webreader read https://example.com -o output.md

# JSON output (for pipelines)
webreader read https://example.com --format json > result.json

# With screenshot
webreader read https://example.com --screenshot page.png

# Wait for dynamic content to load
webreader read https://slow-site.com -w "#main-content"

# Batch read multiple URLs
webreader batch url1 url2 url3 -c 5

# List connected browsers
webreader launch    # Start Edge
webreader list      # Show devices

# System diagnostics
webreader status
```

### Browser Extension Usage

1. Navigate to any webpage
2. Click the **🌐 webreader** icon in your toolbar
3. Click **"Read This Page"**
4. Content is extracted as clean Markdown
5. Click **"Copy Markdown"** to copy to clipboard

### Python API

```python
import asyncio
from webreader.reader import WebReader

async def main():
    async with WebReader(cdp_url="http://localhost:9222") as reader:
        # Single page
        result = await reader.read("https://www.reddit.com/r/Xiaohongshu/hot/")
        print(result.markdown)
        print(result.title)

        # Multiple pages concurrently
        results = await reader.read_multiple(
            ["url1", "url2", "url3"],
            concurrency=3,
        )
        for r in results:
            print(f"{r.title}: {len(r.markdown)} chars")

asyncio.run(main())
```

---

## 🔧 Architecture

```
┌─────────────────────────────────────────────┐
│              User Interface                  │
│                                              │
│  ┌──────────┐    ┌──────────────────┐       │
│  │  CLI     │    │ Browser Extension │      │
│  │  click   │    │  (click 🌐 icon)  │      │
│  └────┬─────┘    └────────┬─────────┘       │
│       │                   │                  │
└───────┼───────────────────┼──────────────────┘
        │                   │
        ▼                   ▼
┌─────────────────────────────────────────────┐
│           Native Messaging Host              │
│           (host.py)                          │
│         Extension ↔ Python bridge            │
└───────────────────┬─────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│              Core Engine                     │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  Reader  │  │ Extractor│  │ Browser  │  │
│  │ (CDP)    │  │(HTML→MD) │  │ Discovery│  │
│  └──────────┘  └──────────┘  └──────────┘  │
│                                              │
│  ┌──────────┐                                │
│  │ Fallback │  ← Jina/Crawl4AI for non-login │
│  └──────────┘                                │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│          Your Real Browser (Edge/Chrome)     │
│     --remote-debugging-port=9222             │
│     Cookies · Login Sessions · Family IP     │
└─────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|---|---|
| `webreader/cli.py` | Command-line interface (Click-based) |
| `webreader/reader.py` | Core reading engine (Playwright CDP) |
| `webreader/extractor.py` | HTML → Markdown converter |
| `webreader/browser.py` | Browser discovery & management |
| `extension/background.js` | Extension background service worker |
| `extension/popup.html/js` | Popup UI |
| `native_host/host.py` | Native Messaging bridge (Extension ↔ Python) |
| `scripts/install.bat` | Windows one-click installer |

---

## 💡 Why Build This?

[dokobot](https://dokobot.ai)'s `--local` mode is brilliant — use your real browser to bypass login walls. But:

1. **Source code is not public** (despite MIT license claims)
2. **No browser extension** — you have to remember CLI commands
3. **No batch reading** or structured output
4. **Search costs money** (we replaced it with free alternatives)

**webreader = dokobot local mode + Chrome Extension + Crawl4AI quality output + 100% open source**

---

## 🛠️ Development

```bash
# Clone & install
git clone https://github.com/YOUR_USER/webreader.git
cd webreader
python -m venv venv
source venv/bin/activate  # or .\venv\Scripts\activate on Windows
pip install -e ".[dev]"
playwright install chromium

# Run
webreader status
webreader launch
webreader read https://example.com

# Tests
pytest tests/

# Build distribution
python -m build
```

---

## 📋 Roadmap

- [x] CLI `read` command with Playwright CDP
- [x] Browser auto-discovery (`list`)
- [x] Auto-launch Edge with debugging (`launch`)
- [x] Clean Markdown extraction
- [x] Browser Extension (Chrome/Edge)
- [x] Native Messaging Host
- [x] Batch reading
- [x] Screenshot capture
- [ ] Firefox support
- [ ] MCP Server integration
- [ ] Jina Reader fallback for non-login pages
- [ ] Docker deployment option
- [ ] Web dashboard (Flask/FastAPI)
- [ ] VS Code / Cursor plugin

---

## 🤝 Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

Areas that need help:
- More extractor strategies (academic papers, news sites, social media)
- Firefox port of the extension
- macOS native app wrapper
- Internationalization (i18n)

---

## 📄 License

[MIT](LICENSE) © 2026 webreader contributors

**Free forever. Open forever. Your data stays yours.**

---

<p align="center">
  Made with ❤️ by people who believe the web should be readable by everyone.
</p>
