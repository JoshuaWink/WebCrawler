import csv
import time
import os
import psutil
from datetime import datetime

class CrawlerBenchmark:
    """Collect performance metrics during web crawling."""
    
    def __init__(self, target_url, workers, batch_size, scope=None, depth=None, max_pages=None):
        self.target_url = target_url
        self.workers = workers
        self.batch_size = batch_size
        self.scope = scope
        self.depth = depth
        self.max_pages = max_pages
        self.start_time = time.time()
        self.end_time = None
        
        # Performance metrics
        self.pages_crawled = 0
        self.http_status_codes = {}
        self.errors = 0
        self.redirects = 0
        self.total_content_size = 0
        self.peak_memory_mb = 0
        self.largest_page_size = 0
        self.largest_page_url = ""
        
        # Interval tracking
        self.last_metric_check = self.start_time
        self.metric_check_interval = 10  # seconds
        
        # Create benchmarks directory if needed
        self.benchmarks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'benchmarks')
        os.makedirs(self.benchmarks_dir, exist_ok=True)
    
    def update_metrics(self, pages_mapped):
        """Update benchmark metrics during crawl."""
        self.pages_crawled = pages_mapped
        
        # Update memory usage periodically
        current_time = time.time()
        if current_time - self.last_metric_check > self.metric_check_interval:
            current_memory = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)  # MB
            self.peak_memory_mb = max(self.peak_memory_mb, current_memory)
            self.last_metric_check = current_time
    
    def record_status_code(self, status_code):
        """Record HTTP status code frequency."""
        self.http_status_codes[status_code] = self.http_status_codes.get(status_code, 0) + 1
    
    def record_error(self):
        """Record a crawl error."""
        self.errors += 1
    
    def record_redirect(self):
        """Record a redirect."""
        self.redirects += 1
    
    def record_content(self, url, content):
        """Record content metrics."""
        content_size = len(content)
        self.total_content_size += content_size
        
        if content_size > self.largest_page_size:
            self.largest_page_size = content_size
            self.largest_page_url = url
    
    def complete(self):
        """Finalize benchmark and save results."""
        self.end_time = time.time()
        current_memory = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)  # MB
        self.peak_memory_mb = max(self.peak_memory_mb, current_memory)
        self._save_to_csv()
        return self._get_summary()
    
    def _get_summary(self):
        """Get benchmark summary as a dictionary."""
        elapsed_time = self.end_time - self.start_time
        return {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'target_url': self.target_url,
            'workers': self.workers,
            'batch_size': self.batch_size,
            'scope': self.scope or self.target_url,
            'depth_limit': self.depth if self.depth and self.depth > 0 else "unlimited",
            'max_pages': self.max_pages if self.max_pages and self.max_pages > 0 else "unlimited",
            'pages_crawled': self.pages_crawled,
            'elapsed_time': elapsed_time,
            'avg_crawl_speed': self.pages_crawled / elapsed_time if elapsed_time > 0 else 0,
            'peak_memory_mb': self.peak_memory_mb,
            'errors': self.errors,
            'redirects': self.redirects,
            'total_content_mb': self.total_content_size / (1024 * 1024),
            'avg_page_size_kb': (self.total_content_size / self.pages_crawled / 1024) if self.pages_crawled > 0 else 0,
            'status_codes': self.http_status_codes
        }
    
    def _save_to_csv(self):
        """Save benchmark results to CSV file."""
        csv_path = os.path.join(self.benchmarks_dir, 'crawler_benchmarks.csv')
        file_exists = os.path.isfile(csv_path)
        summary = self._get_summary()
        
        for code, count in summary['status_codes'].items():
            summary[f'status_{code}'] = count
        del summary['status_codes']
        
        with open(csv_path, 'a', newline='') as csvfile:
            fieldnames = list(summary.keys())
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(summary)
