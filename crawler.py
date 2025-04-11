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
import subprocess
import uuid
import glob
import json
from utils import extract_text_from_html, classify_link, queue_internal_links, normalize_url, RobotsParser, UrlClaimManager
import argparse
from urllib.parse import urljoin, urlparse
from utils import extract_text_from_html, classify_link, queue_internal_links, normalize_url
import os
from config import TIMEOUT, RETRIES
from sitemap import SitemapManager
import xlsxwriter
from datetime import datetime
import re
# import pandas as pd
import fcntl
import threading
from redis_utils import RedisUrlClaimManager, setup_redis_claim_manager
from js_fetcher import fetch_with_js

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

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)

# Spinner animation frames
spinner_frames = ['|', '/', '-', '\\']


def animate_spinner(frame_index):
    """Displays the spinner animation."""
    frame_index = frame_index % len(spinner_frames)
    print(f"\r{spinner_frames[frame_index]}", end="")
    sys.stdout.flush()


def fetch_page(url, use_js=False):
    """Fetches an HTML page and handles potential errors.
    
    Args:
        url (str): The URL to fetch
        use_js (bool): Whether to use JavaScript rendering
        
    Returns:
        tuple: (html_content, soup_object) or (None, None) if the fetch failed
        
    Note:
        Uses global TIMEOUT and RETRIES settings from config.py
        When use_js is True, Playwright will be used to render JavaScript
    """
    try:
        if use_js:
            logging.info(f"Using JavaScript rendering for {url}")
            html_content, soup = fetch_with_js(url, timeout=TIMEOUT)
            return html_content, soup
        else:
            response = requests.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            return response.text, None
    except requests.exceptions.RequestException as e:
        logging.error(f"Request error fetching {url}: {e}")
        return None, None
    except Exception as e:
        logging.error(f"Unexpected error fetching {url}: {e}")
        return None, None


def parse_html(html):
    """Parses HTML content using BeautifulSoup.
    
    Args:
        html (str): Raw HTML content to parse
        
    Returns:
        BeautifulSoup: Parsed HTML document object
    """
    if html is None:
        return None
    return BeautifulSoup(html, 'html.parser')


