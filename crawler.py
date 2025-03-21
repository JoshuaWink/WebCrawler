"""Web crawler for extracting and mapping website content.

This module provides functionality to crawl websites, extract content,
generate sitemaps, and save the results in various formats. It handles
both internal and external links while respecting domain boundaries.
"""

import requests
from bs4 import BeautifulSoup
import logging
import time
import signal
import sys
from utils import extract_text_from_html, classify_link, queue_internal_links, normalize_url, RobotsParser
import argparse
from urllib.parse import urljoin, urlparse
import os
from config import TIMEOUT, RETRIES
from sitemap import SitemapManager
import xlsxwriter
from datetime import datetime
import concurrent.futures
import threading
from queue import Queue, Empty
import shutil
import collections
from benchmarks import CrawlerBenchmark
import psutil  # Ensure this is installed: pip install psutil
import csv

def get_site_name(url):
    """Extract and format the site name from URL."""
    parsed = urlparse(url)
    site_name = parsed.netloc.lower()
    return f"{site_name}-content"

def create_output_file_name(url, output_format):
    """Create output file name with site name and current date."""
    site_name = get_site_name(url)
    current_date = datetime.now().strftime('%Y-%m-%d')
    return f"{site_name}_{current_date}.{output_format}"

def write_to_xlsx(output_file_path, page_contents):
    """Write the crawled content to an XLSX file."""
    workbook = xlsxwriter.Workbook(output_file_path)
    worksheet = workbook.add_worksheet()
    
    # Write headers
    worksheet.write(0, 0, 'url')
    worksheet.write(0, 1, 'content')
    
    # Write data
    for i, (url, content) in enumerate(page_contents.items(), 1):
        worksheet.write(i, 0, url)
        worksheet.write(i, 1, content)
    
    workbook.close()

def write_to_txt(output_file_path, url, text):
    """Write content to a text file."""
    with open(output_file_path, 'a') as output_file:
        output_file.write(f"\n####### START {url.upper()} #######\n\n")
        output_file.write(text)
        output_file.write(f"\n\n####### END {url.upper()} #######\n\n")

def write_to_csv(output_file_path, url, text):
    """Write content to a CSV file."""
    file_exists = os.path.exists(output_file_path)
    with open(output_file_path, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile, quoting=csv.QUOTE_ALL)
        # Write header if file is new
        if not file_exists:
            writer.writerow(['URL', 'Content'])
        # Write the data row
        writer.writerow([url, text])

def save_batch(sitemap, output_file_path, batch_contents, output_format):
    """Save the current batch of pages in the appropriate format.
    
    Args:
        sitemap (SitemapManager): Manager containing all crawled content
        output_file_path (str): Path to save the output file
        batch_contents (dict): Dictionary of URL:content for current batch
        output_format (str): Format to save in ('txt' or 'xlsx')
    """
    if not batch_contents:
        return
        
    logging.info(f"Saving batch of {len(batch_contents)} pages...")
    
    if output_format == 'xlsx':
        # For XLSX, we save all content each time
        write_to_xlsx(output_file_path, sitemap.page_contents)
    elif output_format == 'csv':
        # For CSV, we can append just the new batch content
        for url, content in batch_contents.items():
            write_to_csv(output_file_path, url, content)
    else:
        # For TXT, we can append just the new batch content
        for url, content in batch_contents.items():
            write_to_txt(output_file_path, url, content)
            
    logging.info("Batch saved successfully.")

def configure_logging(output_folder=None, verbose=False):
    """Configure logging with appropriate levels for file and console.
    
    Args:
        output_folder (str): Folder to store log file
        verbose (bool): Whether to show debug logs in console
    """
    # Create a root logger
    root_logger = logging.getLogger()
    # Clear any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Set the default level based on verbosity
    root_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    # Create console handler with appropriate level
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console_format = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_format)
    
    # Add console handler to logger
    root_logger.addHandler(console_handler)
    
    # If output folder is provided, also log to file with more details
    if output_folder:
        log_file = os.path.join(output_folder, 'crawler.log')
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)  # Always debug level in file
        file_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_format)
        root_logger.addHandler(file_handler)

# Spinner animation frames
spinner_frames = ['|', '/', '-', '\\']


def animate_spinner(frame_index):
    """Displays the spinner animation."""
    frame_index = frame_index % len(spinner_frames)
    print(f"\r{spinner_frames[frame_index]}", end="")
    sys.stdout.flush()


