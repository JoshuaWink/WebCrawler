"""Utility functions for web crawling and URL handling."""

import logging
import requests
import time
import os
from urllib.parse import urlparse, urljoin
import re
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

# HTML Processing Functions
def extract_text_from_html(soup):
    """Extracts readable text from the HTML body."""
    try:
        body = soup.find('body')
        if body:
            return body.get_text(separator=' ', strip=True)
        else:
            logging.warning("No body tag found in HTML.")
            return ""
    except Exception as e:
        logging.error(f"Error extracting text: {e}")
        return ""


# URL Classification and Management
def classify_link(base_url, link_url):
    """Classifies a link as internal or external."""
    try:
        absolute_url = urljoin(base_url, link_url)
        base_domain = urlparse(base_url).netloc
        link_domain = urlparse(absolute_url).netloc

        if link_domain == base_domain:
            return "internal"
        else:
            return "external"
    except Exception as e:
        logging.error(f"Error classifying link {link_url}: {e}")
        return "unknown"


# File Operations
def save_content(url, text):
    """Saves extracted content to a file."""
    try:
        # Create directory based on the domain
        domain = urlparse(url).netloc
        output_dir = f"output/{domain}"
        os.makedirs(output_dir, exist_ok=True)

        # Sanitize the URL to create a valid filename
        filename = re.sub(r'https?://', '', url).replace("/", "-").replace("?", "_") + ".txt"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text)
        logging.info(f"Saved content from {url} to {filepath}")

    except Exception as e:
        logging.error(f"Error saving content from {url}: {e}")


# Robots.txt Handling
class RobotsParser:
    """Handles fetching and parsing of robots.txt files."""
    
    def __init__(self, base_url, ignore_robots=False):
        """Initialize with base URL and fetch robots.txt.
        
        Args:
            base_url (str): Base URL of the website to crawl
            ignore_robots (bool): If True, ignores robots.txt rules (default: False)
        """
        self.base_url = base_url
        self.ignore_robots = ignore_robots
        self.robots_url = urljoin(base_url, '/robots.txt')
        self.crawl_delay = 0  # Default no delay
        self.rules = {'*': {'disallow': [], 'allow': []}}  # Default all allowed
        self.last_request_time = 0
        if not ignore_robots:
            self.fetch_and_parse()
    
    def fetch_and_parse(self):
        """Fetch and parse the robots.txt file."""
        try:
            response = requests.get(self.robots_url, timeout=10)
            if response.status_code == 200:
                self._parse_robots_txt(response.text)
            else:
                logging.warning(f"No robots.txt found at {self.robots_url}")
        except Exception as e:
            logging.error(f"Error fetching robots.txt: {e}")
    
    def _parse_robots_txt(self, content):
        """Parse robots.txt content and extract rules."""
        current_agent = '*'
        for line in content.split('\n'):
            line = line.strip().lower()
            if not line or line.startswith('#'):
                continue
                
            if line.startswith('user-agent:'):
                current_agent = line.split(':', 1)[1].strip()
                if current_agent not in self.rules:
                    self.rules[current_agent] = {'disallow': [], 'allow': []}
            elif line.startswith('disallow:'):
                path = line.split(':', 1)[1].strip()
                if path:
                    self.rules[current_agent]['disallow'].append(path)
            elif line.startswith('allow:'):
                path = line.split(':', 1)[1].strip()
                if path:
                    self.rules[current_agent]['allow'].append(path)
            elif line.startswith('crawl-delay:'):
                try:
                    delay = float(line.split(':', 1)[1].strip())
                    self.crawl_delay = max(self.crawl_delay, delay)
                except ValueError:
                    pass

    def is_allowed(self, url):
        """Check if URL is allowed to be crawled based on robots.txt rules.
        
        Args:
            url (str): URL to check against robots.txt rules
            
        Returns:
            bool: True if URL is allowed or if ignore_robots is True
        """
        if self.ignore_robots:
            return True
            
        path = urlparse(url).path
        
        # First check specific user-agent rules
        for agent, rules in self.rules.items():
            if agent == '*':
                continue
                
            for allow in rules['allow']:
                if path.startswith(allow):
                    return True
                    
            for disallow in rules['disallow']:
                if path.startswith(disallow):
                    return False
        
        # Then check wildcard rules
        if '*' in self.rules:
            for allow in self.rules['*']['allow']:
                if path.startswith(allow):
                    return True
                    
            for disallow in self.rules['*']['disallow']:
                if path.startswith(disallow):
                    return False
        
        return True  # Default allow if no matching rules
        
    def respect_crawl_delay(self):
        """Sleep if needed to respect crawl-delay."""
        if self.crawl_delay > 0:
            now = time.time()
            time_since_last = now - self.last_request_time
            if time_since_last < self.crawl_delay:
                time.sleep(self.crawl_delay - time_since_last)
            self.last_request_time = time.time()

# URL Normalization
def normalize_url(url, params_to_remove=['utm_source', 'session_id']):
    """Normalizes a URL while preserving the exact path and query structure.
    
    Args:
        url (str): The URL to normalize
        params_to_remove (list): Query parameters to strip from URL, defaults to
            ['utm_source', 'session_id']. Common tracking and session parameters
            that don't affect page content.
    
    Returns:
        str: Normalized URL with specified parameters removed but path preserved
    """
    try:
        # Parse the URL to get its components
        parsed_url = urlparse(url)
        
        # Only normalize scheme and netloc
        scheme = parsed_url.scheme.lower()
        netloc = parsed_url.netloc.lower()
        
        # Get the original path and query string
        path = parsed_url.path
        query = parsed_url.query
        
        # If there are tracking parameters to remove, handle them without re-encoding the entire query
        if query and params_to_remove:
            # Split the query string but preserve the exact parameter values
            params = dict(param.split('=', 1) if '=' in param else (param, '')
                        for param in query.split('&') if param)
            
            # Remove unwanted parameters
            params = {k: v for k, v in params.items() if k not in params_to_remove}
            
            # Reconstruct query string preserving original format
            query = '&'.join(f"{k}={v}" if v else k for k, v in params.items())
        
        # Reconstruct the URL
        normalized = f"{scheme}://{netloc}{path}"
        if query:
            normalized += f"?{query}"
            
        return normalized
    except Exception as e:
        logging.error(f"Error normalizing URL {url}: {e}")
        return url


# Queue Management
def queue_internal_links(url_queue, base_url, links):
    """Queues internal links for further crawling.
    
    Args:
        url_queue (list): Queue of URLs to be crawled
        base_url (str): Base URL of the website being crawled
        links (list): List of links found on the current page
        
    Note:
        Only internal links (same domain as base_url) are added to the queue.
        External links are logged but not queued for crawling.
    """
    try:
        for link in links:
            normalized_url = normalize_url(urljoin(base_url, link))
            if classify_link(base_url, normalized_url) == "internal":
                if normalized_url not in url_queue:
                    url_queue.append(normalized_url)
            else:
                logging.info(f"Skipping external link: {normalized_url}")
    except Exception as e:
        logging.error(f"Error queueing links: {e}")
