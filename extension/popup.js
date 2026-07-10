/**
 * webreader Popup Script v2
 * 
 * Simplified: Extension mode works standalone — NO Native Host needed!
 * Just click "Read This Page" and it extracts via chrome.debugger.
 * Native Host is only needed for CLI ↔ Extension bridge (optional).
 */

let lastResult = null;

// ─── Init ───────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  updateStatus(true);
});

function updateStatus(ready) {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  
  dot.className = 'status-dot connected';
  text.innerHTML = '<span class="status-dot connected"></span> Ready — click Read below';
  
  document.getElementById('readCurrentBtn').disabled = false;
}

// ─── Read Current Tab ────────────────────────────────────────────

async function readCurrentTab() {
  const btn = document.getElementById('readCurrentBtn');
  const loading = document.getElementById('loading');
  const output = document.getElementById('output');
  const copyBtn = document.getElementById('copyBtn');

  btn.disabled = true;
  loading.classList.add('visible');
  output.classList.remove('visible');
  output.textContent = '';
  document.getElementById('loadingText').textContent = 'Extracting page content...';

  try {
    const response = await chrome.runtime.sendMessage({
      action: 'read_current_tab',
      options: { timeout: 60000 },
    });

    if (response.error) {
      throw new Error(response.error);
    }

    lastResult = response;
    
    loading.classList.remove('visible');
    output.classList.add('visible');
    output.textContent = response.markdown || response.text || JSON.stringify(response, null, 2);

    const len = (response.markdown || response.text || '').length;
    document.getElementById('charCount').textContent = `${len.toLocaleString()} chars · ${response.source || 'extension'}`;

    copyBtn.disabled = false;

    showToast(`✅ Extracted ${len.toLocaleString()} characters`);

  } catch (e) {
    loading.classList.remove('visible');
    btn.disabled = false;
    showToast(`❌ ${e.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

// ─── Copy Result ─────────────────────────────────────────────────

async function copyResult() {
  if (!lastResult) return;

  const text = lastResult.markdown || lastResult.text || '';
  
  try {
    await navigator.clipboard.writeText(text);
    showToast('📋 Copied to clipboard!');
  } catch (e) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    showToast('📋 Copied!');
  }
}

// ─── CLI Hint ───────────────────────────────────────────────────

function showCliHint() {
  const output = document.getElementById('output');
  output.classList.add('visible');
  output.textContent = `# webreader CLI Usage

## Quick Start (Extension Mode)
1. Install the webreader browser extension ✅ (you're using it now!)
2. Open any webpage and click the 🌐 icon → "Read This Page"

## CLI Mode (optional — needs Python backend)
\`\`\`bash
# Install
pip install webreader
webreader install-extension

# Use CLI to read URLs from terminal
webreader read <URL>
webreader batch url1 url2 url3

# With screenshot / JSON output
webreader read <URL> --screenshot shot.png -o result.json --format json
\`\`\`

## Tips
• Extension mode: Zero setup, just click the icon
• CLI mode: Batch reading, screenshots, JSON, automation
• Both modes use your real browser's login session
`;
  document.getElementById('charCount').textContent = '';
}

// ─── Toast ───────────────────────────────────────────────────────

function showToast(message, isError = false) {
  document.querySelectorAll('.toast').forEach(t => t.remove());

  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  if (isError) toast.style.background = '#dc2626';
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}
