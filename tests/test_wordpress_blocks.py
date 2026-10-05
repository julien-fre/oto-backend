"""Markdown → blocs Gutenberg : la forme exacte que chaque bloc cœur sauvegarde
(l'éditeur signale « contenu inattendu » sur le moindre écart)."""
from oto_mcp.tools.wordpress_blocks import markdown_to_blocks as md


def test_paragraph_softbreak_is_space():
    assert md("a\nb") == "<!-- wp:paragraph -->\n<p>a b</p>\n<!-- /wp:paragraph -->"


def test_heading_level_attr_only_when_not_h2():
    assert md("## T").startswith('<!-- wp:heading -->\n<h2 class="wp-block-heading">T</h2>')
    assert md("### T").startswith('<!-- wp:heading {"level":3} -->\n<h3 class="wp-block-heading">')


def test_lists_are_inner_list_item_blocks_nested():
    out = md("- a\n  - b\n")
    assert out.startswith('<!-- wp:list -->\n<ul class="wp-block-list"><!-- wp:list-item -->')
    assert '<li>a<!-- wp:list -->\n<ul class="wp-block-list"><!-- wp:list-item -->\n<li>b</li>' in out


def test_ordered_list_start():
    out = md("3. x\n4. y\n")
    assert out.startswith('<!-- wp:list {"ordered":true,"start":3} -->\n<ol class="wp-block-list" start="3">')


def test_code_is_escaped():
    assert '<code>a &lt; b</code>' in md("```\na < b\n```")


def test_quote_image_separator_table_html():
    assert '<blockquote class="wp-block-quote"><!-- wp:paragraph -->' in md("> q")
    assert '<figure class="wp-block-image"><img src="u" alt="a" /></figure>' in md("![a](u)")
    assert 'wp-block-separator' in md("---")
    t = md("| A | B |\n|---|:-:|\n| 1 | 2 |\n")
    assert t.startswith('<!-- wp:table -->\n<figure class="wp-block-table"><table class="has-fixed-layout"><thead>')
    assert "style=" not in t
    assert md('<div class="x">raw</div>') == '<!-- wp:html -->\n<div class="x">raw</div>\n<!-- /wp:html -->'


def test_empty():
    assert md("") == ""


def test_code_in_a_list_item_is_kept_not_dropped():
    # Un pas-à-pas avec sa commande : `core/list-item` ne porte que du texte et des
    # sous-listes, la liste entière part donc en bloc HTML — jamais sans son code.
    out = md("1. Step\n\n   ```bash\n   rm -rf x\n   ```\n2. Step two\n")
    assert out.startswith("<!-- wp:html -->") and out.endswith("<!-- /wp:html -->")
    assert "rm -rf x" in out and "Step two" in out


def test_table_and_quote_in_a_list_item_are_kept():
    table = md("- item\n\n  | A | B |\n  |---|---|\n  | 1 | 2 |\n")
    assert "<table>" in table and "<td>1</td>" in table
    quote = md("- item\n\n  > cited\n")
    assert "<blockquote>" in quote and "cited" in quote


def test_nested_list_with_code_keeps_the_whole_top_list():
    out = md("- a\n  - b\n\n    ```\n    code\n    ```\n- c\n")
    assert out.count("<!-- wp:html -->") == 1 and "code" in out and "<li>c</li>" in out