def crawl(url, sitemap, base_url, robots_parser=None, depth=None, current_depth=0, max_pages=10, 
          output_format='txt', save_frequency=1, idle_timeout=60, claim_manager=None, use_js=False):
    """Recursively crawls a website starting from the given URL.
    
    Args:
        url (str): The URL to start crawling from
        sitemap (SitemapManager): Manager for tracking crawl state
        base_url (str): The root URL to stay within while crawling
        depth (int, optional): Maximum depth to crawl. None for unlimited
        current_depth (int): Current recursion depth. Used internally
        max_pages (int): Maximum number of pages to crawl. -1 for unlimited
        output_format (str): Format to save content in ('txt' or 'xlsx')
        save_frequency (int): How often to save content based on number of pages crawled. Default is 1 (every page).
        idle_timeout (int): Seconds to wait in idle state before exiting when no URLs are available.
        claim_manager (UrlClaimManager, optional): URL claim manager for multi-agent mode
        use_js (bool): Whether to use JavaScript rendering for this page
    
    Returns:
        SitemapManager: Updated sitemap with crawl results
        
    Note:
        Content is saved to files in the output directory as pages are crawled.
        The sitemap is continuously updated to show crawl progress.
    """
    # Ensure output directory exists
    os.makedirs(sitemap.output_folder, exist_ok=True)
    output_file_path = os.path.join(sitemap.output_folder, create_output_file_name(base_url, output_format))
    
    if robots_parser is None:
        robots_parser = RobotsParser(base_url)

    if not url.startswith(base_url):
        print(f"\rSkipping {url} - outside base URL {base_url}")
        return sitemap

    if not robots_parser.is_allowed(url):
        print(f"\rSkipping {url} - disallowed by robots.txt")
        return sitemap

    if depth is not None and depth >= 0 and current_depth > depth:
        return sitemap

    # In multi-agent mode, this check is already handled by the UrlClaimManager
    if url in sitemap.visited_urls:
        return sitemap

    sitemap.mark_visited(url)
    print(f"\rCrawling: {url}", end="")
    sys.stdout.flush()
    
    robots_parser.respect_crawl_delay()
    
    # Check if URL might need JavaScript (based on common patterns or extensions)
    should_use_js = use_js or should_render_with_js(url)
    html_content, soup = fetch_page(url, use_js=should_use_js)
    
    if not html_content:
        print(f"\rFailed to fetch: {url}")
        return sitemap

    # If we didn't get the soup object directly, parse the HTML
    if soup is None:
        soup = parse_html(html_content)
        
    if soup is None:
        print(f"\rFailed to parse HTML for: {url}")
        return sitemap
        
    text = extract_text_from_html(soup)

    if text in sitemap.page_contents.values():
        print(f"\rSkipping duplicate content: {url}")
        return sitemap

    sitemap.page_contents[url] = text

    # Save content based on save_frequency
    if output_format == 'xlsx':
        # For xlsx, save based on save_frequency or when max_pages is reached
        if len(sitemap.visited_urls) % save_frequency == 0 or (len(sitemap.visited_urls) >= max_pages and max_pages != -1):
            write_to_xlsx(output_file_path, sitemap.page_contents)
    else:
        # For txt, write the current page immediately
        write_to_txt(output_file_path, url, text)

    links = [link.get('href') for link in soup.find_all('a')]
    print(f"\rFound {len(links)} links on {url}", end="")
    sys.stdout.flush()

    for link in links:
        if link:
            normalized_link = normalize_url(urljoin(url, link))
            if sitemap.is_external(url, normalized_link):
                sitemap.add_external_edge(url, normalized_link)
            else:
                sitemap.add_url(url, normalized_link)

    # Track pages processed to manage sitemap updates based on save_frequency
    pages_since_last_update = 0
    
    # For handling idle state
    idle_start_time = None
    
    while sitemap.has_unvisited_urls() and (max_pages == -1 or len(sitemap.visited_urls) < max_pages):
        # Check if task has been marked as complete
        if claim_manager and claim_manager.is_task_complete():
            print("\rTask marked as complete by central coordinator. Exiting.")
            break
            
        next_url = sitemap.get_next_url()
        if next_url:
            # Reset idle state since we found a URL to process
            idle_start_time = None
            
            # Crawl the URL
            crawl(next_url, sitemap, base_url, robots_parser, depth, current_depth + 1, 
                 max_pages, output_format, save_frequency, idle_timeout, claim_manager, use_js)
        else:
            # In multi-agent mode, if no URLs are available, wait in idle state
            if claim_manager:
                # Start tracking idle time if we haven't already
                if idle_start_time is None:
                    idle_start_time = time.time()
                    print("\rEntering idle state - waiting for URLs...", end="")
                    sys.stdout.flush()
                
                current_idle_time = time.time() - idle_start_time
                
                # If we've been idle too long and idle_timeout is enabled
                if idle_timeout > 0 and current_idle_time > idle_timeout:
                    # Check if crawl is still active
                    if claim_manager.is_crawl_active(idle_timeout):
                        # Crawl is active, reset idle timer
                        print(f"\rCrawl still active. Resetting idle timer.", end="")
                        sys.stdout.flush()
                        idle_start_time = time.time()
                    else:
                        # No activity, check if we're the primary agent
                        if sitemap.agent_id == "agent-1" or sitemap.agent_id == "merger":
                            # Signal task completion so other agents know to stop
                            print("\rNo more URLs to process. Signaling task completion.")
                            claim_manager.signal_task_complete()
                        
                        # Exit the loop
                        print(f"\rNo activity for {idle_timeout} seconds. Crawl appears complete.")
                        break
                
                # Show how long we've been idle
                remaining = idle_timeout - current_idle_time if idle_timeout > 0 else "∞"
                print(f"\rIdle: {int(current_idle_time)}s, timeout in {remaining}s", end="")
                sys.stdout.flush()
                
                time.sleep(2)  # Wait a bit before checking again
                continue
            else:
                # In single-agent mode, if no URLs are available, we're done
                break
        
        pages_since_last_update += 1
        # Update sitemap file based on save_frequency
        if pages_since_last_update % save_frequency == 0:
            sitemap.update_sitemap_file()
            pages_since_last_update = 0
            
        frame_index = 0
        animate_spinner(frame_index)
        frame_index += 1
        print_cli_output(sitemap)
        time.sleep(0.2)
    return sitemap

def print_cli_output(sitemap):
    """Prints the CLI output with the desired formatting."""
    print(f"\rMapped: {sitemap.mapped_count}  Unmapped: {sitemap.unmapped_count}", end="")
    sys.stdout.flush()

