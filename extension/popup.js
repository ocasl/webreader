/**
 * webreader Popup Script
 */

let lastResult = null;
let nativeConnected = false;

// ─── Init ───────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  checkNativeConnection();
  
  // Listen for results from background script
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === 'native_result') {
      handleResult(msg.data);
    } else if (msg.action === 'native_disconnected') {
      updateStatus(false, msg.error || 'Disconnected');
    }
  });
});

async function checkNativeConnection() {
  try {
    const response = await chrome.runtime.sendMessage({ action: 'test_native' });
    updateStatus(response.connected);
  } catch (e) {
    updateStatus(false, e.message);
  }
}

function updateStatus(connected, errorText = '') {
  nativeConnected = connected;
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  
  if (connected) {
    dot.className = 'status-dot connected';
    text.innerHTML = '<span class="status-dot connected"></span> Native Host Connected';
  } else {
    dot.className = 'status-dot disconnected';
    text.innerHTML = `<span class="status-dot disconnected"></span> ${errorText || 'Not connected — install CLI first'}`;
  }

  // Enable/disable read button
  document.getElementById('readCurrentBtn').disabled = false; // Works in extension-only mode too
}

// ─── Read Current Tab ────────────────────────────────────────────

async function readCurrentTab() {
  const btn = document.getElementById('readCurrentBtn');
  const loading = document.getElementById('loading');
  const output = document.getElementById('output');
  const copyBtn = document.getElementById('copyBtn');

  // Show loading
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

    // Show result
    lastResult = response;
    
    loading.classList.remove('visible');
    output.classList.add('visible');
    output.textContent = response.markdown || response.text || JSON.stringify(response, null, 2);

    // Update char count
    const len = (response.markdown || response.text || '').length;
    document.getElementById('charCount').textContent = `${len.toLocaleString()} chars`;

    copyBtn.disabled = false;

    showToast(`✅ Read ${len.toLocaleString()} characters`);

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
    // Fallback for older browsers
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

// ─── CLI Hint ────────────────────────────────────────────────────

function showCliHint() {
  const output = document.getElementById('output');
  output.classList.add('visible');
  output.textContent = `# webreader CLI Usage

## Install
pip install webreader
webreader install-extension   # Install browser extension

## Quick Start
webreader launch             # Start Edge with debugging
webreader read <URL>         # Read any page
webreader list               # List browsers

## Examples
# Read Reddit (requires login):
webreader read https://www.reddit.com/r/Xiaohongshu/hot/

# Batch read multiple pages:
webreader batch url1 url2 url3

# Save to file:
webreader read <URL> -o page.md --format json

# With screenshot:
webreader read <URL> --screenshot shot.png

# Auto-detect device:
webreader read <URL> -d local-9222

## Tips
• Extension mode: Click the icon on any page to instantly extract content
• CLI mode: Full power with batch reading, screenshots, JSON output
• Both modes use your real browser's login session
`;
  document.getElementById('charCount').textContent = '';
}

// ─── Toast ───────────────────────────────────────────────────────

function showToast(message, isError = false) {
  // Remove existing toast
  document.querySelectorAll('.toast').forEach(t => t.remove());

  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  if (isError) {
    toast.style.background = '#dc2626';
  }
  document.body.appendChild(toast);

  setTimeout(() => toast.remove(), 2500);
}