def fetch_page(url):
    """Fetches an HTML page and handles potential errors and redirects.
    
    Args:
        url (str): The URL to fetch
        
    Returns:
        dict: Dictionary containing response data or None if fetch failed
            - content: HTML content of the page
            - final_url: URL after following redirects
            - redirected: Boolean indicating if redirects occurred
        
    Note:
        Uses global TIMEOUT and RETRIES settings from config.py
    """
    try:
        response = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        return {
            'content': response.text,
            'final_url': response.url,
            'redirected': len(response.history) > 0
        }
    except requests.exceptions.RequestException as e:
        return None


def parse_html(html):
    """Parses HTML content using BeautifulSoup.
    
    Args:
        html (str): Raw HTML content to parse
        
    Returns:
        BeautifulSoup: Parsed HTML document object
    """
    return BeautifulSoup(html, 'html.parser')


def crawl(url, sitemap, base_url, scope_url=None, robots_parser=None, depth=None, current_depth=0, max_pages=10, output_format='txt', ignore_robots=False, batch_size=5):
    """Recursively crawls a website starting from the given URL.
    
    Args:
        url (str): The URL to start crawling from
        sitemap (SitemapManager): Manager for tracking crawl state
        base_url (str): The root URL to stay within while crawling
        scope_url (str, optional): URL scope limit. If not set, defaults to base_url
        robots_parser (RobotsParser, optional): Parser for robots.txt rules
        depth (int, optional): Maximum depth to crawl. None for unlimited
        current_depth (int): Current recursion depth. Used internally
        max_pages (int): Maximum number of pages to crawl. -1 for unlimited
        output_format (str): Format to save content in ('txt' or 'xlsx')
        ignore_robots (bool): Whether to ignore robots.txt rules
        batch_size (int): Number of pages to process before saving batch data
    
    Returns:
        SitemapManager: Updated sitemap with crawl results
        
    Note:
        Content is saved to files in the output directory as pages are crawled.
        The sitemap is continuously updated to show crawl progress.
    """
    # Ensure output directory exists
    os.makedirs(sitemap.output_folder, exist_ok=True)
    output_file_path = os.path.join(sitemap.output_folder, create_output_file_name(base_url, output_format))
    
    # Initialize batch tracking
    if not hasattr(sitemap, 'batch_counter'):
        sitemap.batch_counter = 0
        sitemap.batch_contents = {}
    
    if robots_parser is None:
        robots_parser = RobotsParser(base_url, ignore_robots=ignore_robots)

    # If no scope provided, use base_url as scope
    scope_url = scope_url or base_url
    
    if not url.startswith(scope_url):
        logging.debug(f"Skipping {url} - outside scope {scope_url}")
        return sitemap

    if not robots_parser.is_allowed(url):
        logging.debug(f"Skipping {url} - disallowed by robots.txt")
        return sitemap

    if depth is not None and depth >= 0 and current_depth > depth:
        return sitemap

    if url in sitemap.visited_urls:
        return sitemap

    sitemap.mark_visited(url)
    logging.info(f"Crawling: {url}")
    
    robots_parser.respect_crawl_delay()
    response = fetch_page(url)
    if not response:
        logging.info(f"Failed to fetch: {url}")
        return sitemap

    if response['redirected']:
        logging.info(f"Redirect: {url} -> {response['final_url']}")
        # Update sitemap with redirect information
        sitemap.add_redirect(url, response['final_url'])
        
        # If redirect URL is within scope, continue crawling from there
        if response['final_url'].startswith(scope_url):
            url = response['final_url']
        else:
            logging.debug(f"Skipping redirect target: outside scope")
            return sitemap

    html_content = response['content']
    soup = parse_html(html_content)
    text = extract_text_from_html(soup)

    if text in sitemap.page_contents.values():
        logging.debug(f"Skipping duplicate content: {url}")
        return sitemap

    sitemap.page_contents[url] = text
    sitemap.batch_contents[url] = text
    sitemap.batch_counter += 1

    # Save batch when counter reaches batch_size
    if sitemap.batch_counter >= batch_size:
        save_batch(sitemap, output_file_path, sitemap.batch_contents, output_format)
        sitemap.batch_counter = 0
        sitemap.batch_contents = {}

    links = [link.get('href') for link in soup.find_all('a')]
    logging.debug(f"Found {len(links)} links on {url}")

    for link in links:
        if link:
            normalized_link = normalize_url(urljoin(url, link))
            if sitemap.is_external(url, normalized_link):
                sitemap.add_external_edge(url, normalized_link)
            else:
                sitemap.add_url(url, normalized_link)

    while sitemap.has_unvisited_urls() and (max_pages == -1 or len(sitemap.visited_urls) < max_pages):
        next_url = sitemap.get_next_url()
        if next_url:
            crawl(next_url, sitemap, base_url, scope_url, robots_parser, depth, current_depth + 1, max_pages, output_format, ignore_robots, batch_size)
        sitemap.update_sitemap_file()
        frame_index = 0
        animate_spinner(frame_index)
        frame_index += 1
        print_cli_output(sitemap)
        time.sleep(0.2)
        
        # Save any accumulated batch before continuing or ending
        if sitemap.batch_counter > 0:
            save_batch(sitemap, output_file_path, sitemap.batch_contents, output_format)
            sitemap.batch_counter = 0
            sitemap.batch_contents = {}
            
    return sitemap