def merge_txt_files(output_dir, base_url, output_file_path=None):
    """Merge all TXT content files from different agents into a single file.
    
    Args:
        output_dir (str): Directory containing output files
        base_url (str): Base URL used to identify content files
        output_file_path (str, optional): Path for merged file, generated if not provided
    
    Returns:
        str: Path to the merged output file
    """
    if output_file_path is None:
        site_name = get_site_name(base_url)
        current_date = datetime.now().strftime('%Y-%m-%d')
        output_file_path = os.path.join(output_dir, f"{site_name}_merged_{current_date}.txt")
    
    # Find all TXT content files for this site
    site_name = get_site_name(base_url).split('-')[0]  # Extract domain part
    content_files = glob.glob(os.path.join(output_dir, f"{site_name}*content*txt"))
    
    # Keep track of URLs we've already added to avoid duplicates
    processed_urls = set()
    url_pattern = r"####### START (.+?) #######"
    
    with open(output_file_path, 'w') as outfile:
        outfile.write(f"# MERGED CRAWLER RESULTS FOR {base_url}\n")
        outfile.write(f"# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        for file_path in content_files:
            with open(file_path, 'r') as infile:
                content = infile.read()
                
                # Extract URL sections and add only if not seen before
                sections = re.split(r"####### START (.+?) #######", content)
                
                # Skip first element which is empty
                if sections and not sections[0].strip():
                    sections = sections[1:]
                
                # Process pairs of (url, content)
                for i in range(0, len(sections), 2):
                    if i+1 < len(sections):
                        url = sections[i].strip()
                        section_content = sections[i+1].strip()
                        
                        # Remove the end marker if present
                        section_content = re.sub(r"####### END .+? #######", "", section_content).strip()
                        
                        if url not in processed_urls:
                            processed_urls.add(url)
                            outfile.write(f"\n####### START {url} #######\n\n")
                            outfile.write(section_content)
                            outfile.write(f"\n\n####### END {url} #######\n\n")
    
    print(f"Merged {len(processed_urls)} unique URLs into {output_file_path}")
    return output_file_path

def merge_xlsx_files(output_dir, base_url, output_file_path=None):
    """Merge multiple XLSX files into a single combined file.
    
    Args:
        output_dir (str): Directory containing the output files
        base_url (str): Base URL used for the crawl, used to determine file naming
        output_file_path (str, optional): Path for the merged output file
            
    Returns:
        str: Path to the merged file
    """
    site_name = get_site_name(base_url)
    current_date = datetime.now().strftime('%Y-%m-%d')
    
    # Find all XLSX files matching the pattern
    pattern = os.path.join(output_dir, f"{site_name}_*_{current_date}.xlsx")
    xlsx_files = glob.glob(pattern)
    
    if not xlsx_files:
        print(f"No XLSX files found matching pattern: {pattern}")
        return None
    
    # Default output path if not specified
    if not output_file_path:
        output_file_path = os.path.join(output_dir, f"{site_name}_merged_{current_date}.xlsx")
    
    # Create a new workbook for the merged output
    workbook = xlsxwriter.Workbook(output_file_path)
    worksheet = workbook.add_worksheet()
    
    # Write headers
    worksheet.write(0, 0, 'url')
    worksheet.write(0, 1, 'content')
    
    # Initialize for content merging
    row_idx = 1
    merged_data = {}
    
    # Read and merge each XLSX file
    for file_path in xlsx_files:
        print(f"Merging file: {file_path}")
        
        # Skip if file is the output file
        if os.path.abspath(file_path) == os.path.abspath(output_file_path):
            continue
        
        try:
            # Using pandas to read Excel files
            # Comment out pandas code for now
            # df = pd.read_excel(file_path)
            # 
            # # Merge the data
            # for _, row in df.iterrows():
            #     url = row['url']
            #     content = row['content']
            #     
            #     # Only add if not already in merged data
            #     if url not in merged_data:
            #         merged_data[url] = content
            print(f"Skipping pandas-based XLSX merging for: {file_path}")
        except Exception as e:
            print(f"Error processing file {file_path}: {e}")
    
    # Write all merged data to the output file
    for url, content in merged_data.items():
        worksheet.write(row_idx, 0, url)
        worksheet.write(row_idx, 1, content)
        row_idx += 1
    
    workbook.close()
    print(f"Merged XLSX output written to: {output_file_path}")
    
    return output_file_path

def merge_sitemaps(output_dir, base_url, output_file_path=None):
    """Merge all DOT sitemap files from different agents into a single combined sitemap.
    
    Args:
        output_dir (str): Directory containing output files
        base_url (str): Base URL used to identify sitemap files
        output_file_path (str, optional): Path for merged file, generated if not provided
    
    Returns:
        str: Path to the merged sitemap file
    """
    if output_file_path is None:
        domain = urlparse(base_url).netloc
        current_date = datetime.now().strftime('%Y-%m-%d')
        output_file_path = os.path.join(output_dir, f"{domain}-sitemap_merged_{current_date}.dot")
    
    # Find all DOT sitemap files for this site
    domain = urlparse(base_url).netloc
    sitemap_files = glob.glob(os.path.join(output_dir, f"{domain}-sitemap_*dot"))
    
    # Initialize containers for merged data
    all_nodes = set()
    all_edges = set()
    node_attributes = {}
    
    for file_path in sitemap_files:
        try:
            with open(file_path, 'r') as f:
                content = f.read()
                
                # Extract node declarations
                node_matches = re.findall(r'"(.+?)" \[(.*?)\];', content)
                for node, attrs in node_matches:
                    all_nodes.add(node)
                    # Keep track of node attributes (using the most recent one if duplicated)
                    node_attributes[node] = attrs
                
                # Extract edge declarations
                edge_matches = re.findall(r'"(.+?)" -> "(.+?)" (\[.*?\])?;', content)
                for source, target, attrs in edge_matches:
                    edge = (source, target, attrs if attrs else "")
                    all_edges.add(edge)
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
    
    # Write merged sitemap
    with open(output_file_path, 'w') as f:
        # Write header
        f.write("/* Generated Merged Site Map */\n")
        f.write("digraph SiteMap {\n")
        f.write("    /* General Graph Attributes */\n")
        f.write("    graph [layout=neato, overlap=false, splines=true];\n")
        f.write('    node [shape=circle, fontname="Arial", fontsize=12, style=filled, fillcolor=lightgray];\n')
        f.write("    edge [fontname=\"Arial\", fontsize=10, fillcolor=orange];\n\n")
        
        # Write nodes with attributes
        f.write("    /* Nodes */\n")
        for node in all_nodes:
            attrs = node_attributes.get(node, '')
            f.write(f'    "{node}" [{attrs}];\n')
        
        # Write edges
        f.write("\n    /* Edges */\n")
        for source, target, attrs in all_edges:
            f.write(f'    "{source}" -> "{target}" {attrs};\n')
        
        # Add a note about merging
        f.write('\n    /* Merger Information */\n')
        f.write(f'    "Merged Sitemap" [shape=box, fillcolor=green];\n')
        f.write(f'    "Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}" [shape=box, fillcolor=green];\n')
        
        f.write("}\n")
    
    print(f"Merged {len(all_nodes)} nodes and {len(all_edges)} edges into {output_file_path}")
    return output_file_path

def run_merger(base_url, output_format='txt', state_dir='output', interval=10):
    """Run a merger agent that periodically combines results from multiple crawler agents.
    
    Args:
        base_url (str): The starting URL for the crawl
        output_format (str): Format of the content files to merge ('txt' or 'xlsx')
        state_dir (str): Directory containing shared state and output files
        interval (int): How often to check for and merge new results (in seconds)
    """
    print(f"Starting merger agent for {base_url}")
    print(f"Looking for output files in {state_dir}")
    print(f"Will merge files every {interval} seconds")
    
    # Parse base URL to get domain
    parsed = urlparse(base_url)
    domain = parsed.netloc
    path = parsed.path.strip('/').replace('/', '-')
    
    # Determine output directory
    output_folder = f"{state_dir}/{domain}-{path}" if path else f"{state_dir}/{domain}"
    if not os.path.exists(output_folder):
        print(f"Output directory {output_folder} does not exist. Creating it.")
        os.makedirs(output_folder, exist_ok=True)
    
    # Keep track of last merge time
    last_merge_time = 0
    
    try:
        # Initialize URL claim manager
        url_claim_manager = UrlClaimManager(base_url, agent_id="merger", state_dir=state_dir)
        
        while True:
            current_time = time.time()
            
            # Check if task has been marked as complete
            if url_claim_manager.is_task_complete():
                print("\nTask marked as complete. Performing final merge...")
                
                # Merge content files
                if output_format == 'txt':
                    merge_txt_files(output_folder, base_url)
                else:
                    merge_xlsx_files(output_folder, base_url)
                
                # Merge sitemap files
                merge_sitemaps(output_folder, base_url)
                
                print("Final merge completed. Exiting merger agent.")
                url_claim_manager.unregister_agent()
                break
            
            # If no activity and it's been more than interval seconds since the last merge, do a final merge
            if not url_claim_manager.is_crawl_active(timeout_seconds=interval*2):
                if current_time - last_merge_time >= interval:
                    print("\nNo active crawlers detected. Performing final merge and signaling task completion...")
                    
                    # Merge content files
                    if output_format == 'txt':
                        merge_txt_files(output_folder, base_url)
                    else:
                        merge_xlsx_files(output_folder, base_url)
                    
                    # Merge sitemap files
                    merge_sitemaps(output_folder, base_url)
                    
                    # Signal task completion to terminate all agents
                    url_claim_manager.signal_task_complete()
                    
                    print("Final merge completed. Exiting merger agent.")
                    url_claim_manager.unregister_agent()
                    break
            
            # If it's time to do a periodic merge
            if current_time - last_merge_time >= interval:
                print("\nPerforming periodic merge...")
                
                # Merge content files
                if output_format == 'txt':
                    merge_txt_files(output_folder, base_url)
                else:
                    merge_xlsx_files(output_folder, base_url)
                
                # Merge sitemap files
                merge_sitemaps(output_folder, base_url)
                
                last_merge_time = current_time
            
            # Show status
            time_until_next = max(0, interval - (current_time - last_merge_time))
            print(f"\rWaiting for next merge in {int(time_until_next)}s... (watching for agent activity)", end="")
            sys.stdout.flush()
            time.sleep(1)
    
    except KeyboardInterrupt:
        print("\nMerger agent interrupted. Performing final merge...")
        
        # Do one final merge
        if output_format == 'txt':
            merge_txt_files(output_folder, base_url)
        else:
            merge_xlsx_files(output_folder, base_url)
        
        merge_sitemaps(output_folder, base_url)
        
        # Signal task completion to terminate all agents
        if 'url_claim_manager' in locals():
            url_claim_manager.signal_task_complete()
            url_claim_manager.unregister_agent()
        
        print("Final merge completed. Exiting merger agent.")
        sys.exit(0)

# For terminal UI
class ProgressDisplay:
    """Manages the terminal UI for displaying multi-agent crawler progress."""
    
    def __init__(self, agent_count, merger_active=True):
        """Initialize the progress display."""
        self.agent_count = agent_count
        self.merger_active = merger_active
        self.agent_statuses = {}
        self.agent_counters = {}
        self.agent_last_url = {}
        self.start_time = datetime.now()
        self.total_urls_processed = 0
        self.total_urls_found = 0
        self.task_complete = False
        self.lock = threading.Lock()
        
        # Initialize status for each agent
        for i in range(1, agent_count + 1):
            agent_id = f"agent-{i}"
            self.agent_statuses[agent_id] = "Starting..."
            self.agent_counters[agent_id] = {"mapped": 0, "unmapped": 0, "processed": 0}
            self.agent_last_url[agent_id] = ""
            
        # Add merger if active
        if merger_active:
            self.agent_statuses["merger"] = "Starting..."
            self.agent_counters["merger"] = {"merged_files": 0, "last_merge": "None"}
    
    def update_agent_status(self, agent_id, status, counters=None, last_url=None):
        """Update the status, counters and last processed URL for an agent."""
        with self.lock:
            self.agent_statuses[agent_id] = status
            
            if counters:
                if agent_id not in self.agent_counters:
                    self.agent_counters[agent_id] = counters
                else:
                    self.agent_counters[agent_id].update(counters)
                
                # Update totals
                if agent_id != "merger" and "processed" in counters:
                    self.total_urls_processed += counters.get("processed", 0)
                if agent_id != "merger" and "found" in counters:
                    self.total_urls_found += counters.get("found", 0)
            
            if last_url:
                self.agent_last_url[agent_id] = last_url
    
    def set_task_complete(self, complete=True):
        """Mark the task as complete."""
        with self.lock:
            self.task_complete = complete
    
    def _get_elapsed_time(self):
        """Get the elapsed time since crawling started."""
        elapsed = datetime.now() - self.start_time
        hours, remainder = divmod(elapsed.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
    
    def _get_urls_per_second(self):
        """Calculate the URLs processed per second."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        if elapsed > 0:
            return self.total_urls_processed / elapsed
        return 0
    
    def render(self):
        """Render the current progress display to the terminal."""
        with self.lock:
            # Build output lines
            output = []
            
            # Show header with separator to visually separate from previous output
            output.append("\n" + "=" * 80)
            output.append(f"  WEB CRAWLER - MULTI-AGENT PROGRESS  |  Elapsed: {self._get_elapsed_time()}")
            output.append("=" * 80)
            
            # Show overall stats
            output.append(f"Total URLs processed: {self.total_urls_processed} | " 
                  f"Rate: {self._get_urls_per_second():.2f} URLs/sec")
            
            # Status
            status_text = "Task Complete" if self.task_complete else "In Progress"
            output.append(f"Status: {status_text}")
                
            output.append("-" * 80)
            
            # Agent status table header
            output.append(f"{'AGENT':^10} | {'STATUS':^15} | {'MAPPED':^8} | {'UNMAPPED':^8} | {'URLS':^8} | {'CURRENT URL':^25}")
            output.append("-" * 80)
            
            # Agent status rows
            for agent_id in sorted(self.agent_statuses.keys()):
                # Different formatting for merger agent
                if agent_id == "merger":
                    continue  # Skip for now, will display separately
                
                status = self.agent_statuses.get(agent_id, "Unknown")
                counters = self.agent_counters.get(agent_id, {})
                last_url = self.agent_last_url.get(agent_id, "")
                
                # Truncate URL if too long
                display_url = last_url[-25:] if len(last_url) > 25 else last_url
                
                output.append(f"{agent_id:^10} | {status:^15} | "
                      f"{counters.get('mapped', 0):^8} | {counters.get('unmapped', 0):^8} | "
                      f"{counters.get('processed', 0):^8} | {display_url:<25}")
            
            # Show merger info if active
            if self.merger_active:
                output.append("-" * 80)
                output.append("MERGER STATUS:")
                merger_status = self.agent_statuses.get("merger", "Unknown")
                merger_counters = self.agent_counters.get("merger", {})
                output.append(f"  Status: {merger_status}")
                output.append(f"  Files merged: {merger_counters.get('merged_files', 0)}")
                output.append(f"  Last merge: {merger_counters.get('last_merge', 'None')}")
            
            output.append("=" * 80)
            output.append("Press Ctrl+C to stop all agents\n")
            
            # Print the complete status display
            print("\n".join(output))
            sys.stdout.flush()

def monitor_agent_logs(progress_display, agent_logs):
    """Monitor agent log files and update the progress display.
    
    Args:
        progress_display (ProgressDisplay): Display to update
        agent_logs (list): List of log file paths to monitor
    """
    # Initialize positions for log files
    log_positions = {log_path: 0 for log_path in agent_logs}
    
    while True:
        for log_path in agent_logs:
            # Extract agent ID from log path
            agent_id = log_path.replace('crawler-', '').replace('.log', '')
            
            try:
                if os.path.exists(log_path):
                    with open(log_path, 'r') as f:
                        # Skip previously read content
                        f.seek(0, os.SEEK_END)
                        file_size = f.tell()
                        
                        # If there's new content
                        if file_size > log_positions[log_path]:
                            # Read from last position
                            f.seek(log_positions[log_path])
                            new_content = f.read()
                            log_positions[log_path] = file_size
                            
                            # Parse log content to update agent status
                            update_agent_status_from_log(agent_id, new_content, progress_display)
            except Exception as e:
                print(f"Error monitoring log for {agent_id}: {e}")
                
        time.sleep(0.5)

def update_agent_status_from_log(agent_id, log_content, progress_display):
    """Parse log content and extract status information."""
    # Extract crawling information
    status = None
    counters = {}
    last_url = None
    
    # Check for crawling activity
    crawling_match = re.search(r"Crawling: (https?://[^\n]+)", log_content)
    if crawling_match:
        status = "Crawling"
        last_url = crawling_match.group(1).strip()
    
    # Check for idle state
    idle_match = re.search(r"Idle: (\d+)s", log_content)
    if idle_match:
        status = f"Idle ({idle_match.group(1)}s)"
    
    # Check for startup
    if "Running as agent:" in log_content:
        status = "Starting"
    
    # Check for task completion
    if "Crawling completed" in log_content or "task completion" in log_content:
        status = "Complete"
    
    # Extract mapped/unmapped counts - improved pattern matching
    mapped_match = re.search(r"Mapped pages: (\d+)", log_content)
    unmapped_match = re.search(r"Unmapped pages: (\d+)", log_content)
    
    if mapped_match:
        counters["mapped"] = int(mapped_match.group(1))
    
    if unmapped_match:
        counters["unmapped"] = int(unmapped_match.group(1))
    
    # Extract links found
    links_match = re.search(r"Found (\d+) links", log_content)
    if links_match:
        counters["found"] = int(links_match.group(1))
    
    # Look for mapped/unmapped in progress output
    mapped_progress = re.search(r"Mapped: (\d+)", log_content)
    unmapped_progress = re.search(r"Unmapped: (\d+)", log_content)
    
    if mapped_progress and "mapped" not in counters:
        counters["mapped"] = int(mapped_progress.group(1))
    
    if unmapped_progress and "unmapped" not in counters:
        counters["unmapped"] = int(unmapped_progress.group(1))
        
    # For merger agent, extract merge info
    if agent_id == "merger":
        if "Performing periodic merge" in log_content or "Final merge" in log_content:
            status = "Merging"
            counters["merged_files"] = counters.get("merged_files", 0) + 1
            counters["last_merge"] = datetime.now().strftime("%H:%M:%S")
        elif "Waiting for next merge" in log_content:
            status = "Waiting"
    
    # Update processed count if we have mapped + unmapped
    if "mapped" in counters and "unmapped" in counters:
        counters["processed"] = counters["mapped"] + counters["unmapped"]
    
    # Update progress display if we have new information
    if status or counters or last_url:
        progress_display.update_agent_status(agent_id, status or "Unknown", counters, last_url)

def create_claim_manager(base_url, agent_id=None, use_redis=False, redis_url=None):
    """Create the appropriate URL claim manager based on configuration.
    
    Args:
        base_url (str): Base URL of the site to crawl
        agent_id (str, optional): Unique identifier for this agent
        use_redis (bool, optional): Whether to use Redis for coordination
        redis_url (str, optional): Redis connection URL if using Redis
        
    Returns:
        The appropriate claim manager instance
    """
    if use_redis:
        logging.info(f"Using Redis-based URL claim manager with URL: {redis_url or 'redis://localhost:6379/0'}")
        return setup_redis_claim_manager(base_url, agent_id, redis_url or "redis://localhost:6379/0")
    else:
        logging.info("Using file-based URL claim manager")
        return UrlClaimManager(base_url, agent_id)

def run_crawler(args):
    """Main crawler function that handles the crawling process."""
    # Set up logging
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    if args.agent_id:
        log_filename = f"crawler-{args.agent_id}.log"
        logging.basicConfig(filename=log_filename, level=logging.INFO, format=log_format)
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)
    
    base_url = args.url
    depth = None if args.depth == "None" else int(args.depth)
    max_pages = args.max_pages
    output_format = args.output_format
    save_frequency = args.save_frequency
    idle_timeout = args.idle_timeout
    agent_id = args.agent_id or str(uuid.uuid4())
    use_js = args.use_js
    
    # If custom JavaScript patterns are provided, add them
    if args.js_patterns:
        # Customize the should_render_with_js function with additional patterns
        global_patterns = globals()['should_render_with_js'].__globals__
        if 'js_patterns' in global_patterns:
            global_patterns['js_patterns'].extend(args.js_patterns)
        logging.info(f"Added custom JavaScript patterns: {args.js_patterns}")
    
    # Set up the robots parser
    robots_parser = RobotsParser(base_url)
    
    # Initialize URL claim manager using the helper function
    claim_manager = create_claim_manager(base_url, agent_id, 
                                        args.use_redis, args.redis_url)
    
    # Initialize site map manager
    sitemap = SitemapManager(base_url)
    
    # Register agent termination signal handler
    def handle_termination(signum, frame):
        logging.info(f"Received termination signal. Shutting down agent {agent_id}.")
        claim_manager.unregister_agent()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, handle_termination)
    
    # Start crawling
    logging.info(f"Running as agent: {agent_id}")
    logging.info(f"Starting crawl from {base_url} with depth={depth}, max_pages={max_pages}, js_enabled={use_js}")
    
    try:
        crawl(base_url, sitemap, base_url, robots_parser, depth, 0, max_pages, 
              output_format, save_frequency, idle_timeout, claim_manager, use_js)
        logging.info(f"Crawl completed successfully.")
    except Exception as e:
        logging.error(f"Error during crawl: {e}")
    finally:
        # Unregister agent from active agents
        claim_manager.unregister_agent()

def signal_handler(signum, frame):
    """Handle termination signals to gracefully exit all agents."""
    print("\nReceived termination signal. Shutting down...")
    # Kill any remaining child processes
    for agent_id, process, _ in agents:
        print(f"Terminating {agent_id}...")
        try:
            process.terminate()
        except:
            pass
            
    # Clean up Redis resources if we were using Redis
    if args.use_redis:
        try:
            # Create a temporary claim manager to clean up resources
            cleanup_manager = create_claim_manager(args.url, "cleanup", 
                                                 args.use_redis, args.redis_url)
            cleanup_manager.reset_crawler_state()
            logging.info("Redis state has been reset")
        except Exception as e:
            logging.error(f"Error cleaning up Redis: {e}")
    
    sys.exit(0)

def main():
    # Global variables needed for signal handler
    global agents
    global args
    
    parser = argparse.ArgumentParser(description='Crawl a website and generate a content map.')
    
    # Required arguments
    parser.add_argument('url', help='URL to start crawling from')
    
    # Optional arguments
    parser.add_argument('--depth', type=str, default='None',
                      help='Maximum crawl depth (default: None for unlimited)')
    parser.add_argument('--max-pages', type=int, default=100,
                      help='Maximum number of pages to crawl (default: 100)')
    parser.add_argument('--output-format', default='txt',
                      help='Output format: txt or xlsx (default: txt)')
    parser.add_argument('--state-dir', default='output',
                      help='Directory to store state files (default: output)')
    parser.add_argument('--idle-timeout', type=int, default=60,
                      help='Seconds to wait while idle before terminating (default: 60)')
    parser.add_argument('--multi-agent', action='store_true',
                      help='Run in multi-agent mode')
    parser.add_argument('--agent-id', help='Agent identifier for multi-agent mode')
    parser.add_argument('--merger', action='store_true',
                      help='Run as a merger agent')
    parser.add_argument('--agent-count', type=int, default=0,
                      help='Number of agents to spawn (default: 0)')
    parser.add_argument('--save-frequency', type=int, default=10,
                      help='How often to save results when crawling (default: 10)')
    parser.add_argument('--merge-interval', type=int, default=10,
                      help='How often to merge results in seconds (default: 10)')
    
    # Redis support
    parser.add_argument('--use-redis', action='store_true',
                      help='Use Redis for URL claim management')
    parser.add_argument('--redis-url', type=str, default="redis://localhost:6379/0",
                      help='Redis connection URL')
                      
    # JavaScript support
    parser.add_argument('--use-js', action='store_true',
                      help='Enable JavaScript rendering for all pages')
    parser.add_argument('--js-patterns', nargs='+', 
                      help='Custom patterns to identify JavaScript-heavy pages')
                      
    args = parser.parse_args()
    
    # Register the global signal handler for graceful termination
    agents = []
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # If Redis is enabled, clear any existing crawler state
        if args.use_redis:
            # Create and immediately reset the claim manager to clear old state
            cleanup_manager = create_claim_manager(args.url, "cleanup", 
                                                  args.use_redis, args.redis_url)
            cleanup_manager.reset_crawler_state()
            print("Redis crawler keys cleared")
        
        # Run merger process if requested
        if args.merger:
            run_merger(args.url, args.output_format, args.state_dir, args.merge_interval)
            return
            
        # Create agent processes if agent_count is specified
        if args.agent_count > 0:
            progress_display = ProgressDisplay(args.agent_count)
            
            # Start the agents
            for i in range(args.agent_count):
                agent_id = f"agent-{i+1}"
                cmd = [
                    sys.executable, __file__, args.url,
                    "--multi-agent", "--agent-id", agent_id,
                    "--depth", args.depth,
                    "--max-pages", str(args.max_pages),
                    "--output-format", args.output_format,
                    "--state-dir", args.state_dir,
                    "--idle-timeout", str(args.idle_timeout),
                    "--save-frequency", str(args.save_frequency)
                ]
                
                # Add Redis options if enabled
                if args.use_redis:
                    cmd.extend(["--use-redis"])
                    if args.redis_url:
                        cmd.extend(["--redis-url", args.redis_url])
                
                # Add JavaScript options if enabled
                if args.use_js:
                    cmd.extend(["--use-js"])
                if args.js_patterns:
                    cmd.extend(["--js-patterns"] + args.js_patterns)
                
                process = subprocess.Popen(cmd)
                agents.append((agent_id, process, None))
                
            # Set up log monitoring threads
            agent_logs = [f"crawler-{agent_id}.log" for agent_id, _, _ in agents]
            monitor_thread = threading.Thread(
                target=monitor_agent_logs,
                args=(progress_display, agent_logs),
                daemon=True
            )
            monitor_thread.start()
            
            # Wait for all agents to complete or until interrupted
            try:
                while True:
                    progress_display.render()
                    time.sleep(0.5)
                    
                    # Check if all agents have terminated
                    all_terminated = True
                    for i, (agent_id, process, _) in enumerate(agents):
                        if process.poll() is None:
                            all_terminated = False
                        else:
                            # Agent terminated, update status if we haven't yet
                            if process.returncode != 0:
                                progress_display.update_agent_status(
                                    agent_id, "ERROR", 
                                    {"visited": "?", "unvisited": "?"}
                                )
                    
                    if all_terminated:
                        progress_display.set_task_complete()
                        progress_display.render()
                        break
                        
            except KeyboardInterrupt:
                # Handle in signal handler
                pass
                
            # Merge results after all agents complete
            if args.output_format == 'txt':
                merge_txt_files(args.state_dir, args.url)
            elif args.output_format == 'xlsx':
                merge_xlsx_files(args.state_dir, args.url)
            merge_sitemaps(args.state_dir, args.url)
        else:
            # Single-agent mode
            run_crawler(args)
    finally:
        # Clean up Redis resources if using Redis
        if args.use_redis:
            try:
                cleanup_manager = create_claim_manager(args.url, "cleanup", 
                                                      args.use_redis, args.redis_url)
                cleanup_manager.reset_crawler_state()
                print("Redis state has been reset")
            except Exception as e:
                print(f"Error cleaning up Redis: {e}")

def should_render_with_js(url):
    """Determines if a URL likely needs JavaScript rendering.
    
    Args:
        url (str): The URL to check
        
    Returns:
        bool: True if the URL likely needs JavaScript, False otherwise
    """
    # Common patterns that often indicate JavaScript-heavy pages
    js_patterns = [
        '/ajax', 
        '/spa/', 
        '/app/', 
        'angular', 
        'react', 
        'vue',
        'javascript',
        'interactive',
        'dynamic'
    ]
    
    # Check URL against patterns
    url_lower = url.lower()
    for pattern in js_patterns:
        if pattern in url_lower:
            logging.info(f"URL {url} matches JS pattern '{pattern}', using JavaScript rendering")
            return True
    
    return False

if __name__ == '__main__':
    main()
