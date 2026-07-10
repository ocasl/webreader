/**
 * webreader Chrome/Edge Extension - Background Service Worker
 * 
 * Handles:
 * 1. Native Messaging Host communication (send page content to Python CLI)
 * 2. Popup messages
 * 3. Content script coordination
 */

// ─── Native Messaging ────────────────────────────────────────────

const NATIVE_HOST_NAME = "com.webreader.host";
let nativePort = null;

function connectToNativeHost() {
  if (nativePort && nativePort.error === null) {
    return nativePort;
  }

  try {
    nativePort = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    
    nativePort.onMessage.addListener((msg) => {
      console.log("[webreader] Native message received:", msg.type || msg);
      
      // Forward to popup or content script
      if (msg.type === "read_result") {
        // Send result to popup
        chrome.runtime.sendMessage({ action: "native_result", data: msg });
      }
    });

    nativePort.onDisconnect.addListener(() => {
      console.log("[webreader] Native host disconnected:", chrome.runtime.lastError?.message);
      nativePort = null;
      
      // Notify popup about disconnect
      chrome.runtime.sendMessage({ 
        action: "native_disconnected", 
        error: chrome.runtime.lastError?.message || "Unknown error" 
      });
    });

    console.log("[webreader] Connected to native host");
    return nativePort;
  } catch (e) {
    console.error("[webreader] Failed to connect to native host:", e);
    return null;
  }
}

// ─── Message Handling ────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const { action, data } = message;

  switch (action) {
    case "read_current_tab":
      handleReadCurrentTab(data).then(sendResponse).catch(err => {
        sendResponse({ error: err.message });
      });
      return true; // Keep channel open for async response

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
        version: chrome.runtime.getManifest().version,
      });
      break;

    default:
      break;
  }
});

// ─── Read Operations ─────────────────────────────────────────────

async function handleReadCurrentTab(options = {}) {
  // Get the active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  
  if (!tab) {
    throw new Error("No active tab found");
  }

  // Extract content from the tab using the content script
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: extractPageContent,
  });

  const content = results?.[0]?.result;
  
  if (!content) {
    throw new Error("Failed to extract page content");
  }

  // Try to send via native host
  const port = connectToNativeHost();
  
  let response;
  if (port) {
    response = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error("Native host timeout"));
      }, options.timeout || 60000);

      port.onMessage.addListener(function listener(msg) {
        clearTimeout(timeout);
        port.onMessage.removeListener(listener);
        resolve(msg);
      });

      port.postMessage({
        type: "read",
        url: tab.url,
        title: tab.title || "",
        html: content.html,
        text: content.text,
        markdown: content.markdown,
        options: options,
      });
    });
  } else {
    // No native host — return extracted content directly
    response = {
      type: "read_result",
      url: tab.url,
      title: tab.title || "",
      markdown: content.markdown,
      text: content.text,
      source: "extension_only",
    };
  }

  return response;
}

async function handleReadUrl(url, options = {}) {
  // For reading arbitrary URLs, we need the native host
  const port = connectToNativeHost();
  if (!port) {
    throw new Error("Native host not available. Please install webreader.");
  }

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error("Native host timeout"));
    }, options.timeout || 120000);

    port.onMessage.addListener(function listener(msg) {
      clearTimeout(timeout);
      port.onMessage.removeListener(listener);
      resolve(msg);
    });

    port.postMessage({
      type: "read_url",
      url: url,
      options: options,
    });
  });
}

// ─── Content Extraction Function (injected into pages) ───────────

// This function runs inside the webpage context via executeScript
function extractPageContent() {
  // Clone DOM and clean it up
  const clone = document.documentElement.cloneNode(true);

  // Remove clutter elements
  const removeSelectors = [
    'script', 'style', 'noscript', 'svg', 'canvas',
    '[aria-hidden="true"]',
    'nav', 'footer', 'header[role="banner"]',
    '[role="navigation"]',
    '[role="contentinfo"]',
    '.sidebar', '.ad', '.advertisement', '.cookie-banner',
    '#sidebar', '#comments', '#footer',
    '[data-testid="primarySidebar"]',
    '[data-testid="sidebar"]',
  ];

  removeSelectors.forEach(sel => {
    clone.querySelectorAll(sel).forEach(el => el.remove());
  });

  // Get raw HTML of cleaned clone
  const cleanHtml = clone.outerHTML;

  // Get plain text
  const text = document.body.innerText;

  // Simple Markdown conversion for basic elements
  let markdown = text; // Fallback

  // If we have time, do a better conversion here...
  // For now, the native host will do proper HTML→Markdown conversion
  
  // Extract structured info
  const title = document.title;
  const metaDesc = document.querySelector('meta[name="description"]')?.content || 
                   document.querySelector('meta[property="og:description"]')?.content || '';
  
  return {
    url: window.location.href,
    title: title,
    description: metaDesc,
    html: cleanHtml.substring(0, 500000), // Cap at 500KB
    text: text.substring(0, 200000),       // Cap at 200K chars
    markdown: markdown.substring(0, 200000),
  };
}
