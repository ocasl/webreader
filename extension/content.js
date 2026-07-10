/**
 * webreader Content Script
 * Injected into every page. Provides page reading capabilities.
 */

(function() {
  'use strict';

  // Signal to the extension that we're loaded
  // This helps with debugging and status checks
  window.dispatchEvent(new CustomEvent('webreader_loaded', {
    detail: { version: '0.1.0' }
  }));

  // Listen for messages from popup/background
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === 'get_page_content') {
      const content = extractContent();
      sendResponse(content);
    } else if (message.action === 'get_selection') {
      sendResponse({
        text: window.getSelection().toString(),
        html: getSelectionHtml(),
      });
    }
    return true;
  });

  /**
   * Extract clean page content.
   */
  function extractContent() {
    // Get main article area if possible
    let mainEl = document.querySelector(
      'article, [role="main"], main, .post-content, .article-body, .entry-content, #content'
    );

    if (!mainEl || mainEl.innerText.length < 200) {
      mainEl = document.body;
    }

    // Clean clone
    const clone = mainEl.cloneNode(true);

    // Remove clutter
    clone.querySelectorAll(`
      script, style, noscript, svg, canvas, iframe,
      [aria-hidden="true"],
      nav, footer, header:not([role="banner"]):not(:first-child),
      [class*="sidebar"], [class*="ad-"], 
      [id*="comment"], [id*="footer"], [id*="sidebar"],
      [data-testid*="sidebar"]
    `).forEach(el => el.remove());

    return {
      url: location.href,
      title: document.title,
      text: clone.innerText.trim(),
      html: clone.innerHTML.substring(0, 300000),
      wordCount: (clone.innerText.match(/\S+/g) || []).length,
    };
  }

  /**
   * Get selected HTML.
   */
  function getSelectionHtml() {
    const sel = window.getSelection();
    if (!sel.rangeCount) return '';
    
    const container = document.createElement('div');
    for (let i = 0; i < sel.rangeCount; i++) {
      container.appendChild(sel.getRangeAt(i).cloneContents());
    }
    return container.innerHTML;
  }

})();