def print_cli_output(sitemap):
    """Prints the CLI output with the desired formatting."""
    print(f"\rMapped: {sitemap.mapped_count}  Unmapped: {sitemap.unmapped_count}", end="")
    sys.stdout.flush()

# Thread-safe counter for managing batch saving
class BatchCounter:
    def __init__(self, batch_size):
        self.counter = 0
        self.batch_size = batch_size
        self.lock = threading.Lock()
        self.batch_contents = {}
        
    def add(self, url, content):
        """Add content to batch and return True if batch size reached"""
        with self.lock:
            self.batch_contents[url] = content
            self.counter += 1
            if self.counter >= self.batch_size:
                return True
            return False
            
    def reset(self):
        """Reset counter and return current batch contents"""
        with self.lock:
            contents = self.batch_contents
            self.counter = 0
            self.batch_contents = {}
            return contents

# Worker function for processing a single URL
def process_url(url, sitemap, base_url, scope_url, robots_parser, depth, current_depth, output_format, batch_counter):
    """Process a single URL and extract its content and links.
    
    Args:
        url (str): The URL to process
        sitemap (SitemapManager): Shared sitemap manager
        base_url (str): Base URL of the site being crawled
        scope_url (str): URL scope limitation
        robots_parser (RobotsParser): Parser for robots.txt rules
        depth (int): Maximum crawl depth
        current_depth (int): Current depth of this URL
        output_format (str): Format to save content in ('txt' or 'xlsx')
        batch_counter (BatchCounter): Thread-safe batch counter
        
    Returns:
        dict: Result dictionary containing processed URL data
    """
    if depth is not None and depth >= 0 and current_depth > depth:
        return {"url": url, "final_url": url, "status": "skipped_depth", "links": []}
    
    if not url.startswith(scope_url):
        return {"url": url, "final_url": url, "status": "skipped_scope", "links": []}

    if not robots_parser.is_allowed(url):
        return {"url": url, "final_url": url, "status": "skipped_robots", "links": []}
    
    robots_parser.respect_crawl_delay()
    response = fetch_page(url)
    if not response:
        return {"url": url, "final_url": url, "status": "failed", "links": []}

    redirected_url = url
    if response['redirected']:
        redirected_url = response['final_url']
        if not redirected_url.startswith(scope_url):
            return {"url": url, "final_url": redirected_url, "status": "redirect_out_of_scope", 
                   "redirect_url": redirected_url, "links": []}

    html_content = response['content']
    soup = parse_html(html_content)
    text = extract_text_from_html(soup)
    
    # Check for duplicate content can only be done in the main thread
    # We'll return the content and let the main thread decide
    
    links = [link.get('href') for link in soup.find_all('a')]
    normalized_links = []
    
    for link in links:
        if link:
            normalized_link = normalize_url(urljoin(redirected_url, link))
            normalized_links.append(normalized_link)
    
    # Add this url's content to the batch
    should_save_batch = batch_counter.add(redirected_url, text)
    
    return {
        "url": url,
        "final_url": redirected_url,
        "status": "success" if not response['redirected'] else "redirected",
        "text": text,
        "links": normalized_links,
        "save_batch": should_save_batch,
        "is_external": False  # Add explicit flag for external URLs
    }

