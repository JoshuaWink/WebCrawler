"""Manages website crawl state and generates visual sitemaps."""

import logging
import os
from urllib.parse import urlparse, urljoin

class SitemapManager:
    """Manages the state of a website crawl and generates visual sitemaps.
    
    This class tracks visited and unvisited URLs, maintains parent-child relationships
    between pages, and generates DOT format sitemaps showing the website structure.
    
    Args:
        base_url (str, optional): The starting URL for the crawl. Used to create
            the output directory structure.
    """
    def __init__(self, base_url=None):
        self.visited_urls = set()
        self.unvisited_urls = []
        self.external_links = set()
        self.mapped_count = 0
        self.unmapped_count = 0
        self.page_contents = {}
        self.parent_urls = {}
        self.external_edges = []  # store (source, external_target)
        if base_url:
            parsed = urlparse(base_url)
            domain = parsed.netloc
            path = parsed.path.strip('/').replace('/', '-')
            if not domain:
                domain = base_url.split('/')[0]
            self.output_folder = f"output/{domain}-{path}" if path else f"output/{domain}"
        else:
            self.output_folder = "output"
        os.makedirs(self.output_folder, exist_ok=True)

        
    def add_url(self, base_url, link_url):
        """Adds a URL to be crawled and tracks its relationship to the parent URL.
        
        Args:
            base_url (str): The parent URL where this link was found
            link_url (str): The URL to be added to the crawl queue
        """
        absolute_url = urljoin(base_url, link_url)
        is_external = self.is_external(base_url, absolute_url)
        if absolute_url != base_url and absolute_url not in self.visited_urls and absolute_url not in self.unvisited_urls:
            self.unvisited_urls.append(absolute_url)
            self.unmapped_count += 1
            self.parent_urls[absolute_url] = {'parent': base_url, 'is_external': is_external}

        
    def mark_visited(self, url):
        """Marks a URL as visited and updates the sitemap.
        
        Args:
            url (str): The URL that has been successfully crawled
        """
        self.visited_urls.add(url)
        self.mapped_count += 1
        # Don't update sitemap file here - too frequent and slow
        # Instead, we'll update periodically in the main crawler loop

    def log_external_link(self, url):
        pass

        
    def is_external(self, base_url, link_url):
        """Determines if a URL is external to the base domain.
        
        Args:
            base_url (str): The source URL to compare against
            link_url (str): The URL to check
            
        Returns:
            bool: True if the URL is external, False if internal
        """
        base_domain = urlparse(base_url).netloc
        link_domain = urlparse(link_url).netloc
        if not link_domain:  # Check if link_url is a relative URL
            return False  # Treat relative URLs as internal
        return base_domain != link_domain

        
    def get_next_url(self):
        """Gets the next URL to crawl from the queue.
        
        Returns:
            str: The next URL to crawl, or None if queue is empty
        """
        if self.unvisited_urls:
            return self.unvisited_urls.pop()
        return None

        
    def has_unvisited_urls(self):
        """Checks if there are any URLs left to crawl.
        
        Returns:
            bool: True if there are URLs in the queue, False otherwise
        """
        return len(self.unvisited_urls) > 0

        
    def update_sitemap_file(self, filename=None):
        """Updates the sitemap DOT file with the current state of the crawl.
        
        Generates a GraphViz DOT file showing the website structure, with:
        - Internal pages as circles
        - External links as boxes
        - Parent-child relationships as solid arrows
        - Cross-links as dashed red arrows
        - External links as dotted blue arrows
        
        Args:
            filename (str, optional): Custom filename for the sitemap. If not provided,
                uses the pattern: domain-sitemap_YYYY-MM-DD.dot
        """
        if not filename:
            from datetime import datetime
            # Extract domain from output_folder path
            domain = os.path.basename(self.output_folder).split('-')[0]
            current_date = datetime.now().strftime("%Y-%m-%d")
            filename = f"{domain}-sitemap_{current_date}.dot"
            
        filepath = os.path.join(self.output_folder, filename)
        try:
            # Skip if we have too many nodes - would create an unreadable graph
            if len(self.visited_urls) > 500:
                # Create a simplified graph with just counts
                with open(filepath, "w") as f:
                    f.write("/* Generated Site Map (Simplified due to large size) */\n")
                    f.write("digraph SiteMap {\n")
                    f.write("    /* Summary Information */\n")
                    f.write(f'    label="Site Map: {len(self.visited_urls)} pages crawled, {len(self.unvisited_urls)} pending";\n')
                    f.write("    labelloc=t;\n")
                    f.write("    node1 [label=\"Crawled Pages\", shape=box];\n")
                    f.write("    node2 [label=\"Pending Pages\", shape=box];\n")
                    f.write("    node1 -> node2 [style=invis];\n")
                    f.write("}\n")
                return
            
            with open(filepath, "w") as f:
                # Write header and graph attributes
                f.write("/* Generated Site Map */\n")
                f.write("digraph SiteMap {\n")
                f.write("    /* General Graph Attributes */\n")
                f.write("    graph [layout=neato, overlap=false, splines=true];\n")
                f.write('    node [shape=circle, fontname="Arial", fontsize=12, style=filled, fillcolor=lightgray];\n')
                f.write("    edge [fontname=\"Arial\", fontsize=10, fillcolor=orange];\n\n")

                # Declare all nodes with clickable URLs
                f.write("    /* Declare unique nodes with clickable links */\n")
                f.write("    {\n")
                for url in self.visited_urls:
                    f.write(f'        "{url}" [URL="{url}"];\n')
                f.write("    }\n\n")

                # Write hierarchical structure
                f.write("    /* Hierarchical Structure */\n")
                for url in self.visited_urls:
                    if url in self.parent_urls:
                        parent_url = self.parent_urls[url]['parent']
                        is_external = self.parent_urls[url]['is_external']
                        if is_external:
                            f.write(f'    "{parent_url}" -> "{url}" [color=blue];\n')
                            f.write(f'    "{url}" [shape=box, fillcolor=gold];\n')
                        else:
                            f.write(f'    "{parent_url}" -> "{url}";\n')
                    else:
                        f.write(f'    "{url}" [fillcolor=lightblue];\n')

                # Write cross-links - LIMIT THIS FOR LARGE SITES
                # This is often a source of performance issues for large crawls
                if len(self.visited_urls) < 100:
                    f.write("\n    /* Cross-Links to Show Page Interconnections */\n")
                    f.write("    edge [color=red, style=dashed];\n")
                    processed_urls = set()
                    for url in self.visited_urls:
                        for other_url in self.visited_urls:
                            if url != other_url and (url, other_url) not in processed_urls and (other_url, url) not in processed_urls:
                                if url not in self.parent_urls and other_url not in self.parent_urls:
                                    f.write(f'    "{url}" -> "{other_url}";\n')
                                    processed_urls.add((url, other_url))

                # Write external edges - LIMIT FOR LARGE SITES
                max_external_edges = 100  # Limit number of external edges in the DOT file
                f.write("\n    /* External Links */\n")
                f.write("    node [fillcolor=gold];\n")
                for i, (source, target) in enumerate(self.external_edges):
                    if i >= max_external_edges:
                        f.write(f'    /* {len(self.external_edges) - max_external_edges} more external links omitted */\n')
                        break
                    f.write(f'    "{source}" -> "{target}" [URL="{target}", style=dotted, color=blue];\n')

                f.write("}\n")
        except Exception as e:
            logging.error(f"Error updating sitemap file: {e}")

        
    def add_external_edge(self, parent_url, external_url):
        """Records an external link found during crawling.
        
        Args:
            parent_url (str): The internal page where the external link was found
            external_url (str): The external URL that was linked to
        """
        self.external_edges.append((parent_url, external_url))
        self.update_sitemap_file()

    def add_redirect(self, source_url, target_url):
        """Records a URL redirect in the sitemap.
        
        Args:
            source_url (str): Original URL that redirected
            target_url (str): Final URL after redirect
        """
        # Mark the source URL as visited if it's not already
        if source_url not in self.visited_urls:
            self.visited_urls.add(source_url)
        
        # Add parent relationship for the target URL if not already set
        if target_url not in self.parent_urls:
            self.parent_urls[target_url] = {
                'parent': source_url, 
                'is_external': self.is_external(source_url, target_url)
            }
        
        # If the source URL was in the unvisited queue, replace it with the target URL
        if source_url in self.unvisited_urls:
            self.unvisited_urls.remove(source_url)
        
        # Add the target URL to unvisited if it's not already in visited or unvisited
        if target_url not in self.visited_urls and target_url not in self.unvisited_urls:
            self.unvisited_urls.append(target_url)
        
        # Update the sitemap file to reflect the redirect
        self.update_sitemap_file()
