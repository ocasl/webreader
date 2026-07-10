/**
 * webreader Chrome/Edge Extension - Background Service Worker v2
 * 
 * Architecture (dokobot-style, no CDP needed):
 * 
 *   CLI → HTTP POST localhost:18789/read → [host.py]
 *                                              ↓ (Native Messaging)
 *                                        [this background.js]
 *                                              ↓
 *                                    chrome.debugger.attach(tabId)
 *                                    Runtime.evaluate(extract DOM)
 *                                              ↓
 *                                   NM response → host.py → HTTP → CLI
 */

const NATIVE_HOST_NAME = "com.webreader.host";
const VERSION = "0.2.0";

// ─── State ──────────────────────────────────────────────────────

let nativePort = null;
/** Map: requestId → {tabId?, resolve?, timer?} */
let pendingCliRequests = {};

// ─── Native Messaging Connection ────────────────────────────────

function connectToNativeHost() {
  if (nativePort && nativePort.error === null) {
    return nativePort;
  }

  try {
    nativePort = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    
    // Send hello so host knows we're alive
    nativePort.postMessage({ type: "hello", version: VERSION });
    
    nativePort.onMessage.addListener((msg) => {
      console.log("[webreader-bg] Received:", msg.type || JSON.stringify(msg).slice(0, 100));
      
      // Route CLI responses back via NM
      if (msg.requestId || msg.request_id || msg.type === "cli_result" || msg.type === "read_result") {
        const reqId = msg.requestId || msg.request_id;
        if (reqId && pendingCliRequests[reqId]) {
          const pending = pendingCliRequests[reqId];
          clearTimeout(pending.timer);
          delete pendingCliRequests[reqId];
          
          // Send back through NM to host (host will forward to CLI via HTTP)
          nativePort.postMessage(msg);
          return;
        }
      }
      
      // Popup-initiated results: forward to popup
      if (msg.type === "read_result") {
        chrome.runtime.sendMessage({ action: "native_result", data: msg });
        return;
      }
    });

    nativePort.onDisconnect.addListener(() => {
      console.error("[webreader-bg] Native host disconnected:", chrome.runtime.lastError?.message);
      nativePort = null;
      
      // Fail all pending CLI requests
      Object.keys(pendingCliRequests).forEach(rid => {
        const p = pendingCliRequests[rid];
        clearTimeout(p.timer);
        if (p.resolve) {
          p.resolve({ type: "error", message: "Native host disconnected", requestId: rid });
        }
        delete pendingCliRequests[rid];
      });
      
      chrome.runtime.sendMessage({ 
        action: "native_disconnected", 
        error: chrome.runtime.lastError?.message || "Unknown error" 
      });
    });

    console.log(`[webreader-bg] Connected to native host (${VERSION})`);
    return nativePort;
  } catch (e) {
    console.error("[webreader-bg] Failed to connect to native host:", e);
    return null;
  }
}

// Auto-connect on service worker startup
connectToNativeHost();

// ─── Message Handlers (from popup/content script) ───────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const { action, data } = message;

  switch (action) {
    case "read_current_tab":
      handleReadCurrentTab(data).then(sendResponse).catch(err => {
        sendResponse({ error: err.message });
      });
      return true;

    case "read_url":
      handleReadUrl(data.url, data.options).then(sendResponse).catch(err => {
        sendResponse({ error: err.message });
      });
      return true;

    case "test_native":
      const port = connectToNativeHost();
      sendResponse({ connected: port !== null && port.error === null });

    case "get_status":
      sendResponse({
        nativeConnected: nativePort && nativePort.error === null,
        version: VERSION,
        pendingCliCount: Object.keys(pendingCliRequests).length,
      });

    default:
      break;
  }
});

// ─── CLI Command Handler (from host via NM) ─────────────────────

/**
 * When the host forwards a CLI 'cli_read_url' command, this function:
 * 1. Finds an existing tab with that URL, OR opens a new tab
 * 2. Attaches chrome.debugger to the tab
 * 3. Evaluates extraction JS via Debugger API
 * 4. Sends result back via NM
 */