# Add this to store text hashes for faster duplicate detection
def get_text_hash(text):
    """Create a hash of text content for faster duplicate detection."""
    import hashlib
    return hashlib.md5(text.encode('utf-8')).hexdigest()

# Fix progress display issues
class CrawlProgress:
    def __init__(self, update_interval=0.5):
        self.start_time = time.time()
        self.last_update_time = self.start_time
        self.update_interval = update_interval
        self.mapped_count = 0
        self.unmapped_count = 0
        self.batch_times = collections.deque(maxlen=5)  # Keep last 5 batch times for moving average
        self.last_batch_time = self.start_time
        self.processed_per_second = 0
        try:
            self.terminal_width = shutil.get_terminal_size().columns
        except:
            self.terminal_width = 80  # Default if we can't get terminal size
        
        # Clear the current line to start fresh
        print("\033[K", end="")  # ANSI escape code to clear the line
        
    def update(self, mapped_count, unmapped_count, force=False):
        """Update progress bar if enough time has passed or if force=True."""
        current_time = time.time()
        if force or (current_time - self.last_update_time >= self.update_interval):
            self.mapped_count = mapped_count
            self.unmapped_count = unmapped_count
            self._record_batch()
            self._display_progress()
            self.last_update_time = current_time
            
    def _record_batch(self):
        """Record time taken for current batch for rate calculation."""
        current_time = time.time()
        if self.mapped_count > 0:  # Only calculate rate if we've mapped pages
            time_diff = current_time - self.last_batch_time
            
            # Track only the DIFFERENCE in mapped count since last update
            current_diff = self.mapped_count - self.last_mapped_count if hasattr(self, 'last_mapped_count') else self.mapped_count
            self.last_mapped_count = self.mapped_count
            
            # Only add to batch times if there's an actual difference
            if current_diff > 0:
                self.batch_times.append((current_diff, time_diff))
            
            # Calculate processing rate based on moving average
            if len(self.batch_times) > 0:
                total_processed = sum(count for count, _ in self.batch_times)
                total_time = sum(time_taken for _, time_taken in self.batch_times)
                if total_time > 0:
                    self.processed_per_second = total_processed / total_time
        
        self.last_batch_time = current_time
            
    def _display_progress(self):
        """Display progress bar and stats."""
        # Clear the current line
        print("\033[K", end="")  # ANSI escape code to clear the line
        
        elapsed = time.time() - self.start_time
        elapsed_str = self._format_time(elapsed)
        
        # Calculate ETA if we have some data
        eta_str = "Unknown"
        if self.processed_per_second > 0 and self.unmapped_count > 0:
            eta = self.unmapped_count / self.processed_per_second
            eta_str = self._format_time(eta)
        
        # Format progress bar - make it more visible with colors and characters
        bar_width = min(30, self.terminal_width - 60)  # Reduce bar width to ensure time fits
        total = max(1, self.mapped_count + self.unmapped_count)
        progress = min(1.0, self.mapped_count / total)
        bar_fill = '█' * int(bar_width * progress)  # Using block character for better visibility
        bar_empty = '░' * (bar_width - len(bar_fill))  # Using light shade for empty part
        
        # Prioritize critical information first to avoid truncation
        status = f"\r\033[1m[{bar_fill}{bar_empty}]\033[0m "  # Bold progress bar
        status += f"{self.mapped_count}/{total} "
        status += f"| \033[32m{self.processed_per_second:.1f} p/s\033[0m "  # Green rate
        status += f"| \033[36m{elapsed_str}\033[0m elapsed"  # Cyan elapsed time
        
        # Only add ETA if there's space
        if len(status) + 15 < self.terminal_width:
            status += f" | ETA: \033[33m{eta_str}\033[0m"  # Yellow ETA
        
        # Ensure we don't exceed terminal width
        if len(status) > self.terminal_width:
            status = status[:self.terminal_width-3] + "..."
            
        print(status, end="", flush=True)
            
    def _format_time(self, seconds):
        """Format seconds into h:m:s format."""
        hours, remainder = divmod(int(seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes:02d}m {seconds:02d}s"
        else:
            return f"{minutes}m {seconds:02d}s"
            
    def finish(self):
        """Display final stats."""
        self.update(self.mapped_count, self.unmapped_count, force=True)
        sys.stdout.write("\n")  # Move to next line after progress bar
        
        # Calculate and display summary
        total_time = time.time() - self.start_time
        pages_per_second = self.mapped_count / total_time if total_time > 0 else 0
        
        print(f"\nCrawl completed in {self._format_time(total_time)}")
        print(f"Pages mapped: {self.mapped_count}")
        print(f"Average rate: {pages_per_second:.2f} pages/second")

# Update concurrent_crawl function to use the progress tracker
def concurrent_crawl(start_url, max_workers=4, scope_url=None, depth=None, max_pages=-1, 
                    output_format='txt', ignore_robots=False, batch_size=5, verbose=False):
    """Crawl a website using multiple concurrent workers."""
    # Initialize shared state
    sitemap = SitemapManager(start_url)
    
    # Configure logging properly
    configure_logging(sitemap.output_folder, verbose)
    
    # Initialize robots parser - FIX: This was missing before
    robots_parser = RobotsParser(start_url, ignore_robots=ignore_robots)
    
    # For faster duplicate detection
    content_hashes = set()
    
    # Initialize progress tracker
    print("\033c", end="")  # Clear entire screen
    print("Starting crawler with progress tracking...\n")
    progress = CrawlProgress()
    
    # Performance optimizations - limit frequency of sitemap updates
    last_sitemap_update = time.time()
    sitemap_update_interval = 10  # seconds between sitemap file updates
    
    # URLs to process and URLs being processed
    to_crawl = Queue()
    to_crawl.put((start_url, 0))  # (url, depth)
    in_progress = set()
    
    # Create thread-safe batch counter
    batch_counter = BatchCounter(batch_size)
    
    # Ensure output directory exists
    os.makedirs(sitemap.output_folder, exist_ok=True)
    output_file_path = os.path.join(sitemap.output_folder, 
                                   create_output_file_name(start_url, output_format))
    
    # Reduce logging verbosity for cleaner console output
    logging.basicConfig(level=logging.WARNING, 
                       format='%(asctime)s - %(levelname)s - %(message)s',
                       filename=os.path.join(sitemap.output_folder, 'crawler.log'))
    
    # Initialize benchmarking
    benchmark = CrawlerBenchmark(
        target_url=start_url,
        workers=max_workers,
        batch_size=batch_size,
        scope=scope_url,
        depth=depth,
        max_pages=max_pages
    )
    
    # Setup thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        
        try:
            while True:
                # Start new tasks if we have URLs and workers available
                while len(futures) < max_workers and not to_crawl.empty() and (max_pages == -1 or len(sitemap.visited_urls) < max_pages):
                    try:
                        url, current_depth = to_crawl.get(block=False)
                        
                        # Skip if already visited or in progress
                        if url in sitemap.visited_urls or url in in_progress:
                            continue
                            
                        # Mark as in progress
                        in_progress.add(url)
                        
                        # Submit task to executor
                        future = executor.submit(
                            process_url,
                            url, sitemap, start_url, scope_url, robots_parser, 
                            depth, current_depth, output_format, batch_counter
                        )
                        futures[future] = url
                        # Don't print every URL to keep console clean
                    except Empty:
                        break
                
                # Update progress display and benchmarks
                progress.update(len(sitemap.visited_urls), len(sitemap.unvisited_urls) + len(in_progress))
                benchmark.update_metrics(len(sitemap.visited_urls))
                
                # Exit if no more work
                if not futures:
                    if to_crawl.empty() or (max_pages != -1 and len(sitemap.visited_urls) >= max_pages):
                        break
                    time.sleep(0.1)  # Short sleep to avoid CPU spinning
                    continue
                    
                # Wait for a task to complete
                done, _ = concurrent.futures.wait(
                    futures, 
                    return_when=concurrent.futures.FIRST_COMPLETED,
                    timeout=0.5  # Add timeout to update progress display regularly
                )
                
                # If no tasks completed yet, continue to next iteration for progress update
                if not done:
                    continue
                
                # Process completed tasks
                for future in done:
                    url = futures[future]
                    in_progress.remove(url)
                    del futures[future]
                    
                    try:
                        result = future.result()
                        status = result.get("status", "unknown")
                        final_url = result.get("final_url", url)  # Always use .get() with default
                        
                        # Always mark as visited regardless of success/failure
                        sitemap.mark_visited(url)
                        
                        # Record metrics for benchmarking - ensure we handle missing keys
                        benchmark.record_status_code(result.get("status_code", 200))
                        
                        if status == "redirected":
                            benchmark.record_redirect()
                            sitemap.add_redirect(url, final_url)
                        elif status.startswith("failed"):
                            benchmark.record_error()
                            logging.debug(f"Failed to process {url}: {status}")
                        
                        # Handle content if it exists (for any status)
                        if "text" in result:
                            text = result["text"]
                            # Record content metrics with safe access to final_url
                            benchmark.record_content(final_url, text)
                            
                            # Process content if not duplicate
                            text_hash = get_text_hash(text)
                            if text_hash not in content_hashes:
                                content_hashes.add(text_hash)
                                sitemap.page_contents[final_url] = text
                                
                                # Process links if they exist
                                if "links" in result:
                                    for link in result["links"]:
                                        # Explicitly check if link is internal AND within scope
                                        is_external = sitemap.is_external(final_url, link)
                                        
                                        if is_external:
                                            # Handle external link (only for sitemap visualization)
                                            if len(sitemap.external_edges) < 1000:
                                                sitemap.add_external_edge(final_url, link)
                                        else:
                                            # Handle internal link
                                            # Only queue for crawling if within scope
                                            if link.startswith(scope_url or start_url):
                                                if link not in sitemap.visited_urls and link not in in_progress:
                                                    sitemap.add_url(final_url, link)
                                                    to_crawl.put((link, current_depth + 1))
                        
                        # Check if we need to save a batch
                        if result.get("save_batch", False):
                            batch_contents = batch_counter.reset()
                            save_batch(sitemap, output_file_path, batch_contents, output_format)
                            # Update progress after saving batch
                            progress.update(len(sitemap.visited_urls), len(sitemap.unvisited_urls) + len(in_progress), force=True)
                        
                    except Exception as e:
                        logging.error(f"Error processing {url}: {str(e)}")
                        benchmark.record_error()
                        # Still mark as visited to avoid infinite retry loops
                        sitemap.mark_visited(url)
                
                # Update sitemap file less frequently to reduce I/O overhead
                current_time = time.time()
                if current_time - last_sitemap_update > sitemap_update_interval:
                    sitemap.update_sitemap_file()
                    last_sitemap_update = current_time
                
        except KeyboardInterrupt:
            print("\nInterrupted. Saving progress...")
            
            # Cancel remaining futures
            for future in futures:
                future.cancel()
                
            # Save any remaining batch
            batch_contents = batch_counter.reset()
            if batch_contents:
                save_batch(sitemap, output_file_path, batch_contents, output_format)
    
    # Save any final batch that didn't reach the batch size
    final_batch = batch_counter.reset()
    if final_batch:
        save_batch(sitemap, output_file_path, final_batch, output_format)
    
    # Complete benchmark and display results
    benchmark_summary = benchmark.complete()
    print("\nBenchmark Summary:")
    print(f"Average Speed: {benchmark_summary['avg_crawl_speed']:.2f} pages/second")
    print(f"Peak Memory: {benchmark_summary['peak_memory_mb']:.2f} MB")
    print(f"Total Content: {benchmark_summary['total_content_mb']:.2f} MB")
    print(f"Results saved to benchmarks/crawler_benchmarks.csv")
    
    # Display final progress statistics
    progress.finish()
        
    return sitemap

def crawl_site(start_url, sitemap, max_pages=-1, depth=-1, scope=None):
    # Add debugging to identify termination point
    try:
        # Existing code...
        
        unvisited_urls = list(sitemap.unvisited_urls)  # Initialize unvisited_urls from sitemap
        while len(unvisited_urls) > 0 and (max_pages < 0 or mapped_count < max_pages):
            try:
                # Log the queue status periodically
                if len(sitemap.visited_urls) % 100 == 0:
                    logging.info(f"Queue status: {len(unvisited_urls)} URLs in queue")
                
                # Process URL
                # ...
                
                # Add error recovery for failed pages
            except Exception as e:
                logging.error(f"Error processing {url}: {str(e)}")
                # Don't just discard the URL - maybe retry later
                if retries.get(url, 0) < 3:  # Allow 3 retry attempts
                    retries[url] = retries.get(url, 0) + 1
                    unvisited_urls.append(url)  # Put back in queue
                    
        # Add debug info about why we stopped
        if len(unvisited_urls) == 0:
            logging.info("Crawl completed: No more URLs to visit")
        elif max_pages > 0 and mapped_count >= max_pages:
            logging.info(f"Crawl completed: Reached max pages limit ({max_pages})")
            
    except Exception as e:
        logging.error(f"Fatal error in crawler: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Crawl a website and generate a content map.',
        formatter_class=argparse.RawTextHelpFormatter)
    
    # Required arguments
    parser.add_argument('url', metavar='URL', type=str,
                      help='''Starting URL to crawl.
Examples:
  'https://example.com'
  'https://docs.example.com/path?param=value'
Note: For URLs with special characters, enclose in quotes:
  python crawler.py 'https://site.com/path?param=value' --scope 'https://site.com'
''')
    
    # Optional crawl control arguments
    parser.add_argument('--depth', type=int, default=-1,
                      help='Maximum crawl depth. -1 for unlimited (default: -1)')
    parser.add_argument('--max-pages', type=int, default=-1,
                      help='Maximum pages to crawl. -1 for unlimited (default: -1)')
    parser.add_argument('--scope', type=str,
                      help='''URL scope limit. If not set, defaults to the starting URL.
Examples:
  --scope 'https://example.com'
  --scope 'https://docs.example.com/path'
Note: The scope should be a parent or equal to the starting URL.''')
    
    # Output format selection
    parser.add_argument('--output-format', type=str, choices=['txt', 'xlsx', 'csv'], default='txt',
                      help='Save content as text files, Excel spreadsheet, or CSV file (default: txt)')
    parser.add_argument('--ignore-robots', action='store_true',
                      help='Ignore robots.txt rules when crawling')
    parser.add_argument('--batch-size', type=int, default=5,
                      help='Number of pages to process before saving batch data (default: 5)')
    # Add workers parameter
    parser.add_argument('--workers', type=int, default=4,
                      help='Number of concurrent workers for crawling (default: 4)')
    # Add verbose flag
    parser.add_argument('--verbose', action='store_true',
                      help='Enable verbose logging output')
    args = parser.parse_args()
    
    # Validate and normalize URLs
    try:
        start_url = normalize_url(args.url)
        if args.scope:
            scope_url = normalize_url(args.scope)
            if not start_url.startswith(scope_url):
                print(f"Error: Starting URL '{start_url}' is not within scope '{scope_url}'")
                sys.exit(1)
    except ValueError as e:
        print(f"Error: Invalid URL format - {str(e)}")
        sys.exit(1)
        
    # Setup signal handler
    def signal_handler(sig, frame):
        logging.warning("\nCrawling interrupted. Saving progress...")
        sys.exit(1)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        if args.workers > 1:
            # Clear screen before starting
            print("\033c", end="")  # Clear screen
            print("=" * 70)
            print(f"Starting concurrent crawl with {args.workers} workers...")
            print(f"Target: {start_url}")
            print("=" * 70)
            print()  # Empty line before progress bar
            
            sitemap = concurrent_crawl(
                start_url, 
                max_workers=args.workers,
                scope_url=args.scope, 
                depth=args.depth, 
                max_pages=args.max_pages, 
                output_format=args.output_format, 
                ignore_robots=args.ignore_robots,
                batch_size=args.batch_size,
                verbose=args.verbose  # Pass the verbose flag
            )
        else:
            print("Starting single-threaded crawl...")
            sitemap = SitemapManager(start_url)
            
            # Configure logging properly
            configure_logging(sitemap.output_folder, args.verbose)
            
            robots_parser = RobotsParser(start_url, ignore_robots=args.ignore_robots)
            sitemap.add_url(start_url, start_url)
            
            sitemap = crawl(
                start_url, sitemap, start_url, args.scope, 
                robots_parser, args.depth, 0, args.max_pages, 
                args.output_format, args.ignore_robots, args.batch_size
            )
            
        print("\nCrawling completed.")
        print(f"Mapped pages: {sitemap.mapped_count}")
        print(f"Unmapped pages: {sitemap.unmapped_count}")
        
    except KeyboardInterrupt:
        print("\nCrawling interrupted.")
        sys.exit(0)
    except Exception as e:
        print(f"\nCrawling failed with error: {str(e)}")
        logging.exception("Uncaught exception")
        sys.exit(1)
