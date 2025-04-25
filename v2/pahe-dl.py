#!/usr/bin/env python3
"""
AnimePahe Downloader - Optimized Implementation with Rate Limiting and Connection Pooling
"""
import os
import re
import ssl
import time
import random
import logging
import argparse
import concurrent.futures
import threading
from urllib.parse import urljoin, quote, urlparse
from collections import deque
from tqdm import tqdm
import cloudscraper
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("anime_dl.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
]

class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_ciphers('HIGH:!DH:!aNULL')
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)

class RequestThrottler:
    """Rate limiter for HTTP requests to prevent too many requests errors"""
    def __init__(self, requests_per_minute=15, burst_capacity=3):
        self.rate = requests_per_minute / 60.0  # requests per second
        self.burst_capacity = burst_capacity
        self.tokens = burst_capacity
        self.last_time = time.time()
        self.lock = threading.Lock()
        
    def wait_for_token(self):
        """Wait for and consume a token"""
        with self.lock:
            current_time = time.time()
            time_passed = current_time - self.last_time
            self.last_time = current_time
            
            # Add new tokens based on time passed
            self.tokens = min(self.burst_capacity, self.tokens + time_passed * self.rate)
            
            if self.tokens < 1:
                # Calculate wait time to get 1 token
                wait_time = (1 - self.tokens) / self.rate
                time.sleep(wait_time)
                self.tokens = 0
                self.last_time = time.time()
            else:
                # Consume a token
                self.tokens -= 1