async function handleCliCommand(cmd) {
  const url = cmd.url;
  const options = cmd.options || {};
  const reqId = cmd.request_id || cmd.requestId;
  const timeout = (options.timeout || 120) * 1000; // ms
  
  console.log(`[webreader-bg] CLI read request: ${url.slice(0, 80)}... (id=${reqId})`);
  
  let tab;
  
  // Step 1: Find existing tab or open new one
  const tabs = await chrome.tabs.query({ url: url.split('?')[0].split('#')[0] + '*' });
  
  if (tabs.length > 0) {
    tab = tabs[0];
    console.log(`[webreader-bg] Found existing tab: ${tab.id}`);
  } else {
    // Open new tab
    tab = await chrome.tabs.create({ url: url, active: false });
    console.log(`[webreader-bg] Created new tab: ${tab.id}`);
  }
  
  // Step 2: Wait for page to load
  if (tab.status !== 'complete') {
    await waitForTabLoad(tab.id, Math.min(timeout, 30000));
  }
  
  // Extra wait for dynamic content (X/Reddit need this)
  await sleep(3000);
  
  // Step 3: Attach debugger
  let content = null;
  let error = null;
  
  try {
    await attachDebugger(tab.id);
    
    // Step 4: Extract page content via Debugger.evaluate
    content = await extractViaDebugger(tab.id);
    
    // Detach
    await detachDebugger(tab.id);
    
  } catch (e) {
    error = e.message || String(e);
    console.error(`[webreader-bg] Debug extract failed:`, error);
    
    // Ensure detach
    try { await detachDebugger(tab.id); } catch (_) {}
  }
  
  // Step 5: Send result back via Native Messaging
  const result = {
    type: "read_result",
    request_id: reqId,
    requestId: reqId,  // Both formats for compatibility
    url: url,
    title: content?.title || "",
    markdown: content?.markdown || "",
    text: content?.text || "",
    html_size: content?.htmlSize || 0,
    word_count: content?.wordCount || 0,
    source: "chrome_debugger",
    error: error,
    elapsed_ms: content?.elapsedMs || 0,
  };
  
  const port = connectToNativeHost();
  if (port) {
    port.postMessage(result);
  }
  
  console.log(`[webreader-bg] Result sent: ${(content?.markdown || '').length} chars, error=${!!error}`);
  
  return result;
}

// Register handler for incoming CLI commands from host
// We intercept these in the onMessage listener above, but also handle
// direct NM messages here:
const originalPostMessage = null;  // We'll patch below

// Actually, CLI commands come through NM onMessage, so we hook that
// We already set up onMessage above. Let's add routing for cli_read_url:

// Patch: wrap the existing onMessage to also handle CLI commands
// This is done by adding the check in the existing listener above.

// ... but since we can't easily modify the closure, we'll use a different approach:
// Add a dedicated listener for NM-originated CLI commands.

// Re-open the connection to capture CLI commands
function setupCliCommandListener() {
  const port = connectToNativeHost();
  if (!port) return;
  
  // Replace/add onMessage handler that routes CLI commands
  port.onMessage.addListener((msg) => {
    console.log("[webreader-bg] NM msg:", msg.type, msg.request_id ? `(id=${msg.request_id})` : "");
    
    // If this is a CLI command from the host (forwarded), handle it
    if (msg.type === "cli_read_url") {
      handleCliCommand(msg).catch(err => {
        console.error("[webreader-bg] CLI command error:", err);
        const failResult = {
          type: "read_result",
          request_id: msg.request_id || msg.requestId,
          error: err.message || String(err),
        };
        const p = connectToNativeHost();
        if (p) p.postMessage(failResult);
      });
    }
  });
}

// Set up CLI listener after a short delay (ensure service worker ready)
setTimeout(setupCliCommandListener, 500);


// ─── Read Operations ─────────────────────────────────────────────

async function handleReadCurrentTab(options = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("No active tab found");
  
  return readTab(tab.id, options);
}

async function handleReadUrl(url, options = {}) {
  const tabs = await chrome.tabs.query({ url: url.split('?')[0].split('#')[0] + '*' });
  
  let tab;
  if (tabs.length > 0) {
    tab = tabs[0];
  } else {
    tab = await chrome.tabs.create({ url, active: false });
    // Wait for load
    await waitForTabLoad(tab.id, 30000);
    await sleep(3000);
  }
  
  return readTab(tab.id, options);
}

/**
 * Read a tab's content using chrome.debugger API.
 * This is the core magic — works without --remote-debugging-port!
 */
