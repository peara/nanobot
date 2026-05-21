from __future__ import annotations

from web_agent.extractors.content import heuristic_extract

# heuristic_extract requires 40+ words in a candidate node, so test HTML
# must include enough paragraph text to pass the scoring threshold.


def test_heuristic_extract_strips_style_tags():
    """Regression: <style> content must not leak into extracted text."""
    html = (
        "<html><body><div><style>.theme-light{color:red;}</style>"
        "<p>This is readable text that should be extracted by the heuristic "
        "extractor function when processing a web page with style tags present "
        "in the document structure and layout.</p>"
        "<p>Additional paragraph content to ensure enough words are present "
        "for the heuristic scoring algorithm to select this div as the best "
        "content container on the page.</p></div></body></html>"
    )
    result = heuristic_extract(html)
    assert ".theme-light" not in result
    assert "readable text" in result


def test_heuristic_extract_strips_script_tags():
    html = (
        "<html><body><div><script>var x = 1; console.log('hello');</script>"
        "<p>Article content that discusses important topics in depth and "
        "provides detailed analysis of the subject matter at hand with "
        "comprehensive coverage of all relevant aspects.</p>"
        "<p>Further elaboration on the key points raised in the preceding "
        "paragraph to reinforce the main arguments and provide supporting "
        "evidence for the claims being made throughout this piece.</p></div></body></html>"
    )
    result = heuristic_extract(html)
    assert "console.log" not in result
    assert "Article content" in result


def test_heuristic_extract_strips_noscript_tags():
    html = (
        "<html><body><div><noscript>Please enable JavaScript</noscript>"
        "<p>Real content paragraph that provides meaningful information "
        "to readers who visit this webpage looking for substantive details "
        "about the topic being covered in this article section here.</p>"
        "<p>More real content in the second paragraph to push the word "
        "count above the minimum threshold required by the extractor "
        "to consider this node as a viable content candidate.</p></div></body></html>"
    )
    result = heuristic_extract(html)
    assert "enable JavaScript" not in result
    assert "Real content" in result


def test_heuristic_extract_reddit_css_regression():
    """Regression guard: massive CSS variable blocks must not appear in output."""
    css_block = ".theme-light,:root{--rem360:22.5rem;--rem320:20rem;--spacer-a-px:0px;}"
    html = (
        f"<html><body><div><style>{css_block}</style>"
        "<p>Discussion about local LLMs and their applications in everyday "
        "use cases including code generation, text summarization, and "
        "question answering tasks that demonstrate practical utility.</p>"
        "<p>Further discussion about the benefits of running models locally "
        "on consumer hardware with sufficient memory and compute capacity "
        "for inference workloads of varying size and complexity.</p></div></body></html>"
    )
    result = heuristic_extract(html)
    assert "--rem360" not in result
    assert "local LLMs" in result


def test_heuristic_extract_returns_text_from_normal_page():
    html = (
        "<html><body><article>"
        "<p>First paragraph of article content that introduces the main "
        "topic and provides context for what follows in subsequent paragraphs "
        "of this detailed and informative article about technology.</p>"
        "<p>Second paragraph with more details about the subject including "
        "specific examples and case studies that illustrate the key points "
        "being discussed throughout this comprehensive piece of writing.</p>"
        "</article></body></html>"
    )
    result = heuristic_extract(html)
    assert "First paragraph" in result
    assert "Second paragraph" in result