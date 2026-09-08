#!/usr/bin/env python3
"""
test_markdown.py — the docs renderer.

WHAT IS ACTUALLY AT STAKE. This turns .md files into HTML that is served from
the same origin as the app, inside a session. So the question is not "does the
formatting look right" but "can anything in a document become executable".

Most of the assertions below are therefore about ABSENCE: no raw tag, no raw
quote inside an attribute, no link to a scheme that is not http or https.

THE THREAT MODEL, stated honestly. Documents are files on the monitor's own
filesystem, so putting a hostile one there already requires access that would
be worse news than an XSS. That does not make escaping optional: this renderer
ships to other operators, and the moment a document arrives from anywhere less
trusted, incomplete escaping becomes immediately exploitable. The property is
cheap to hold and expensive to notice missing.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import xxops_md as md


class Escaping(unittest.TestCase):

    def test_a_tag_in_the_source_does_not_survive_as_a_tag(self):
        out = md.md_to_html("Hello <script>alert(1)</script> there")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_ampersands_are_escaped_before_brackets(self):
        """
        Order matters. Escaping < before & would turn &lt; into &amp;lt; and
        show the entity to the reader instead of the character.
        """
        self.assertEqual(md.md_escape("a & b < c"), "a &amp; b &lt; c")
        self.assertNotIn("&amp;lt;", md.md_escape("<"))

    def test_a_quote_cannot_break_out_of_an_href(self):
        """
        THE ONE THAT MATTERS MOST HERE.

        The link rule drops a captured URL straight into href="...", after
        escaping. If escaping does not cover the double quote, a URL can close
        the attribute and open another one:

            [x](https://evil.net"onmouseover=alert(1))

        which renders an anchor carrying an event handler. No space is needed,
        so the URL pattern's own whitespace ban does not prevent it.
        """
        out = md.md_inline('[x](https://evil.net"onmouseover=alert(1))')
        self.assertNotIn('"onmouseover', out,
                         "a quote escaped the href and started a new attribute")
        self.assertIn("&quot;", out)

    def test_a_bare_quote_in_a_url_is_escaped(self):
        out = md.md_inline('[x](https://evil.net")')
        self.assertNotIn('net""', out)

    def test_code_blocks_escape_their_contents(self):
        out = md.md_to_html("```\n<b>not bold</b>\n```")
        self.assertIn("&lt;b&gt;", out)
        self.assertNotIn("<b>not bold", out)

    def test_headings_escape_their_contents(self):
        out = md.md_to_html("# <img src=x onerror=alert(1)>")
        self.assertNotIn("<img", out)

    def test_table_cells_escape_their_contents(self):
        out = md.md_to_html("| a | b |\n| - | - |\n| <i>x</i> | y |")
        self.assertNotIn("<i>x</i>", out)
        self.assertIn("&lt;i&gt;", out)

    def test_list_items_escape_their_contents(self):
        out = md.md_to_html("- <b>x</b>")
        self.assertIn("&lt;b&gt;", out)

    def test_the_page_title_is_escaped(self):
        out = md.doc_page("</title><script>alert(1)</script>", "<p>hi</p>")
        self.assertNotIn("<script>", out)


class Links(unittest.TestCase):

    def test_an_ordinary_link_works(self):
        out = md.md_inline("see [the docs](https://example.net/x)")
        self.assertIn('href="https://example.net/x"', out)
        self.assertIn(">the docs</a>", out)

    def test_links_open_safely(self):
        """target=_blank without rel=noopener hands the opener to the target."""
        out = md.md_inline("[x](https://example.net)")
        self.assertIn('rel="noopener"', out)

    def test_a_javascript_url_is_not_linked(self):
        """Only http and https are matched, so this stays inert text."""
        out = md.md_inline("[x](javascript:alert(1))")
        self.assertNotIn("<a ", out)

    def test_a_data_url_is_not_linked(self):
        out = md.md_inline("[x](data:text/html,<script>alert(1)</script>)")
        self.assertNotIn("<a ", out)

    def test_an_ampersand_in_a_query_string_survives_as_an_entity(self):
        out = md.md_inline("[x](https://example.net/a?b=1&c=2)")
        self.assertIn("b=1&amp;c=2", out)


class Formatting(unittest.TestCase):
    """Enough markdown for the documents this project writes, and no more."""

    def test_bold_italic_and_code(self):
        out = md.md_inline("**b** and *i* and `c`")
        self.assertIn("<strong>b</strong>", out)
        self.assertIn("<em>i</em>", out)
        self.assertIn("<code>c</code>", out)

    def test_a_lone_star_stays_literal(self):
        """A shell glob on its own must not open a tag that never closes."""
        self.assertNotIn("<em>", md.md_inline("rm -rf build/*.o"))
        self.assertNotIn("<em>", md.md_inline("a star * by itself"))

    def test_a_star_touching_a_word_does_not_open_italics(self):
        """The lookbehind exists for this: 2*3 and x*y are not markup."""
        self.assertNotIn("<em>", md.md_inline("2*3 = 6"))

    def test_known_limitation_two_stars_on_a_line_italicise_between_them(self):
        """
        RECORDING ACTUAL BEHAVIOUR, NOT ENDORSING IT.

        The italic rule pairs the first eligible star with the next one, so a
        line containing two unrelated stars - a glob and an arithmetic
        expression, say - renders the text between them as italic.

        It is cosmetic: nothing becomes executable, the document still renders,
        and putting either fragment in backticks avoids it. Fixing it properly
        means a real inline parser rather than a regex, which is a larger
        change than the problem deserves.

        This test exists so the behaviour is known rather than discovered, and
        so that changing it is deliberate.
        """
        out = md.md_inline("rm -rf build/*.o and 3 * 4")
        self.assertIn("<em>", out)

    def test_headings_at_each_level(self):
        for n in range(1, 7):
            out = md.md_to_html("#" * n + " Title")
            self.assertIn("<h%d>Title</h%d>" % (n, n), out)

    def test_bullets_and_numbers(self):
        self.assertIn("<ul><li>one</li><li>two</li></ul>",
                      md.md_to_html("- one\n- two"))
        self.assertIn("<ol><li>one</li><li>two</li></ol>",
                      md.md_to_html("1. one\n2. two"))

    def test_a_table_renders(self):
        out = md.md_to_html("| a | b |\n| - | - |\n| 1 | 2 |")
        self.assertIn("<th>a</th>", out)
        self.assertIn("<td>2</td>", out)

    def test_a_horizontal_rule(self):
        self.assertIn("<hr>", md.md_to_html("---"))

    def test_paragraphs_join_wrapped_lines(self):
        out = md.md_to_html("one\ntwo\n\nthree")
        self.assertIn("<p>one two</p>", out)
        self.assertIn("<p>three</p>", out)

    def test_unrecognised_markup_falls_through_rather_than_breaking(self):
        """A document that renders imperfectly beats one that renders blank."""
        out = md.md_to_html("> a quote\n\n![img](x.png)")
        self.assertTrue(out.strip())

    def test_windows_line_endings(self):
        self.assertIn("<h1>Title</h1>", md.md_to_html("# Title\r\n\r\ntext"))

    def test_an_empty_document_does_not_crash(self):
        self.assertEqual(md.md_to_html(""), "")


class DocNames(unittest.TestCase):

    def test_it_accepts_ordinary_names(self):
        for good in ("install-guide.md", "a.md", "x_y.z.md"):
            self.assertTrue(md.DOC_NAME.fullmatch(good), good)

    def test_it_refuses_traversal_and_odd_names(self):
        """
        This pattern is what stands between a docs URL and the filesystem.
        Slashes and dot-dot must not match.
        """
        for bad in ("../etc/passwd", "a/b.md", "..md.md/../x", "A.md",
                    "no-extension", "x.md.txt", ""):
            self.assertIsNone(md.DOC_NAME.fullmatch(bad), "accepted %r" % bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
