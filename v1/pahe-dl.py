#!/usr/bin/env python3
"""
AnimePahe Downloader - Streamlined Implementation with Form Capture
"""
import os
import re
import ssl
import time
import random
import logging
import argparse
from urllib.parse import urljoin, quote, urlparse
from tqdm import tqdm
import cloudscraper
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("anime_dl.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15',
]

class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_ciphers('HIGH:!DH:!aNULL')
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)

class AnimeDownloader:
    def __init__(self, dl_dir="downloads"):
        self.base_url = "https://animepahe.ru"
        self.dl_dir = dl_dir
        # self.snapshot_dir = os.path.join(self.dl_dir, 'snapshots')
        self.driver = None
        self._init_session()
        self._init_browser()
        logger.info("Initialized")
        
        # Create snapshot directory
        # os.makedirs(self.snapshot_dir, exist_ok=True)

    def _init_session(self):
        """Configure cloudscraper session with proper initialization"""
        self.sess = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True},
            delay=10,
            interpreter='js2py'
        )
        
        self.sess.headers.update({
            'User-Agent': random.choice(USER_AGENTS),
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': self.base_url,
        })
        
        adapter = TLSAdapter()
        self.sess.mount('https://', adapter)

    def _init_browser(self):
        """Initialize undetected Chrome driver with customized preferences"""
        options = uc.ChromeOptions()
        
        # Important: Configure Chrome to save downloads and not ask for save location
        prefs = {
            "download.default_directory": os.path.abspath(self.dl_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": False,
            "plugins.always_open_pdf_externally": True
        }
        options.add_experimental_option("prefs", prefs)
        
        options.add_argument("--headless=new")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        
        # Set up ChromeDriver Monitoring
        self.driver = uc.Chrome(
            options=options,
            enable_cdp_events=True
        )
        self.driver.set_window_size(1920, 1080)
        
        # Set up download behavior
        self.driver.execute_cdp_cmd('Page.setDownloadBehavior', {
            'behavior': 'allow',
            'downloadPath': os.path.abspath(self.dl_dir)
        })

    # def _snapshot(self, label):
    #     """Capture a screenshot of the current browser page"""
    #     safe_label = re.sub(r'[^a-zA-Z0-9_-]', '_', label)
    #     path = os.path.join(self.snapshot_dir, f"{safe_label}.png")
    #     try:
    #         self.driver.save_screenshot(path)
    #         logger.info(f"Snapshot saved: {path}")
    #     except Exception as e:
    #         logger.warning(f"Failed to save snapshot {label}: {e}")

    def _random_delay(self, min_seconds=1.0, max_seconds=4.0):
        """Human-like random delay"""
        alpha = 2
        beta = (max_seconds - min_seconds) / alpha
        delay = min_seconds + random.gammavariate(alpha, beta)
        delay = min(delay, max_seconds)
        delay += random.random() * 0.001
        time.sleep(delay)
        return delay

    def _req(self, url, retry=2):
        """Smart request handler with retry logic"""
        for attempt in range(retry):
            try:
                resp = self.sess.get(url)
                if "DDoS-Guard" in resp.text:
                    # logger.warning("DDoS protection triggered, retrying with browser")
                    self.driver.get(url)
                    WebDriverWait(self.driver, 30).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                    cookies = self.driver.get_cookies()
                    self.sess.cookies.clear()
                    for c in cookies:
                        self.sess.cookies.set(c['name'], c['value'], domain=c['domain'])
                    resp = self.sess.get(url)
                return resp
            except Exception as e:
                logger.warning(f"Request failed (attempt {attempt+1}): {str(e)}")
                time.sleep(2)
        return None

    def search(self, query):
        """Search for anime titles"""
        logger.info(f"Searching for: {query}")
        search_url = f"{self.base_url}/api?m=search&q={quote(query)}"
        
        resp = self._req(search_url)
        
        if resp and resp.status_code == 200:
            data = resp.json().get('data', [])
            logger.info(f"Found {len(data)} results")
            return {item['title']: item['session'] for item in data}
        return {}

    def fetch_episodes(self, session_id, start, end):
        """Get episode list for anime session"""
        logger.info(f"Fetching episodes {start}-{end}")
        eps = {}
        page = 1
        
        while True:
            api_url = f"{self.base_url}/api?m=release&id={session_id}&sort=episode_asc&page={page}"
            resp = self._req(api_url)
            if not resp or resp.status_code != 200:
                break
                
            data = resp.json()
            for ep in data.get('data', []):
                try:
                    num = int(ep['episode'])
                    if start <= num <= end:
                        eps[num] = f"{self.base_url}/play/{session_id}/{ep['session']}"
                except ValueError:
                    continue
                    
            if page >= data.get('last_page', 1):
                break
            page += 1
            self._random_delay()
            
        logger.info(f"Found {len(eps)} episodes")
        return eps

    def _extract_download_links(self, episode_url, quality_pref=1080):
        """Extract download links from episode page"""
        # logger.info(f"Extracting download links from: {episode_url}")
        
        try:
            # First try with regular session
            resp = self._req(episode_url)
            if not resp or resp.status_code != 200:
                # If that fails, try with browser
                # logger.info("Using browser to extract download links")
                self.driver.get(episode_url)
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.ID, "pickDownload"))
                )
                html = self.driver.page_source
                soup = BeautifulSoup(html, 'html.parser')
            else:
                soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Look for download dropdown
            download_menu = soup.select_one("#pickDownload")
            if not download_menu:
                logger.warning("Download menu not found on page")
                return None
                
            # Get all download links
            download_links = {}
            for link in download_menu.select("a.dropdown-item"):
                text = link.text.strip()
                href = link.get('href')
                
                # Parse resolution from link text (e.g., "SubsPlease · 1080p (131MB)")
                resolution_match = re.search(r'(\d+)p', text)
                if resolution_match and href:
                    resolution = int(resolution_match.group(1))
                    download_links[resolution] = href
            
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

    def _get_pahe_kwik_link(self, pahe_url):
        """Navigate pahe.win gateway to get kwik link"""
        # logger.info(f"Navigating to pahe.win gateway: {pahe_url}")
        
        try:
            self.driver.get(pahe_url)
            time.sleep(6)  # Wait for redirect or page load
            # self._snapshot('pahe_gateway')
            
            # If we're redirected to kwik directly
            current_url = self.driver.current_url
            if "kwik.cx" in current_url or "kwik.si" in current_url:
                # logger.info(f"Redirected to kwik: {current_url}")
                return current_url
                
            # Otherwise look for kwik link on the page
            try:
                # Wait for any link with kwik in it
                kwik_link = WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='kwik']"))
                )
                href = kwik_link.get_attribute("href")
                logger.info(f"Found kwik link: {href}")
                return href
            except Exception as e:
                logger.warning(f"No kwik link found on page: {str(e)}")
                return None
                
        except Exception as e:
            logger.error(f"Error navigating pahe gateway: {str(e)}")
            return None

    def _handle_kwik_form_submission(self, url, output_path):
        """Handle Kwik page form submission and capture the download"""
        # logger.info(f"Processing Kwik link with form submission approach: {url}")
        
        try:
            # Navigate to the kwik page
            self.driver.get(url)
            self._random_delay(min_seconds=2.0, max_seconds=3.5)
            # self._snapshot('kwik_initial')
            
            # Setup monitoring for downloads
            self._setup_download_monitoring(output_path)
            
            # Scroll down slightly to see the button (human-like behavior)
            self.driver.execute_script("window.scrollBy(0, window.innerHeight * 0.4);")
            self._random_delay(min_seconds=0.8, max_seconds=1.5)
            
            # Wait for the download form to appear
            download_button = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "form button.button.is-success"))
            )
            # self._snapshot('kwik_before_click')
            
            # Get the form details before clicking
            form = self.driver.find_element(By.CSS_SELECTOR, "form[action*='/d/']")
            form_action = form.get_attribute('action')
            csrf_token = self.driver.find_element(By.CSS_SELECTOR, "input[name='_token']").get_attribute('value')
            
            # logger.info(f"Found form action: {form_action} with token: {csrf_token[:10]}...")
            
            # Click the button programmatically
            download_button.click()
            # logger.info("Download button clicked")
            # self._snapshot('kwik_after_click')
            
            # Wait for the form submission to complete
            self._random_delay(min_seconds=3.5, max_seconds=5.5)
            
            # Check if we got redirected to direct file or download page
            current_url = self.driver.current_url
            # logger.info(f"Redirected to: {current_url}")
            
            # If there's a download link on the page, capture it
            try:
                download_link = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[download], a.button.is-success"))
                )
                direct_url = download_link.get_attribute("href")
                # logger.info(f"Found direct download link on page: {direct_url}")
                
                # Download using selenium session
                if direct_url:
                    return self._download_file(direct_url, output_path)
            except:
                pass
            
            # Plan B: Use requests to submit the form directly
            try:
                # Create a session with the same cookies as selenium
                cookies = {cookie['name']: cookie['value'] for cookie in self.driver.get_cookies()}
                headers = {
                    'User-Agent': self.driver.execute_script("return navigator.userAgent"),
                    'Referer': url,
                    'Origin': '.'.join(urlparse(url).netloc.split('.')[-2:])
                }
                
                # Submit the form with POST data
                form_data = {'_token': csrf_token}
                full_form_url = urljoin(url, form_action)
                # logger.info(f"Submitting form via requests to: {full_form_url}")
                
                response = self.sess.post(
                    full_form_url, 
                    data=form_data, 
                    headers=headers, 
                    cookies=cookies, 
                    allow_redirects=True,
                    stream=True  # Important for capturing the download stream
                )
                
                # Check response type and save the file
                content_type = response.headers.get('Content-Type', '')
                content_disp = response.headers.get('Content-Disposition', '')
                
                if ('video' in content_type or 'octet-stream' in content_type or 
                    'attachment' in content_disp or 'filename' in content_disp):
                    # logger.info(f"Got downloadable content: {content_type}")
                    
                    # Save the response content to the output file
                    total = int(response.headers.get('content-length', 0))
                    
                    # Create parent directories if they don't exist
                    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                    
                    with open(output_path, 'wb') as f, tqdm(
                        desc=os.path.basename(output_path),
                        total=total,
                        unit='iB',
                        unit_scale=True,
                        unit_divisor=1024,
                    ) as bar:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                bar.update(len(chunk))
                    
                    logger.info(f"Download saved to: {output_path}")
                    return True
                else:
                    # Try to find download link in the response
                    soup = BeautifulSoup(response.text, 'html.parser')
                    download_link = soup.select_one("a[download], a.button.is-success")
                    
                    if download_link and download_link.has_attr('href'):
                        direct_url = urljoin(response.url, download_link['href'])
                        # logger.info(f"Found direct download link in response: {direct_url}")
                        return self._download_file(direct_url, output_path)
            except Exception as e:
                logger.warning(f"Form submission via requests failed: {e}")
            
            # Wait for Chrome's download manager to complete downloading
            logger.info("Waiting for Chrome's download manager to complete...")
            self._wait_for_download_complete(output_path, timeout=120)
            
            return os.path.exists(output_path)
            
        except Exception as e:
            logger.error(f"Kwik form submission failed: {str(e)}")
            return False

    def _setup_download_monitoring(self, output_path):
        """Set up monitoring of Chrome downloads"""
        output_filename = os.path.basename(output_path)
        output_dir = os.path.dirname(os.path.abspath(output_path))
        
        # Make sure the directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Setup DevTools Protocol listener for download events
        self.driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": output_dir
        })
        
        logger.info(f"Set up download path to: {output_dir}")

    def _wait_for_download_complete(self, output_path, timeout=120):
        """Wait for Chrome's download to complete and move to correct location"""
        start_time = time.time()
        download_dir = os.path.dirname(os.path.abspath(output_path))
        target_filename = os.path.basename(output_path)
        
        # Look for both crdownload files and completed downloads
        while time.time() - start_time < timeout:
            files = os.listdir(download_dir)
            crdownloads = [f for f in files if f.endswith('.crdownload')]
            
            # Check if download is in progress
            if crdownloads:
                logger.info(f"Download in progress: {crdownloads}")
                time.sleep(2)
                continue
                
            # Check if we have any video files that might be our download
            video_files = [f for f in files if f.endswith(('.mp4', '.mkv'))]
            if video_files:
                # Use the most recently modified file that's not our target
                recent_files = [(f, os.path.getmtime(os.path.join(download_dir, f))) 
                               for f in video_files if f != target_filename]
                
                if recent_files:
                    recent_files.sort(key=lambda x: x[1], reverse=True)
                    newest_file = recent_files[0][0]
                    
                    # Rename to our target filename if needed
                    if newest_file != target_filename:
                        src_path = os.path.join(download_dir, newest_file)
                        
                        try:
                            # Check if target exists and remove it if it does
                            if os.path.exists(output_path):
                                os.remove(output_path)
                                
                            # Move file to target location
                            os.rename(src_path, output_path)
                            logger.info(f"Moved download from {newest_file} to {target_filename}")
                        except Exception as e:
                            logger.error(f"Failed to move file: {str(e)}")
                    
                    return True
            
            # If no download activity is detected, wait a bit
            time.sleep(2)
        
        logger.warning(f"Download timeout after {timeout} seconds")
        return False

    def _download_file(self, url, path):
        """Download file with progress tracking"""
        logger.info(f"Starting download: {os.path.basename(path)}")
        max_retries = 3
        current_try = 0
        
        while current_try < max_retries:
            try:
                current_try += 1
                
                # Set up headers to look like a browser
                headers = {
                    'User-Agent': self.driver.execute_script("return navigator.userAgent"),
                    'Referer': url,
                    'Accept': 'video/webm,video/mp4,video/*,*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Range': 'bytes=0-',  # Support resume
                }
                
                with self.sess.get(url, stream=True, headers=headers) as r:
                    r.raise_for_status()
                    total = int(r.headers.get('content-length', 0))
                    
                    # Create parent directories if they don't exist
                    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                    
                    with open(path, 'wb') as f, tqdm(
                        desc=os.path.basename(path),
                        total=total,
                        unit='iB',
                        unit_scale=True,
                        unit_divisor=1024,
                    ) as bar:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                bar.update(len(chunk))
                    
                    logger.info(f"Download complete: {path}")
                    return True
            except Exception as e:
                logger.error(f"Download attempt {current_try} failed: {str(e)}")
                if current_try < max_retries:
                    wait_time = 2 ** current_try  # Exponential backoff
                    logger.info(f"Waiting {wait_time}s before retrying...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"All download attempts failed for {url}")
                    if os.path.exists(path):
                        os.remove(path)
                    return False
        return False

    def download_episode(self, episode_url, output_path, quality_pref=1080):
        """Process and download a single episode using form submission method"""
        logger.info(f"Processing episode: {episode_url}")
        
        # Step 1: Extract pahe.win download link from episode page
        pahe_link = self._extract_download_links(episode_url, quality_pref)
        if not pahe_link:
            logger.error("Failed to extract download link from episode page")
            return False
        
        # logger.info(f"Found pahe.win link: {pahe_link}")
        
        # Step 2: Get kwik link from pahe.win
        kwik_link = self._get_pahe_kwik_link(pahe_link)
        if not kwik_link:
            logger.error("Failed to get kwik link from pahe.win")
            return False
            
        # logger.info(f"Got kwik link: {kwik_link}")
        
        # Step 3: Use the form submission method directly
        return self._handle_kwik_form_submission(kwik_link, output_path)

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
        """Clean up resources when the object is destroyed"""
        if self.driver is not None:
            try:
                self.driver.quit()
                logger.info("Browser driver closed properly")
            except Exception as e:
                logger.error(f"Error closing browser: {str(e)}")

def main():
    parser = argparse.ArgumentParser(description="Anime Downloader")
    parser.add_argument("-n", "--name", required=True, help="Anime title")
    parser.add_argument("-s", "--start", type=int, default=1, help="Start episode")
    parser.add_argument("-e", "--end", type=int, required=False, help="End episode")
    parser.add_argument("-q", "--quality", type=int, default=1080, help="Preferred quality (e.g., 1080, 720, 360)")
    parser.add_argument("-d", "--dir", default="downloads", help="Output directory")
    args = parser.parse_args()

    logger.info("=== Starting Anime Downloader ===")
    dl = AnimeDownloader(args.dir)
    
    try:
        # Search for anime
        results = dl.search(args.name)
        if not results:
            logger.error("No results found")
            return
            
        # Select first result
        title, session_id = next(iter(results.items()))
        logger.info(f"Selected title: {title}")
        
        # Start download
        dl.download((title, session_id), (args.start, args.end), args.quality)
        logger.info("=== Download process completed ===")
    finally:
        # Ensure browser is closed properly
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