from __future__ import annotations

from web_agent.scorers.content import looks_like_code, score_content, stopword_density, symbol_ratio

REAL_ARTICLE = """
Artificial intelligence has transformed the way we think about computing. In recent years,
researchers have made significant advances in natural language processing, computer vision, and
reinforcement learning. These developments have led to practical applications in healthcare,
education, and transportation. The field continues to evolve rapidly, with new breakthroughs
being reported on a regular basis. Many experts believe that we are still in the early stages
of understanding what these systems can achieve.
"""

CSS_BLOB = """.theme-light,:root{--rem360:22.5rem;--rem320:20rem;--rem192:12rem;--rem144:9rem;
--rem128:8rem;--rem96:6rem;--rem90:5.625rem;--rem88:5.5rem;--rem64:4rem;--rem56:3.5rem;
--rem48:3rem;--spacer-a-px:0px;--spacer-button-lg-px:var(--spacer-lg);
--spacer-button-md-px:var(--spacer-lg);position:relative;display:flex;}"""

JS_BLOB = """function handleClick(event) {
    var target = event.target;
    if (target.classList.contains('button')) {
        const result = document.querySelector('.result');
        window.scrollTo({top: 0, behavior: 'smooth'});
        console.log('Button clicked:', target.id);
        return typeof result !== 'undefined';
    }
}"""


def test_score_real_article():
    score = score_content(REAL_ARTICLE)
    assert score >= 0.48, f"Real article scored {score:.3f}, expected >= 0.48"


def test_score_css_content():
    score = score_content(CSS_BLOB)
    assert score < 0.1, f"CSS blob scored {score:.3f}, expected < 0.1"


def test_score_js_content():
    score = score_content(JS_BLOB)
    assert score < 0.1, f"JS blob scored {score:.3f}, expected < 0.1"


def test_score_nav_boilerplate():
    nav = "Home Services Products Support FAQ Login Signup Dashboard Settings Account Preferences"
    score = score_content(nav)
    assert score < 0.4, f"Nav boilerplate scored {score:.3f}, expected < 0.4"


def test_score_short_quality():
    short = "The government announced new policies today that will affect trade relations between the two countries."
    score = score_content(short)
    assert score > 0.2, f"Short quality text scored {score:.3f}, expected > 0.2"


def test_score_non_english_like():
    nonsense = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore"
    score = score_content(nonsense)
    assert score < 0.2, f"Nonsense text scored {score:.3f}, expected < 0.2"


def test_score_mixed_content():
    mixed = """
    In this tutorial we will explain how to implement a binary search algorithm.
    The key insight is that the array must be sorted first. Consider this example:
    ```python
    def binary_search(arr, target):
        left, right = 0, len(arr) - 1
        while left <= right:
            mid = (left + right) // 2
            if arr[mid] == target:
                return mid
            elif arr[mid] < target:
                left = mid + 1
            else:
                right = mid - 1
        return -1
    ```
    As you can see, the algorithm reduces the search space by half each time.
    This gives us a time complexity of O(log n), which is much better than linear search.
    """
    score = score_content(mixed)
    assert score >= 0.48, f"Mixed content scored {score:.3f}, expected >= 0.48"


def test_score_empty():
    assert score_content("") == 0.0
    assert score_content("   ") == 0.0


def test_stopword_density_values():
    assert stopword_density("") == 0.0
    article_density = stopword_density(REAL_ARTICLE)
    assert 0.25 < article_density < 0.55, f"Article stopword density {article_density:.3f}"
    nav_density = stopword_density("Home Services Products Support FAQ Login Signup Dashboard")
    assert nav_density < 0.15, f"Nav stopword density {nav_density:.3f}"


def test_symbol_ratio_values():
    assert symbol_ratio("") == 0.0
    article_sym = symbol_ratio(REAL_ARTICLE)
    assert article_sym < 0.1, f"Article symbol ratio {article_sym:.3f}"
    css_sym = symbol_ratio(CSS_BLOB)
    assert css_sym > 0.15, f"CSS symbol ratio {css_sym:.3f}"


def test_looks_like_code():
    assert looks_like_code(CSS_BLOB) is True
    assert looks_like_code(JS_BLOB) is True
    assert looks_like_code(REAL_ARTICLE) is False


def test_blog_about_code_scores_above_threshold():
    css_blog = """
    I love using flexbox for layouts. To center a div, you can set display: flex on the parent
    container and use margin: 0 auto on the child. The position: relative property is useful for
    creating positioning contexts. One thing to remember is that !important should be used sparingly,
    as it overrides all other specificity rules. When debugging layout issues, I often check the
    display: block property to understand element behavior. The padding: 10px and margin: 5px
    shorthands are convenient for quick adjustments.
    """
    score = score_content(css_blog)
    assert score >= 0.48, f"Blog about CSS scored {score:.3f}, expected >= 0.48 (prose discussing code, not raw code)"
    assert looks_like_code(css_blog) is True, "Blog about CSS triggers looks_like_code"
    assert stopword_density(css_blog) > 0.20, "Blog about CSS has natural language stopwords"


def test_link_heavy_content():
    link_text = "Visit https://example.com for more info at https://example.org or https://example.net home page."
    prose_score = score_content(REAL_ARTICLE)
    link_score = score_content(link_text)
    assert link_score < prose_score, f"Link-heavy {link_score:.3f} should score below prose {prose_score:.3f}"


def test_long_quality_article():
    long_text = REAL_ARTICLE * 5
    score = score_content(long_text)
    assert score >= 0.48, f"Long article scored {score:.3f}, expected >= 0.48"
