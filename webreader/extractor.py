"""
HTML to Markdown converter with smart content extraction.
Falls back gracefully when markdownify is unavailable.
"""

from __future__ import annotations

import html
import re
from typing import Optional
from urllib.parse import urlparse, urljoin


def html_to_markdown(raw_html: str, url: str = "") -> str:
    """
    Convert HTML content to clean Markdown.
    
    Uses markdownify if available, otherwise falls back to built-in regex-based converter.
    """
    # Try markdownify first (better quality)
    try:
        from markdownify import MarkdownConverter

        # Pre-cleanup: remove script/style/noscript entirely (including inner content)
        # markdownify's `strip` only skips conversion but still outputs the text inside
        clean_html = re.sub(
            r'(?is)<(?:script|style|noscript)[^>]*>.*?(?:</(?:script|style|noscript)>|$)',
            '', raw_html
        )

        # Custom converter for cleaner output
        converter = MarkdownConverter(
            heading_style="ATX",
            bullets="-",
            strip=["svg"],  # Only strip non-content elements now
            escape_asterisks=False,
            escape_underscores=False,
        )
        md = converter.convert(clean_html)
        
    except ImportError:
        md = _regex_html_to_markdown(raw_html)

    # Post-processing cleanup (always apply)
    # Remove any residual script/style content that converters may have missed
    # markdownify's `strip` only skips conversion, it still outputs inner text
    md = re.sub(
        r'(?is)<(?:script|style|noscript)[^>]*>.*?(?:</(?:script|style|noscript)>|$)',
        '', md
    )
    # Also catch any leftover raw JS that leaked through (common with markdownify)
    for js_pattern in [r'(?i)\balert\s*\(', r'(?i)\bdocument\.write\s*\(']:
        # Only remove if it looks like orphaned code, not legitimate discussion of these terms
        lines = md.split('\n')
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if re.search(js_pattern, stripped) and (
                '{' in stripped or ';' in stripped or stripped.count('(') > 1
            ):
                continue
            cleaned_lines.append(line)
        md = '\n'.join(cleaned_lines)

    md = _cleanup_markdown(md, url)
    
    return md


def _regex_html_to_markdown(html_content: str) -> str:
    """Fallback HTML→Markdown using regex (no external deps)."""
    text = html_content
    
    # Remove script, style, noscript blocks
    text = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove HTML comments
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    
    # Headings
    for i in range(6, 0, -1):
        text = re.sub(rf'<h{i}[^>]*>(.*?)</h{i}>', rf'\n{"#" * i} \1\n', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Paragraphs and line breaks
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<div[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
    
    # Bold / Italic
    text = re.sub(r'<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<(?:em|i)[^>]*>(.*?)</(?:em|i)>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Links: [text](url)
    def _link_replace(m):
        link_text = m.group(1) or ""
        href = m.group(2) or ""
        if link_text == href or not link_text:
            return f"<{href}>"
        return f"[{link_text}]({href})"
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', _link_replace, text, flags=re.DOTALL | re.IGNORECASE)
    
    # Lists
    text = re.sub(r'<li[^>]*>', '- ', text, flags=re.IGNORECASE)
    
    # Code blocks
    text = re.sub(r'<pre[^>]*><code[^>]*>', '```\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</code></pre>', '\n```', text, flags=re.IGNORECASE)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Blockquotes
    text = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', lambda m: '\n' + '\n'.join(f'> {line}' for line in m.group(1).strip().split('\n')) + '\n', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Images: ![alt](url)
    text = re.sub(r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*/?>', r'![\2](\1)', text, flags=re.IGNORECASE)
    text = re.sub(r'<img[^>]*src="([^"]*)"[^>]*/?>', r'![](\1)', text, flags=re.IGNORECASE)
    
    # HR
    text = re.sub(r'<hr\s*/?\s*>', '\n---\n', text, flags=re.IGNORECASE)
    
    # Decode HTML entities
    text = html.unescape(text)
    
    # Strip remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    
    return text.strip()


def _cleanup_markdown(md: str, base_url: str = "") -> str:
    """Post-process cleanup on generated Markdown."""
    lines = md.split("\n")
    cleaned = []
    
    for line in lines:
        stripped = line.strip()
        
        # Skip empty lines that follow another empty line
        if not stripped and cleaned and not cleaned[-1].strip():
            continue
        
        # Skip very long single-word lines (likely garbage)
        if len(stripped) > 500 and " " not in stripped:
            continue
        
        # Collapse excessive whitespace
        collapsed = re.sub(r'[ \t]+', ' ', stripped)
        cleaned.append(collapsed)

    result = "\n".join(cleaned)
    
    # Collapse 3+ blank lines into 2
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    # Remove leading/trailing whitespace per line
    result = "\n".join(line.rstrip() for line in result.split("\n"))
    
    # Truncate absurdly long output (guardrail)
    if len(result) > 2_000_000:
        result = result[:2_000_000] + f"\n\n... (truncated, total {len(result):,} chars)"

    return result.strip()


def extract_main_content(html_content: str) -> str:
    """
    Extract only the main article/content area from a page.
    Removes navbars, sidebars, footers, ads, etc.
    """
    # Heuristic: look for common main content selectors
    import re
    
    # Try to find the largest content block
    patterns = [
        r'<article[^>]*>(.*?)</article>',
        r'(?i)<div[^>]*class="[^"]*(?:content|article|post|main|entry|body-text)[^"]*"[^>]*>(.*?)</div>',
        r'(?i)<div[^>]*id="[^"]*(?:content|article|post|main|entry|body-text)[^"]*"[^>]*>(.*?)</div>',
        r'(?i)<section[^>]*(?:role="?main"?|class="[^"]*main[^"]*")[^>]*>(.*?)</section>',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html_content, re.DOTALL)
        if match:
            content = match.group(1)
            # Only use if it's substantial (>50 chars)
            if len(content) > 50:
                return content

    # Fallback: try <body> minus obvious clutter
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL)
    if body_match:
        body = body_match.group(1)
        # Remove common clutter elements (escape attribute quotes properly)
        for selector_pattern in [
            r'nav', r'footer', r'header',
            r'\[role\s*=\s*["\']?navigation["\']?\]',
            r'\[role\s*=\s*["\']?banner["\']?\]',
            r'\[role\s*=\s*["\']?contentinfo["\']?\]',
        ]:
            body = re.sub(
                rf'<{selector_pattern}[^>]*>.*?</{selector_pattern}>',
                '', body, flags=re.DOTALL | re.IGNORECASE
            )
        # Remove scripts/styles
        body = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', body, flags=re.DOTALL | re.IGNORECASE)
        return body

    return html_content


def extract_structured_data(markdown: str, url: str = "") -> dict:
    """Extract structured metadata from a page's markdown content."""
    # Try to extract title from first heading
    title_match = re.match(r'^#\s+(.+)$', markdown, re.MULTILINE)
    title = title_match.group(1) if title_match else ""

    # Estimate word count
    words = len(markdown.split())
    
    # Extract links
    links = re.findall(r'\[([^\]]*)\]\(([^)]+)\)', markdown)
    
    # Detect language (simple heuristic)
    has_chinese = bool(re.search(r'[\u4e00-\u9fff]', markdown))
    
    return {
        "title": title,
        "word_count": words,
        "char_count": len(markdown),
        "link_count": len(links),
        "has_code_blocks": "```" in markdown,
        "has_images": "![" in markdown,
        "language_hint": "zh-CN" if has_chinese else "en",
        "source_domain": urlparse(url).netloc if url else "",
    }
