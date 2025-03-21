# Web Crawler

A Python-based web crawler that maps website structure and extracts content. This tool can generate text, CSV and Excel outputs of crawled pages along with visual sitemaps.

## Features

- Crawls websites and extracts content
- Generates visual sitemaps in DOT format
- Supports TXT, CSV, and XLSX output formats
- Configurable crawl depth and page limits
- Handles both internal and external links
- Normalizes URLs and removes unwanted parameters
- Handles URL redirects automatically
- Supports custom URL scope limits for targeted crawling
- Batch processing to save data periodically and optimize memory usage
- Multi-threaded crawling with configurable number of workers

## Installation

1. Clone the repository
2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Basic usage:
```bash
python crawler.py <URL>
```

Options:
- `--depth`: Maximum crawl depth (default: unlimited)
- `--max-pages`: Maximum number of pages to crawl (default: unlimited)
- `--output-format`: Output format, either 'txt', 'csv', or 'xlsx' (default: txt)
- `--scope`: URL scope limit (default: same as starting URL)
- `--batch-size`: Number of pages to process before saving batch data (default: 5)
- `--workers`: Number of concurrent workers for crawling (default: 4)
- `--verbose`: Enable detailed logging output (by default, only warnings and errors are shown)

Examples:
```bash
python crawler.py https://example.com --depth 2 --max-pages 10 --output-format xlsx

# Output as CSV:
python crawler.py https://example.com --output-format csv

# Crawl with custom scope limit:
python crawler.py https://opensource.ebay.com/ebayui-core/ --scope https://opensource.ebay.com/ebayui-core/docs/

# Crawl with custom batch size:
python crawler.py https://example.com --batch-size 10

# Crawl with 8 concurrent workers for faster processing:
python crawler.py https://example.com --workers 8
```

## Output

The crawler generates two types of output:
1. Content files (TXT, CSV, or XLSX) containing extracted text from crawled pages
2. A sitemap.dot file visualizing the website structure

Output files are organized in folders by domain name in the `output` directory.
Detailed logs are saved to `crawler.log` in the output folder.

## Dependencies

- requests: For making HTTP requests
- beautifulsoup4: For HTML parsing
- xlsxwriter: For Excel file generation
