"""JavaScript content rendering module using Playwright.

This module provides functionality to render web pages with JavaScript
using Playwright, a headless browser automation library.
"""

import asyncio
import logging
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

async def fetch_with_playwright(url, timeout=30000, wait_until='networkidle'):
    """Fetches a web page using Playwright with JavaScript execution.
    
    Args:
        url (str): The URL to fetch
        timeout (int): Maximum navigation time in milliseconds
        wait_until (str): Navigation wait condition ('networkidle', 'load', 'domcontentloaded')
        
    Returns:
        tuple: (html_content, soup_object) or (None, None) if failed
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            
            logging.info(f"Loading {url} with Playwright")
            response = await page.goto(url, timeout=timeout, wait_until=wait_until)
            
            if not response or response.status >= 400:
                logging.error(f"Failed to load URL: {url}, status: {response.status if response else 'unknown'}")
                await browser.close()
                return None, None
            
            # Wait for content to settle (additional wait after networkidle)
            await page.wait_for_timeout(1000)
            
            # Get the rendered HTML content
            html_content = await page.content()
            
            await browser.close()
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            return html_content, soup
    except Exception as e:
        logging.error(f"Playwright error for {url}: {e}")
        return None, None

def fetch_with_js(url, timeout=30):
    """Synchronous wrapper for Playwright fetching.
    
    Args:
        url (str): The URL to fetch
        timeout (int): Timeout in seconds
        
    Returns:
        tuple: (html_content, soup_object) or (None, None) if failed
    """
    try:
        # Convert timeout to milliseconds for Playwright
        return asyncio.run(fetch_with_playwright(url, timeout * 1000))
    except Exception as e:
        logging.error(f"Error in fetch_with_js for {url}: {e}")
        return None, None 