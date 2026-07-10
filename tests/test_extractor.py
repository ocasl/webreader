"""Basic tests for webreader core functionality."""

import pytest
from webreader.extractor import (
    html_to_markdown,
    _cleanup_markdown,
    extract_main_content,
    extract_structured_data,
)


class TestExtractor:
    """Test HTML → Markdown extraction."""

    def test_simple_paragraph(self):
        html = "<p>Hello <b>world</b></p>"
        md = html_to_markdown(html)
        assert "Hello" in md
        assert "**world**" in md or "world" in md

    def test_heading_conversion(self):
        html = "<h1>Title</h1><h2>Subtitle</h2>"
        md = html_to_markdown(html)
        assert "# Title" in md
        assert "## Subtitle" in md

    def test_link_conversion(self):
        html = '<a href="https://example.com">Click here</a>'
        md = html_to_markdown(html)
        assert "[Click here]" in md or "Click here" in md

    def test_code_block(self):
        html = '<pre><code>print("hello")</code></pre>'
        md = html_to_markdown(html)
        assert "```" in md or 'print("hello")' in md

    def test_list_conversion(self):
        html = "<ul><li>One</li><li>Two</li></ul>"
        md = html_to_markdown(html)
        assert "- One" in md
        assert "- Two" in md

    def test_script_removal(self):
        html = '<p>Content</p><script>alert("xss")</script><p>More</p>'
        md = html_to_markdown(html)
        assert "Content" in md
        assert "alert" not in md
        assert "xss" not in md

    def test_image_conversion(self):
        html = '<img src="photo.jpg" alt="A photo">'
        md = html_to_markdown(html)
        assert "![" in md and "photo.jpg" in md

    def test_cleanup_excessive_blank_lines(self):
        text = "\n\n\n\nHello\n\n\n\n"
        result = _cleanup_markdown(text)
        assert result.count("\n\n") <= 2

    def test_cleanup_long_garbage_lines(self):
        text = f"{'a' * 1000}\nNormal line here."
        result = _cleanup_markdown(text)
        assert "Normal" in result


class TestStructuredExtraction:

    def test_extract_title_from_md(self):
        md = "# My Article\n\nSome content here."
        data = extract_structured_data(md)
        assert data["title"] == "My Article"

    def test_word_count(self):
        md = "Hello world, this is a test."
        data = extract_structured_data(md)
        assert data["word_count"] == 6

    def test_chinese_detection(self):
        md = "# 测试文章\n\n这是一篇中文文章。"
        data = extract_structured_data(md)
        assert data["language_hint"] == "zh-CN"


class TestMainContentExtraction:

    def test_article_tag(self):
        html = '<nav>Skip this</nav><article>Main content goes here with enough characters to pass the threshold</article><footer>Ignore</footer>'
        result = extract_main_content(html)
        assert "Main content" in result
        assert "Skip this" not in result
        assert "Ignore" not in result

    def test_no_article_falls_back_to_body(self):
        html = '<body><p>Body content here with enough length to be considered substantial content for extraction purposes</p></body>'
        result = extract_main_content(html)
        assert "Body content" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