class BrowserPool:
    """Manages a pool of browser instances with efficient allocation"""
    def __init__(self, max_size=3, base_dl_dir="downloads"):
        self.max_size = max_size
        self.base_dl_dir = base_dl_dir
        self.available = deque()
        self.in_use = set()
        self.lock = threading.Lock()
        self.creation_lock = threading.Lock()  # Separate lock for browser creation
        
    def get_browser(self):
        """Get an available browser or create a new one if needed"""
        with self.lock:
            if self.available:
                browser, dl_dir = self.available.popleft()
                self.in_use.add((browser, dl_dir))
                return browser, dl_dir
        
        # No browser available, create a new one with a separate lock
        with self.creation_lock:
            # Double-check if a browser became available while waiting
            with self.lock:
                if self.available:
                    browser, dl_dir = self.available.popleft()
                    self.in_use.add((browser, dl_dir))
                    return browser, dl_dir
                
            # Create new browser with randomized download directory
            worker_id = random.randint(1000, 9999)
            dl_dir = os.path.join(self.base_dl_dir, f"worker_{worker_id}")
            os.makedirs(dl_dir, exist_ok=True)
            
            browser = self._create_browser(dl_dir)
            
            # Add to in-use set
            with self.lock:
                self.in_use.add((browser, dl_dir))
            
            return browser, dl_dir
            
    def return_browser(self, browser, dl_dir):
        """Return a browser to the pool or close it"""
        with self.lock:
            # Remove from in-use set
            if (browser, dl_dir) in self.in_use:
                self.in_use.remove((browser, dl_dir))
            
            # If we're below capacity, add it back to the pool
            if len(self.available) < self.max_size:
                self.available.append((browser, dl_dir))
            else:
                # Otherwise close it
                threading.Thread(target=self._close_browser, args=(browser,)).start()
    
    def _create_browser(self, dl_dir):
        """Create a new browser instance with proper configuration"""
        options = uc.ChromeOptions()
        
        # Configure Chrome with download preferences
        prefs = {
            "download.default_directory": os.path.abspath(dl_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": False,
            "plugins.always_open_pdf_externally": True,
            # Limit resource usage
            "profile.default_content_setting_values.images": 2,  # Don't load images
        }
        options.add_experimental_option("prefs", prefs)
        
        options.add_argument("--headless=new")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1280,720")  # Smaller window size
        
        # Set random user agent to avoid detection patterns
        user_agent = random.choice(USER_AGENTS)
        options.add_argument(f"--user-agent={user_agent}")
        
        # Create the driver with proper configuration
        driver = uc.Chrome(
            options=options,
            enable_cdp_events=True
        )
        
        # Set up download behavior
        driver.execute_cdp_cmd('Page.setDownloadBehavior', {
            'behavior': 'allow',
            'downloadPath': os.path.abspath(dl_dir)
        })
        
        return driver
    
    def _close_browser(self, browser):
        """Safely close a browser instance"""
        try:
            browser.quit()
        except Exception as e:
            logger.warning(f"Error closing browser: {str(e)}")
    
    def close_all(self):
        """Close all browser instances"""
        with self.lock:
            browsers = list(self.available)
            self.available.clear()
        
        # Close available browsers
        for browser, _ in browsers:
            try:
                browser.quit()
            except Exception as e:
                logger.warning(f"Error closing browser: {str(e)}")
        
        # Close in-use browsers
        with self.lock:
            browsers = list(self.in_use)
            self.in_use.clear()
        
        for browser, _ in browsers:
            try:
                browser.quit()
            except Exception as e:
                logger.warning(f"Error closing browser: {str(e)}")

class AnimeDownloader:
    def __init__(self, dl_dir="downloads", max_workers=3, requests_per_minute=20):
        self.base_url = "https://animepahe.ru"
        self.dl_dir = dl_dir
        self.max_workers = max_workers
        
        # Create browser pool
        self.browser_pool = BrowserPool(max_size=max_workers, base_dl_dir=dl_dir)
        
        # Create primary browser for non-parallel tasks
        self.driver = self._init_browser(dl_dir)
        
        # Set up request throttling
        self.throttler = RequestThrottler(requests_per_minute=requests_per_minute)
        
        # Initialize HTTP session with retries and connection pooling
        self._init_session()
        
        # Create download directory
        os.makedirs(self.dl_dir, exist_ok=True)
        
        logger.info(f"Initialized downloader with {max_workers} workers and {requests_per_minute} requests/minute limit")

    def _init_browser(self, dl_dir):
        """Initialize primary browser instance"""
        options = uc.ChromeOptions()
        
        # Configure Chrome to save downloads
        prefs = {
            "download.default_directory": os.path.abspath(dl_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": False,
            "plugins.always_open_pdf_externally": True,
            # Limit resource usage
            "profile.default_content_setting_values.images": 2,  # Don't load images
        }
        options.add_experimental_option("prefs", prefs)
        
        options.add_argument("--headless=new")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        
        # Set random user agent
        user_agent = random.choice(USER_AGENTS)
        options.add_argument(f"--user-agent={user_agent}")
        
        driver = uc.Chrome(
            options=options,
            enable_cdp_events=True
        )
        driver.set_window_size(1280, 720)
        
        # Set up download behavior
        driver.execute_cdp_cmd('Page.setDownloadBehavior', {
            'behavior': 'allow',
            'downloadPath': os.path.abspath(dl_dir)
        })
        
        return driver

    def _init_session(self):
        """Configure cloudscraper session with proper retry and connection pooling"""
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        # Create TLS adapter with retry strategy
        adapter = TLSAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
        
        # Create cloudscraper session
        self.sess = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
            delay=5,
            interpreter='js2py'
        )
        
        # Set random user agent
        self.sess.headers.update({
            'User-Agent': random.choice(USER_AGENTS),
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': self.base_url,
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        })
        
        # Mount adapters
        self.sess.mount('https://', adapter)
        self.sess.mount('http://', adapter)

    def _random_delay(self, min_seconds=1.0, max_seconds=3.5):
        """Human-like random delay with jitter"""
        # Use triangular distribution for more human-like timing
        delay = random.triangular(min_seconds, max_seconds, (min_seconds + max_seconds) / 2)
        # Add microsecond jitter
        delay += random.random() * 0.001
        time.sleep(delay)
        return delay

    def _req(self, url, retry=3):
        """Smart request handler with rate limiting and retry logic"""
        # Apply rate limiting
        self.throttler.wait_for_token()
        
        for attempt in range(retry):
            try:
                resp = self.sess.get(url, timeout=(10, 30))  # (connect, read) timeouts
                
                # Check for common anti-bot challenges
                if "DDoS-Guard" in resp.text or "Are you a human" in resp.text or "captcha" in resp.text.lower():
                    logger.warning(f"Protection detected on attempt {attempt+1}, using browser fallback")
                    
                    # Try with browser
                    self.driver.get(url)
                    self._random_delay(2.0, 4.0)  # Give more time for protection bypass
                    
                    # Wait for page to load
                    WebDriverWait(self.driver, 30).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                    
                    # Refresh session cookies from browser
                    cookies = self.driver.get_cookies()
                    for c in cookies:
                        self.sess.cookies.set(c['name'], c['value'], domain=c['domain'])
                    
                    # Try request again with updated cookies
                    self.throttler.wait_for_token()  # Apply rate limiting again
                    resp = self.sess.get(url, timeout=(10, 30))
                
                return resp
                
            except Exception as e:
                logger.warning(f"Request failed (attempt {attempt+1}/{retry}): {str(e)}")
                wait_time = 2 ** (attempt + 1)  # Exponential backoff
                logger.info(f"Waiting {wait_time}s before retrying...")
                time.sleep(wait_time)
        
        logger.error(f"All request attempts failed for: {url}")
        return None

    def search(self, query):
        """Search for anime titles with enhanced error handling"""
        logger.info(f"Searching for: {query}")
        search_url = f"{self.base_url}/api?m=search&q={query}"
        
        resp = self._req(search_url)
        
        if resp and resp.status_code == 200:
            try:
                search_results = resp.json()
                data = search_results.get('data', [])
                logger.info(f"Found {len(data)} results")
                return {item['title']: item['session'] for item in data}
            except ValueError as e:
                logger.error(f"Failed to parse search results: {str(e)}")
                return {}
        return {}

    def fetch_episodes(self, session_id, start, end):
        """Get episode list for anime session with pagination handling"""
        logger.info(f"Fetching episodes {start}-{end}")
        eps = {}
        page = 1
        last_page = None
        
        while True:
            # Stop if we know we've reached the last page
            if last_page is not None and page > last_page:
                break
                
            api_url = f"{self.base_url}/api?m=release&id={session_id}&sort=episode_asc&page={page}"
            resp = self._req(api_url)
            
            if not resp or resp.status_code != 200:
                logger.error(f"Failed to fetch episode data for page {page}")
                break
                
            try:
                data = resp.json()
                last_page = data.get('last_page', 1)
                
                for ep in data.get('data', []):
                    try:
                        ep_num = float(ep['episode'])  # Handle episode numbers like 13.5
                        int_ep_num = int(ep_num)
                        
                        # Include the episode if it's within our range
                        if start <= int_ep_num <= end:
                            eps[int_ep_num] = f"{self.base_url}/play/{session_id}/{ep['session']}"
                    except (ValueError, KeyError) as e:
                        logger.warning(f"Failed to process episode: {str(e)}")
                        continue
                    
                page += 1
                self._random_delay(1.5, 3.0)  # Slightly longer delay between pages
                
            except ValueError as e:
                logger.error(f"Failed to parse episode data: {str(e)}")
                break
            
        logger.info(f"Found {len(eps)} episodes")
        return eps

    def _extract_download_links(self, episode_url, driver, quality_pref=1080):
        """Extract download links from episode page with improved parsing"""
        logger.info(f"Extracting download links from: {episode_url}")
        
        try:
            # First try with regular session
            resp = self._req(episode_url)
            
            if not resp or resp.status_code != 200:
                # If that fails, try with browser
                logger.info("Using browser to extract download links")
                driver.get(episode_url)
                
                # Wait for download menu to appear
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.ID, "pickDownload"))
                )
                html = driver.page_source
                soup = BeautifulSoup(html, 'html.parser')
            else:
                soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Look for download dropdown
            download_menu = soup.select_one("#pickDownload")
            if not download_menu:
                logger.warning("Download menu not found on page")
                
                # Fall back to direct browser extraction if parsing fails
                driver.get(episode_url)
                self._random_delay(2.0, 3.0)
                
                try:
                    # Wait for download button and click it to show menu
                    download_btn = WebDriverWait(driver, 20).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.dropdown-toggle[data-bs-toggle='dropdown']"))
                    )
                    download_btn.click()
                    self._random_delay(1.0, 2.0)
                    
                    # Get all download links from dropdown
                    download_items = driver.find_elements(By.CSS_SELECTOR, "#pickDownload a.dropdown-item")
                    download_links = {}
                    
                    for link in download_items:
                        text = link.text.strip()
                        href = link.get_attribute('href')
                        
                        resolution_match = re.search(r'(\d+)p', text)
                        if resolution_match and href:
                            resolution = int(resolution_match.group(1))
                            download_links[resolution] = href
                    
                    if not download_links:
                        logger.error("No download links found after browser fallback")
                        return None
                    
                    logger.info(f"Found download options via browser: {list(download_links.keys())}")
                    
                    # Select preferred quality or best available
                    if quality_pref in download_links:
                        selected = quality_pref
                    else:
                        # Get closest available quality
                        available = sorted(download_links.keys())
                        selected = min(available, key=lambda x: abs(x - quality_pref))
                        logger.info(f"Selected closest quality: {selected}p")
                        
                    return download_links[selected]
                    
                except Exception as e:
                    logger.error(f"Browser extraction fallback failed: {str(e)}")
                    return None
                    
            # Parse download links
            download_links = {}
            for link in download_menu.select("a.dropdown-item"):
                text = link.text.strip()
                href = link.get('href')
                
                # Parse resolution from link text (e.g., "SubsPlease · 1080p (131MB)")
                resolution_match = re.search(r'(\d+)p', text)
                if resolution_match and href:
                    resolution = int(resolution_match.group(1))
                    download_links[resolution] = href
            
            if not download_links:
                logger.warning("No valid download links found")
                return None
                
            logger.info(f"Found download options: {list(download_links.keys())}")
            
            # Select preferred quality or best available
            if quality_pref in download_links:
                selected = quality_pref
            else:
                # Get closest available quality
                available = sorted(download_links.keys())
                selected = min(available, key=lambda x: abs(x - quality_pref))
                logger.info(f"Selected closest quality: {selected}p")
                
            return download_links[selected]
            
        except Exception as e:
            logger.error(f"Error extracting download links: {str(e)}")
            return None

    def _get_pahe_kwik_link(self, pahe_url, driver):
        """Navigate pahe.win gateway to get kwik link with improved reliability"""
        logger.info(f"Navigating to pahe.win gateway: {pahe_url}")
        
        try:
            driver.get(pahe_url)
            
            # Wait with random delay to avoid detection
            self._random_delay(4.0, 6.0)
            
            # Check if we're redirected to kwik directly
            current_url = driver.current_url
            if "kwik.cx" in current_url or "kwik.si" in current_url:
                logger.info(f"Redirected to kwik: {current_url}")
                return current_url
                
            # Otherwise look for kwik link on the page
            try:
                # Try different selectors to find the kwik link
                selectors = [
                    "a[href*='kwik']",
                    "a.button.is-primary",
                    "a.button"
                ]
                
                # Try each selector
                for selector in selectors:
                    try:
                        kwik_link = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        href = kwik_link.get_attribute("href")
                        if href and ("kwik.cx" in href or "kwik.si" in href):
                            logger.info(f"Found kwik link with selector '{selector}': {href}")
                            return href
                    except:
                        continue
                
                # If we didn't find the link, try extracting from page source
                html = driver.page_source
                soup = BeautifulSoup(html, 'html.parser')
                
                # Look for any link containing 'kwik'
                for a in soup.find_all('a', href=True):
                    if 'kwik' in a['href']:
                        href = a['href']
                        logger.info(f"Found kwik link from page source: {href}")
                        return href
                
                logger.warning("No kwik link found on page after trying all methods")
                return None
                
            except Exception as e:
                logger.warning(f"Error finding kwik link: {str(e)}")
                return None
                
        except Exception as e:
            logger.error(f"Error navigating pahe gateway: {str(e)}")
            return None

    def _handle_kwik_form_submission(self, url, output_path, driver, worker_dl_dir):
        """Handle Kwik page form submission with improved reliability"""
        logger.info(f"Processing Kwik link: {url}")
        
        try:
            # Navigate to the kwik page
            driver.get(url)
            self._random_delay(2.0, 3.0)
            
            # Setup monitoring for downloads
            output_filename = os.path.basename(output_path)
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            
            # Setup DevTools Protocol listener for downloads
            driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": worker_dl_dir
            })
            
            # Scroll down slightly to see the button (human-like behavior)
            driver.execute_script("window.scrollBy(0, window.innerHeight * 0.4);")
            self._random_delay(0.8, 1.5)
            
            # Track if we've submitted the form
            form_submitted = False
            
            # Strategy 1: Try to locate and click the download button
            try:
                # Wait for the download form to appear (multiple possible selectors)
                selectors = [
                    "form button.button.is-success", 
                    "button.button.is-success",
                    "form input[type='submit']",
                    "a.button.is-success"
                ]
                
                # Try each selector
                for selector in selectors:
                    try:
                        download_button = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        # Wait a moment and click
                        self._random_delay(0.5, 1.0)
                        download_button.click()
                        logger.info(f"Clicked download button with selector: {selector}")
                        form_submitted = True
                        break
                    except:
                        continue
                        
            except Exception as e:
                logger.warning(f"Failed to click download button: {str(e)}")
            
            # Strategy 2: If button click didn't work, try to submit the form programmatically
            if not form_submitted:
                try:
                    # Find the form and extract details
                    form = driver.find_element(By.CSS_SELECTOR, "form")
                    form_action = form.get_attribute('action')
                    
                    # Try to get CSRF token
                    try:
                        csrf_token = driver.find_element(By.CSS_SELECTOR, "input[name='_token']").get_attribute('value')
                    except:
                        csrf_token = None
                    
                    logger.info(f"Found form action: {form_action}")
                    
                    # Submit form via JavaScript
                    driver.execute_script("arguments[0].submit();", form)
                    logger.info("Form submitted via JavaScript")
                    form_submitted = True
                    
                except Exception as e:
                    logger.warning(f"Failed to submit form programmatically: {str(e)}")
            
            # Wait for the form submission to complete
            self._random_delay(3.5, 5.5)
            
            # Strategy 3: If browser download started, wait for it to complete
            if form_submitted:
                logger.info("Waiting for download to complete...")
                download_success = self._wait_for_download_complete(output_path, worker_dl_dir, timeout=180)
                
                if download_success:
                    logger.info(f"Download completed successfully: {output_path}")
                    return True
            
            # Strategy 4: Try to find direct download link on the current page
            try:
                # Look for download links on the current page
                download_link = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[download], a.button.is-success"))
                )
                direct_url = download_link.get_attribute("href")
                
                if direct_url:
                    logger.info(f"Found direct download link: {direct_url}")
                    return self._download_file(direct_url, output_path)
            except Exception as e:
                logger.warning(f"No direct download link found: {str(e)}")
            
            # Strategy 5: Last resort - use requests to submit the form directly
            try:
                # Get current URL and page source
                current_url = driver.current_url
                html = driver.page_source
                soup = BeautifulSoup(html, 'html.parser')
                
                # Try to find form and token in the HTML
                form = soup.select_one("form")
                if form:
                    form_action = form.get('action', '')
                    csrf_token = None
                    
                    # Look for token input
                    token_input = form.select_one("input[name='_token']")
                    if token_input:
                        csrf_token = token_input.get('value')
                    
                    if csrf_token and form_action:
                        # Create a session with the same cookies as selenium
                        cookies = {cookie['name']: cookie['value'] for cookie in driver.get_cookies()}
                        headers = {
                            'User-Agent': driver.execute_script("return navigator.userAgent"),
                            'Referer': current_url,
                            'Origin': '.'.join(urlparse(current_url).netloc.split('.')[-2:])
                        }
                        
                        # Apply rate limiting
                        self.throttler.wait_for_token()
                        
                        # Submit the form with POST data
                        form_data = {'_token': csrf_token}
                        full_form_url = urljoin(current_url, form_action)
                        logger.info(f"Submitting form via requests to: {full_form_url}")
                        
                        response = self.sess.post(
                            full_form_url, 
                            data=form_data, 
                            headers=headers, 
                            cookies=cookies, 
                            allow_redirects=True,
                            stream=True
                        )
                        
                        # Check response type and save the file
                        content_type = response.headers.get('Content-Type', '')
                        content_disp = response.headers.get('Content-Disposition', '')
                        
                        if ('video' in content_type or 'octet-stream' in content_type or 'filename=' in content_disp):
                            logger.info(f"Received file response with content type: {content_type}")
                            # Save the file content
                            with open(output_path, 'wb') as f:
                                for chunk in response.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            logger.info(f"File saved to: {output_path}")
                            return True
            except Exception as e:
                logger.error(f"Form submission fallback failed: {str(e)}")
                
            logger.error("All download strategies failed")
            return False
            
        except Exception as e:
            logger.error(f"Error in Kwik link processing: {str(e)}")
            return False

    def _wait_for_download_complete(self, output_path, dl_dir, timeout=180):
        """Wait for download to complete and move to destination"""
        start_time = time.time()
        downloaded_file = None
        
        while time.time() - start_time < timeout:
            try:
                # Check for any newly created files in the download directory
                files = [f for f in os.listdir(dl_dir) if not f.endswith('.crdownload')]
                
                # If we found a file and it's not empty
                if files:
                    for file in files:
                        file_path = os.path.join(dl_dir, file)
                        
                        # Check if it's a valid media file and not empty
                        if os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                            # Check file extension
                            if file.endswith(('.mp4', '.mkv', '.avi', '.mov', '.ts')):
                                downloaded_file = file_path
                                break
                
                if downloaded_file:
                    # Move to destination
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    os.rename(downloaded_file, output_path)
                    logger.info(f"Download completed and moved to: {output_path}")
                    return True
                    
                # Wait briefly before checking again
                time.sleep(2)
                
            except Exception as e:
                logger.warning(f"Error while checking download status: {str(e)}")
                time.sleep(2)
        
        logger.warning(f"Download timed out after {timeout} seconds")
        return False

    def _download_file(self, url, output_path):
        """Download a file from direct URL"""
        logger.info(f"Downloading file from direct URL: {url}")
        
        try:
            # Apply rate limiting
            self.throttler.wait_for_token()
            
            # Add referer and other headers for better acceptance
            headers = {
                'User-Agent': random.choice(USER_AGENTS),
                'Referer': url.split('/')[0] + '//' + url.split('/')[2],
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Range': 'bytes=0-'  # Support for resumed downloads
            }
            
            # Make request with streaming
            response = self.sess.get(url, headers=headers, stream=True, timeout=(15, 300))
            
            # Get content length for progress bar
            total_size = int(response.headers.get('content-length', 0))
            
            # Create directory if doesn't exist
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            
            # Create temporary file to handle interrupted downloads
            temp_path = output_path + '.part'
            
            # Download with progress bar
            with open(temp_path, 'wb') as f, tqdm(
                    total=total_size,
                    unit='B',
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=os.path.basename(output_path)
                ) as pbar:
                
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
            
            # Rename the temp file to the final file
            os.rename(temp_path, output_path)
            
            logger.info(f"Direct download complete: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Direct download failed: {str(e)}")
            
            # Attempt to remove partial file
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
                
            return False

    def _download_episode(self, episode_url, output_path, quality=1080):
        """Download a single episode using worker browser"""
        logger.info(f"Processing episode: {episode_url}")
        
        # Get browser from pool
        browser, dl_dir = self.browser_pool.get_browser()
        
        try:
            # Extract download links
            download_link = self._extract_download_links(episode_url, browser, quality)
            
            if not download_link:
                logger.error("Failed to extract download link")
                self.browser_pool.return_browser(browser, dl_dir)
                return False
                
            # Get kwik link
            kwik_link = self._get_pahe_kwik_link(download_link, browser)
            
            if not kwik_link:
                logger.error("Failed to get kwik link")
                self.browser_pool.return_browser(browser, dl_dir)
                return False
            
            # Process kwik download
            result = self._handle_kwik_form_submission(kwik_link, output_path, browser, dl_dir)
            
            # Return browser to pool
            self.browser_pool.return_browser(browser, dl_dir)
            
            return result
            
        except Exception as e:
            logger.error(f"Error downloading episode: {str(e)}")
            # Return browser to pool even on failure
            self.browser_pool.return_browser(browser, dl_dir)
            return False

    def download_episodes(self, episodes, output_dir, quality=1080, start_ep=1, end_ep=9999, workers=None):
        """Download multiple episodes in parallel with efficient worker management"""
        os.makedirs(output_dir, exist_ok=True)
        
        # Filter episodes within range
        filtered_episodes = {ep_num: url for ep_num, url in episodes.items() if start_ep <= ep_num <= end_ep}
        
        if not filtered_episodes:
            logger.warning(f"No episodes found between {start_ep} and {end_ep}")
            return False
        
        logger.info(f"Downloading {len(filtered_episodes)} episodes with quality {quality}p")
        
        # Set up workers
        workers = workers or self.max_workers
        workers = min(workers, len(filtered_episodes))  # Don't use more workers than episodes
        
        # Create list of tasks
        tasks = []
        for ep_num, url in filtered_episodes.items():
            # Create filename with padding for proper sorting
            filename = f"Episode_{ep_num:03d}.mp4"
            output_path = os.path.join(output_dir, filename)
            
            # Skip if file already exists and is not empty
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                logger.info(f"Episode {ep_num} already exists, skipping")
                continue
                
            tasks.append((url, output_path, ep_num))
        
        if not tasks:
            logger.info("All episodes already downloaded")
            return True
            
        # Sort by episode number
        tasks.sort(key=lambda x: x[2])
        
        logger.info(f"Downloading {len(tasks)} episodes with {workers} workers")
        
        # Create progress bar
        with tqdm(total=len(tasks), desc="Downloading Episodes") as pbar:
            # Process tasks with thread pool
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                # Create future to episode mapping for progress tracking
                future_to_ep = {}
                
                # Submit initial batch of tasks
                for url, output_path, ep_num in tasks:
                    future = executor.submit(self._download_episode, url, output_path, quality)
                    future_to_ep[future] = ep_num
                
                # Process completed tasks
                for future in concurrent.futures.as_completed(future_to_ep):
                    ep_num = future_to_ep[future]
                    try:
                        success = future.result()
                        if success:
                            logger.info(f"Successfully downloaded episode {ep_num}")
                        else:
                            logger.error(f"Failed to download episode {ep_num}")
                    except Exception as e:
                        logger.error(f"Exception while downloading episode {ep_num}: {str(e)}")
                    
                    # Update progress
                    pbar.update(1)
        
        logger.info("Download process completed")
        return True

    def download(self, anime_info, ep_range, quality):
        """Main download controller"""
        title, session_id = anime_info
        logger.info(f"Starting download for: {title}")
        
        eps = self.fetch_episodes(session_id, ep_range[0], ep_range[1])
        if not eps:
            logger.error("No episodes found")
            return
            
        sanitized = re.sub(r'[\\/*?:"<>|]', '', title)
        dl_dir = os.path.join(self.dl_dir, sanitized)
        os.makedirs(dl_dir, exist_ok=True)
        logger.info(f"Output directory: {dl_dir}")
        
        success = 0
        for num, url in sorted(eps.items()):
            fname = f"{sanitized} - Episode {num}.mp4"
            path = os.path.join(dl_dir, fname)
            
            if os.path.exists(path):
                logger.info(f"Skipping existing episode {num}")
                success += 1
                continue
            
            logger.info(f"Processing episode {num}/{len(eps)}")
            if self.download_episode(url, path, quality):
                success += 1
                self._random_delay()
                
        logger.info(f"Completed: {success}/{len(eps)} episodes downloaded")

    def __del__(self):
        """Clean up resources"""
        logger.info("Cleaning up resources")
        
        # Close primary browser
        try:
            if self.driver:
                self.driver.quit()
        except Exception as e:
            logger.warning(f"Error closing primary browser: {str(e)}")
        
        # Close browser pool
        try:
            self.browser_pool.close_all()
        except Exception as e:
            logger.warning(f"Error closing browser pool: {str(e)}")

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="AnimePahe Downloader")
    parser.add_argument("-n", "--name", help="Name of the anime to download")
    parser.add_argument("-o", "--output", help="Output directory", default="downloads")
    parser.add_argument("-s", "--start", type=int, help="Starting episode", default=1)
    parser.add_argument("-e", "--end", type=int, help="Ending episode", default=9999)
    parser.add_argument("-q", "--quality", type=int, help="Preferred quality (e.g., 1080, 720)", default=1080)
    parser.add_argument("-w", "--workers", type=int, help="Number of concurrent downloads", default=3)
    parser.add_argument("-r", "--rate", type=int, help="Maximum requests per minute", default=20)
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Set debug level if requested
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    try:
        # Initialize downloader
        dl = AnimeDownloader(
            dl_dir=args.output,
            max_workers=args.workers,
            requests_per_minute=args.rate
        )
        # Search for anime
        results = dl.search(args.name)
        if not results:
            logger.error("No results found")
            return 
        
        # Select first result
        title, session_id = next(iter(results.items()))
        logger.info(f"Selected title: {title}")
        
        # Download anime
        dl.download(
            (title, session_id),
            (args.start, args.end),
            args.quality,
            workers=args.workers
        )
        
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
    except Exception as e:
        logger.error(f"Unhandled exception: {str(e)}")
    finally:
        # Cleanup
        if 'downloader' in locals():
            dl.close()
        if dl.driver is not None:
            dl.driver.quit()
            logger.info("Browser resources released")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Download interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")