import unittest
from crawler import fetch_page, parse_html, crawl, print_cli_output
from utils import classify_link, normalize_url
from sitemap import SitemapManager
from unittest.mock import patch
from urllib.parse import urljoin
import io
import sys
import logging

class TestCrawler(unittest.TestCase):

    def test_fetch_page_success(self):
        result = fetch_page("http://example.com")
        self.assertIsNotNone(result)

    def test_fetch_page_failure(self):
        result = fetch_page("http://invalid-url")
        self.assertIsNone(result)

    def test_parse_html(self):
        html = "<html><body><h1>Test</h1><a href='/internal'>Internal Link</a><a href='http://external.com'>External Link</a></body></html>"
        soup = parse_html(html)
        self.assertEqual(soup.find('h1').text, "Test")
        self.assertEqual(len(soup.find_all('a')), 2)

    def test_classify_link_internal(self):
        base_url = "http://example.com"
        link_url = "/internal"
        result = classify_link(base_url, link_url)
        self.assertEqual(result, "internal")

    def test_classify_link_external(self):
        base_url = "http://example.com"
        link_url = "http://external.com"
        result = classify_link(base_url, link_url)
        self.assertEqual(result, "external")

    def test_sitemap_manager(self):
        sitemap = SitemapManager("http://example.com")  # pass base_url here
        base_url = "http://example.com"
        internal_url = "/internal"
        external_url = "http://external.com"

        # The seed URL should be added and marked as visited first
        sitemap.add_url(base_url, base_url)
        sitemap.mark_visited(base_url)

        sitemap.add_url(base_url, external_url)
        sitemap.add_external_edge(base_url, external_url)
        self.assertEqual(len(sitemap.external_edges), 1)

        sitemap.add_url(base_url, internal_url)
        self.assertEqual(len(sitemap.unvisited_urls), 2)

        next_url = sitemap.get_next_url()
        self.assertEqual(next_url, "http://example.com/internal")

        sitemap.mark_visited(next_url)
        self.assertEqual(len(sitemap.visited_urls), 2)
        self.assertEqual(len(sitemap.unvisited_urls), 1) # internal url has been visited, external url remains
        self.assertEqual(len(sitemap.external_links), 0)

    def test_crawl_success(self):
        logging.info("Starting test_crawl_success")
        sitemap = SitemapManager("http://example.com")  # pass base_url here
        sitemap.add_url("http://example.com", "http://example.com")
        # Mocking fetch_page to avoid actual network requests
        with patch('crawler.fetch_page', return_value="<html><body><h1>Test</h1></body></html>"):
            sitemap = crawl("http://example.com", sitemap, "http://example.com", max_pages=1)
        self.assertEqual(len(sitemap.visited_urls), 1)
        logging.info("Completed test_crawl_success")

    def test_crawl_left_hand_rule(self):
        logging.info("Starting test_crawl_left_hand_rule")
        sitemap = SitemapManager("http://example.com")  # pass base_url
        base_url = "http://example.com"

        def mock_fetch_page(url):
            if url == base_url:
                return """
                <!DOCTYPE html>
                <html>
                <head><title>React - A JavaScript library for building user interfaces</title></head>
                <body>
                    <header>
                        <nav>
                            <a href='/docs/getting-started.html'>Getting Started</a>
                            <a href='/docs/tutorial.html'>Tutorial</a>
                        </nav>
                    </header>
                    <main>
                        <article>
                            <h1>React</h1>
                            <p>React is a declarative, efficient, and flexible JavaScript library for building user interfaces.</p>
                            <a href='/docs/components-and-props.html'>Components and Props</a>
                        </article>
                    </main>
                    <footer>
                        <a href='/community/support.html'>Support</a>
                    </footer>
                </body>
                </html>
                """
            elif url == urljoin(base_url, "docs/getting-started.html"):
                return """
                <!DOCTYPE html>
                <html>
                <head><title>Getting Started - React</title></head>
                <body>
                    <main>
                        <article>
                            <h1>Getting Started</h1>
                            <p>This page will help you get started with React.</p>
                            <a href='/docs/installation.html'>Installation</a>
                        </article>
                    </main>
                </body>
                </html>
                """
            elif url == urljoin(base_url, "docs/tutorial.html"):
                return """
                <!DOCTYPE html>
                <html>
                <head><title>Tutorial - React</title></head>
                <body>
                    <main>
                        <article>
                            <h1>React Tutorial</h1>
                            <p>Follow this tutorial to learn React.</p>
                            <a href='/docs/thinking-in-react.html'>Thinking in React</a>
                        </article>
                    </main>
                </body>
                </html>
                """
            elif url == urljoin(base_url, "docs/components-and-props.html"):
                return """
                <!DOCTYPE html>
                <html>
                <head><title>Components and Props - React</title></head>
                <body>
                    <main>
                        <article>
                            <h1>Components and Props</h1>
                            <p>Learn about Components and Props in React.</p>
                        </article>
                    </main>
                </body>
                </html>
                """
            elif url == urljoin(base_url, "docs/installation.html"):
                return """
                <!DOCTYPE html>
                <html>
                <head><title>Installation - React</title></head>
                <body>
                    <main>
                        <article>
                            <h1>Installation</h1>
                            <p>Learn how to install React.</p>
                        </article>
                    </main>
                </body>
                </html>
                """
            elif url == urljoin(base_url, "docs/thinking-in-react.html"):
                return """
                <!DOCTYPE html>
                <html>
                <head><title>Thinking in React - React</title></head>
                <body>
                    <main>
                        <article>
                            <h1>Thinking in React</h1>
                            <p>Learn how to think in React.</p>
                        </article>
                    </main>
                </body>
                </html>
                """
            elif url == urljoin(base_url, "community/support.html"):
                return """
                <!DOCTYPE html>
                <html>
                <head><title>React Community Support</title></head>
                <body>
                    <main>
                        <article>
                            <h1>React Community Support</h1>
                            <p>Get help from the React community.</p>
                        </article>
                    </main>
                </body>
                </html>
                """
            else:
                return None

        with patch('crawler.fetch_page', side_effect=mock_fetch_page):
            crawl(base_url, sitemap, base_url, max_pages=1)

        expected_visited_urls = {base_url}
        self.assertEqual(sitemap.visited_urls, expected_visited_urls)
        logging.info("Completed test_crawl_left_hand_rule")

    def test_crawl_strict_url(self):
        logging.info("Starting test_crawl_strict_url")
        sitemap = SitemapManager("http://example.com")  # pass base_url here
        base_url = "http://example.com"

        def mock_fetch_page(url):
            if url == base_url:
                return "<html><body><h1>Test</h1><a href='/internal'>Internal Link</a></body></html>"
            elif url == urljoin(base_url, "internal"):
                return "<html><body><h1>Internal</h1></body></html>"
            else:
                return None

        with patch('crawler.fetch_page', side_effect=mock_fetch_page):
            crawl(base_url, sitemap, base_url, max_pages=1)

        expected_visited_urls = {base_url}
        self.assertEqual(sitemap.visited_urls, expected_visited_urls)
        logging.info("Completed test_crawl_strict_url")

    def test_crawl_duplicate_content(self):
        logging.info("Starting test_crawl_duplicate_content")
        sitemap = SitemapManager("http://example.com")  # pass base_url here
        base_url = "http://example.com"

        def mock_fetch_page(url):
            return "<html><body><h1>Test</h1></body></html>"

        with patch('crawler.fetch_page', side_effect=mock_fetch_page):
            sitemap.add_url(base_url, base_url + "/page1")
            sitemap.add_url(base_url, base_url + "/page2")
            crawl(base_url + "/page1", sitemap, base_url, max_pages=1)
            crawl(base_url + "/page2", sitemap, base_url, max_pages=1)

        self.assertEqual(len(sitemap.visited_urls), 2) # Should be 1 since the second URL is skipped due to duplicate content
        # Expect one unique page content saved
        self.assertEqual(len(sitemap.page_contents), 1)
        logging.info("Completed test_crawl_duplicate_content")

    def test_crawl_respect_scope(self):
        """Test that crawl respects the scope URL limit"""
        sitemap = SitemapManager('http://example.com')
        scope_url = 'http://example.com/docs'
        
        def mock_fetch_page(url):
            return "<html><body><a href='/outside'>Outside Link</a></body></html>"
        
        with patch('crawler.fetch_page', side_effect=mock_fetch_page):
            crawl('http://example.com/docs/page', sitemap, 'http://example.com', scope_url)
            # Should not follow the /outside link as it's outside scope
            self.assertNotIn('http://example.com/outside', sitemap.visited_urls)

    @patch('sys.stdout', new_callable=io.StringIO)
    def test_print_cli_output(self, mock_stdout):
        sitemap = SitemapManager()
        sitemap.mapped_count = 1
        sitemap.unmapped_count = 0
        print_cli_output(sitemap)
        output = mock_stdout.getvalue()
        self.assertEqual(output, "\rMapped: 1  Unmapped: 0")

class TestNormalizeUrl(unittest.TestCase):
    from utils import normalize_url

    def test_remove_fragment(self):
        url = "http://example.com/docs#getting-started"
        expected_url = "http://example.com/docs"
        self.assertEqual(normalize_url(url), expected_url)

    def test_remove_tracking_parameters(self):
        url = "http://example.com/page?utm_source=google&session_id=123"
        expected_url = "http://example.com/page"
        self.assertEqual(normalize_url(url), expected_url)

    def test_remove_fragment_and_tracking_parameters(self):
        url = "http://example.com/page#section?utm_source=google&session_id=123"
        expected_url = "http://example.com/page"
        self.assertEqual(normalize_url(url), expected_url)

    def test_no_fragment_or_tracking_parameters(self):
        url = "http://example.com/page"
        expected_url = "http://example.com/page"
        self.assertEqual(normalize_url(url), expected_url)

if __name__ == '__main__':
    unittest.main()