async function readTab(tabId, options = {}) {
  const timeout = options.timeout || 60000;
  
  const tab = await chrome.tabs.get(tabId);
  
  let content;
  let error;
  
  try {
    await attachDebugger(tabId);
    content = await extractViaDebugger(tabId);
    await detachDebugger(tabId);
  } catch (e) {
    error = e.message || String(e);
    try { await detachDebugger(tabId); } catch (_) {}
  }
  
  if (error) throw new Error(error);
  
  // Try sending via native host for markdown processing
  const port = connectToNativeHost();
  if (port && content) {
    try {
      return await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          reject(new Error("Native host timeout"));
        }, timeout);
        
        const listener = (msg) => {
          clearTimeout(timer);
          port.onMessage.removeListener(listener);
          resolve(msg);
        };
        
        port.onMessage.addListener(listener);
        port.postMessage({
          type: "read",
          url: tab.url || "",
          title: tab.title || "",
          html: content.rawHtml || "",
          text: content.text || "",
          markdown: content.markdown || "",  // Pre-extracted if available
          options: options,
        });
      });
    } catch (e) {
      // Fall through to returning raw content
    }
  }
  
  // No native host — return what we got directly
  return {
    type: "read_result",
    url: tab.url || "",
    title: tab.title || "",
    markdown: content?.markdown || content?.text || "",
    text: content?.text || "",
    source: "debugger_only",
  };
}


// ─── Chrome Debugger Operations ──────────────────────────────────

function attachDebugger(tabId) {
  return new Promise((resolve, reject) => {
    chrome.debugger.attach({ tabId }, "1.3", () => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve();
      }
    });
  });
}

function detachDebugger(tabId) {
  return new Promise((resolve) => {
    chrome.debugger.detach({ tabId }, () => {
      resolve();  // Ignore errors on detach
    });
  });
}

/**
 * Use the Chrome DevTools Protocol to evaluate JavaScript in the page context.
 * This extracts clean DOM content WITHOUT needing remote-debugging-port!
 */
function extractViaDebugger(tabId) {
  const startTime = Date.now();
  
  return new Promise((resolve, reject) => {
    // Use Runtime.evaluate to run extraction JS in the page
    chrome.debugger.sendCommand(
      { tabId },
      "Runtime.evaluate",
      {
        expression: `
          (function() {
            try {
              // Get basic info
              var title = document.title;
              var url = location.href;
              var text = document.body.innerText;
              
              // Clone and clean DOM for HTML extraction
              var clone = document.documentElement.cloneNode(true);
              var removeSelectors = [
                'script', 'style', 'noscript', 'svg', 'canvas', 'iframe',
                '[aria-hidden="true"]',
                'nav', 'footer', '[role="navigation"]', '[role="contentinfo"]',
                '.sidebar', '.ad', '.advertisement', '.cookie-banner',
                '#sidebar', '#comments', '#footer',
                '[data-testid="primarySidebar"]',
                '[data-testid="sidebar"]',
                '[id*="sidebar"]', '[class*="sidebar"]',
              ];
              removeSelectors.forEach(function(sel) {
                clone.querySelectorAll(sel).forEach(function(el) { el.remove(); });
              });
              var rawHtml = clone.outerHTML;
              
              // Word count
              var wordCount = (text.match(/\\S+/g) || []).length;
              
              return JSON.stringify({
                success: true,
                title: title,
                url: url,
                text: text.substring(0, 500000),
                rawHtml: rawHtml.substring(0, 1000000),
                htmlSize: rawHtml.length,
                wordCount: wordCount,
                elapsedMs: Date.now() - startTime
              });
            } catch(e) {
              return JSON.stringify({success: false, error: e.message || String(e)});
            }
          })()
        `,
        returnByValue: true,
        awaitPromise: false,
      },
      (result) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        
        // result.result.value is the JSON string we returned
        try {
          const parsed = typeof result.result.value === 'string' 
            ? JSON.parse(result.result.value) 
            : result.result.value;
          
          if (!parsed.success) {
            reject(new Error(parsed.error || 'Extraction failed'));
            return;
          }
          
          resolve({
            title: parsed.title,
            text: parsed.text,
            rawHtml: parsed.rawHtml,
            htmlSize: parsed.htmlSize,
            wordCount: parsed.wordCount,
            elapsedMs: parsed.elapsedMs,
          });
        } catch (parseErr) {
          // Fallback: try to get whatever we can
          resolve({
            text: '',
            rawHtml: '',
            htmlSize: 0,
            wordCount: 0,
            elapsedMs: Date.now() - startTime,
          });
        }
      }
    );
  });
}


// ─── Utilities ──────────────────────────────────────────────────

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function waitForTabLoad(tabId, timeout) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeout);  // Resolve anyway after timeout
    
    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        clearTimeout(timer);
        resolve();
      }
    }
    
    chrome.tabs.onUpdated.addListener(listener);
    
    // Also check current status immediately
    chrome.tabs.get(tabId, (tab) => {
      if (tab && tab.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        clearTimeout(timer);
        resolve();
      }
    });
  });
}
