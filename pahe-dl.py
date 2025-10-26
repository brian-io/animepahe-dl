#!/usr/bin/env python3
"""
AnimePahe Downloader - Clean, efficient implementation for downloading anime episodes
"""
import glob
import os
import re
import ssl
import sys
import time
import json
import random
import logging
import argparse
from urllib.parse import urljoin, quote, urlparse
from tqdm import tqdm
import cloudscraper
from bs4 import BeautifulSoup
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] {%(filename)s:%(lineno)d} %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("anime_dl.log", mode='a'), 
        logging.StreamHandler(stream=sys.stderr)
    ]
)
logger = logging.getLogger(__name__)

# Create logs directory if it doesn't exist
os.makedirs("logs", exist_ok=True)

# Browser user agents
USER_AGENTS = [
    'Mozilla/5.0 (Linux; Android 15; SM-S931B Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/127.0.6533.103 Mobile Safari/537.36',
    'Mozilla/5.0 (Linux; Android 14; Pixel 9 Pro Build/AD1A.240418.003; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.54 Mobile Safari/537.36',
    'Mozilla/5.0 (Linux; Android 13; 23129RAA4G Build/TKQ1.221114.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36'
    ]


class TLSAdapter(HTTPAdapter):
    """Custom SSL adapter to handle modern TLS requirements"""
    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_ciphers('HIGH:!DH:!NULL')
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)


class AnimeDownloader:
    """Main downloader class that handles searching, fetching and downloading anime episodes"""
    
    def __init__(self, dl_dir="downloads", skip_browser=False):
        """Initialize the downloader with base configuration"""
        self.base_url = "https://animepahe.ru"
        self.dl_dir = dl_dir
        self.driver = None
        
        # Create download directory
        os.makedirs(self.dl_dir, exist_ok=True)
        
        # Initialize HTTP session and browser
        self._init_session()
        if not skip_browser:
            self._init_browser()

    def _init_session(self):
        """Configure cloudscraper session with proper headers and TLS support"""
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
        
        # Add TLS adapter for modern sites
        adapter = TLSAdapter()
        self.sess.mount('https://', adapter)

    def _init_browser(self):
        """Initialize undetected Chrome driver with download settings"""
        options = uc.ChromeOptions()
        
        # Configure Chrome to save downloads automatically
        prefs = {
            "download.default_directory": os.path.abspath(self.dl_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": False,
            "plugins.always_open_pdf_externally": True
        }
        options.add_experimental_option("prefs", prefs)
        
        # Add anti-detection arguments
        options.add_argument("--headless=new")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        
        # Initialize Chrome
        self.driver = uc.Chrome(
            options=options,
            enable_cdp_events=True,
            use_subprocess=True,
            version_main=None

        )
        self.driver.set_window_size(1920, 1080)
        
        # Set download behavior via CDP
        self.driver.execute_cdp_cmd('Page.setDownloadBehavior', {
            'behavior': 'allow',
            'downloadPath': os.path.abspath(self.dl_dir)
        })

    def _random_delay(self, min_seconds=1.0, max_seconds=4.0):
        """Add human-like random delay to avoid detection"""
        alpha = 2
        beta = (max_seconds - min_seconds) / alpha
        delay = min_seconds + random.gammavariate(alpha, beta)
        delay = min(delay, max_seconds)
        delay += random.random() * 0.001
        time.sleep(delay)
        return delay

    def _req(self, url, retry=2):
        """Send HTTP request with retry logic and anti-DDoS measures"""
        for attempt in range(retry):
            try:
                resp = self.sess.get(url)
                # Check if DDoS protection is triggered
                if "DDoS-Guard" in resp.text:
                    # Use browser to bypass protection
                    self.driver.get(url)
                    WebDriverWait(self.driver, 30).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                    # Transfer cookies from browser to session
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
        """Search for anime titles and return results"""
        logger.info(f"Searching for: {query}")
        search_url = f"{self.base_url}/api?m=search&q={quote(query)}"
        
        resp = self._req(search_url)
        
        if resp and resp.status_code == 200:
            try:
                data = resp.json().get('data', [])
                logger.info(f"Found {len(data)} results")
                return {item['title']: item['session'] for item in data}
            except Exception as e:
                logger.error(f"Failed to parse search response: {e}")
                return {}
        else:
            logger.warning("Search request failed or returned non-200 status")
            return {}

    def fetch_episodes(self, session_id, start, end=None):
        """Get episode list for anime session within specified range"""
        if end is None:
            end = float('inf')  # Default to all episodes if end not specified
            
        logger.info(f"Fetching episodes {start}-{end if end != float('inf') else 'end'}")
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

    def _extract_download_links(self, episode_url, quality_pref=1080, prefer_dub=False):
        """Extract download links from episode page with quality and audio preference"""
        try:
            # Try with regular session first
            resp = self._req(episode_url)
            if not resp or resp.status_code != 200:
                # Fall back to browser if session request fails
                self.driver.get(episode_url)
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.ID, "pickDownload"))
                )
                html = self.driver.page_source
                soup = BeautifulSoup(html, 'html.parser')
            else:
                soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Find download menu
            download_menu = soup.select_one("#pickDownload")
            if not download_menu:
                logger.warning("Download menu not found on page")
                return None
                
            # Parse all available download options
            download_links = {}
            for link in download_menu.select("a.dropdown-item"):
                text = link.text.strip()
                href = link.get('href')

                # Check if this is a dubbed version
                is_dub = 'badge-warning' in str(link) and ('eng' in str(link) or 'chi' in str(link))
                
                # Parse resolution from link text
                resolution_match = re.search(r'(\d+)p', text)
                if resolution_match and href:
                    resolution = int(resolution_match.group(1))
                    key = (resolution, is_dub)
                    download_links[key] = {
                        'url': href,
                        'text': text,
                        'is_dub': is_dub
                    }
            
            # Organize options by audio type
            available_options = {
                'subbed': [k for k in download_links.keys() if not k[1]],
                'dubbed': [k for k in download_links.keys() if k[1]]
            } 

            # Log available options
            logger.info(f"Available subbed qualities: {[r[0] for r in available_options['subbed']]}")
            logger.info(f"Available dubbed qualities: {[r[0] for r in available_options['dubbed']]}")
             
            # Determine target and fallback audio types
            target_type = 'dubbed' if prefer_dub else 'subbed'
            fallback_type = 'subbed' if prefer_dub else 'dubbed'
            
            # Select the best option based on preferences
            selected_key = None
            
            # Try preferred audio type first
            if available_options[target_type]:
                options = available_options[target_type]
                resolutions = [r[0] for r in options]
                
                if quality_pref in resolutions:
                    selected_key = (quality_pref, prefer_dub)
                else:
                    # Get closest available quality
                    closest_res = min(resolutions, key=lambda x: abs(x - quality_pref))
                    selected_key = (closest_res, prefer_dub)
                    logger.info(f"Selected closest quality: {closest_res}p ({target_type})")
            
            # Fall back to other audio type if preferred isn't available
            elif available_options[fallback_type]:
                logger.info(f"Preferred {target_type} not available, falling back to {fallback_type}")
                options = available_options[fallback_type]
                resolutions = [r[0] for r in options]
                
                if quality_pref in resolutions:
                    selected_key = (quality_pref, not prefer_dub)
                else:
                    # Get closest available quality
                    closest_res = min(resolutions, key=lambda x: abs(x - quality_pref))
                    selected_key = (closest_res, not prefer_dub)
                    logger.info(f"Selected closest quality: {closest_res}p ({fallback_type})")
            else:
                logger.warning("No download options found")
                return None
                
            selected_link = download_links[selected_key]
            logger.info(f"Selected download option: {selected_link['text']}")
            
            return selected_link['url']
            
        except Exception as e:
            logger.error(f"Error extracting download links: {str(e)}")
            return None

    def _get_kwik_link(self, pahe_url):
        """Navigate pahe gateway to get kwik link"""
        try:
            self.driver.get(pahe_url)
            time.sleep(6)  # Wait for redirect or page load
            
            # Check if we're already redirected to kwik
            current_url = self.driver.current_url
            if "kwik.cx" in current_url or "kwik.si" in current_url:
                return current_url
                
            # Otherwise look for kwik link on the page
            try:
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

    def _handle_kwik_download(self, url, output_path):
        """Handle Kwik page form submission and capture the download"""
        try:
            # Navigate to the kwik page
            self.driver.get(url)
            self._random_delay(min_seconds=2.0, max_seconds=3.5)
            
            # Setup monitoring for downloads
            self._setup_download_monitoring(output_path)
            
            # Scroll down slightly (human-like behavior)
            self.driver.execute_script("window.scrollBy(0, window.innerHeight * 0.4);")
            self._random_delay(min_seconds=0.8, max_seconds=1.5)
            
            # Wait for and get the download form elements
            download_button = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "form button.button.is-success"))
            )
            
            form = self.driver.find_element(By.CSS_SELECTOR, "form[action*='/d/']")
            form_action = form.get_attribute('action')
            csrf_token = self.driver.find_element(By.CSS_SELECTOR, "input[name='_token']").get_attribute('value')
            
            # Click the download button
            download_button.click()
            
            # Wait for the form submission to complete
            self._random_delay(min_seconds=3.5, max_seconds=5.5)
            
            # Try to find direct download link on the page
            try:
                download_link = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[download], a.button.is-success"))
                )
                direct_url = download_link.get_attribute("href")
                
                # Download using direct link if found
                if direct_url:
                    return self._download_file(direct_url, output_path)
            except:
                pass
            
            # Alternative: Submit form directly via requests
            try:
                # Create a session with the same cookies as selenium
                cookies = {cookie['name']: cookie['value'] for cookie in self.driver.get_cookies()}
                headers = {
                    'User-Agent': self.driver.execute_script("return navigator.userAgent"),
                    'Referer': url,
                    'Origin': '.'.join(urlparse(url).netloc.split('.')[-2:])
                }
                
                # Submit the form
                form_data = {'_token': csrf_token}
                full_form_url = urljoin(url, form_action)
                
                response = self.sess.post(
                    full_form_url, 
                    data=form_data, 
                    headers=headers, 
                    cookies=cookies, 
                    allow_redirects=True,
                    stream=True
                )
                
                # Check if response is a file download
                content_type = response.headers.get('Content-Type', '')
                content_disp = response.headers.get('Content-Disposition', '')
                
                if ('video' in content_type or 'octet-stream' in content_type or 
                    'attachment' in content_disp or 'filename' in content_disp):
                    
                    # Save the response to file with progress bar
                    total = int(response.headers.get('content-length', 0))
                    
                    # Create parent directories
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
        output_dir = os.path.dirname(os.path.abspath(output_path))
        
        # Make sure the directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Configure Chrome's download behavior
        self.driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": output_dir
        })
        
        logger.info(f"Download path set to: {output_dir}")

    def _wait_for_download_complete(self, output_path, timeout=120):
        """Wait for Chrome's download to complete and move to correct location"""
        start_time = time.time()
        download_dir = os.path.dirname(os.path.abspath(output_path))
        target_filename = os.path.basename(output_path)
        
        while time.time() - start_time < timeout:
            files = os.listdir(download_dir)
            
            # Check for in-progress downloads
            crdownloads = [f for f in files if f.endswith('.crdownload')]
            if crdownloads:
                logger.info(f"Download in progress: {crdownloads}")
                time.sleep(2)
                continue
                
            # After no partial files - Look for completed video files
            video_files = [f for f in files if f.endswith(('.mp4', '.mkv'))]
            if video_files:
                # Find the most recently modified file
                recent_files = [(f, os.path.getmtime(os.path.join(download_dir, f))) 
                               for f in video_files if f != target_filename]
                
                if recent_files:
                    recent_files.sort(key=lambda x: x[1], reverse=True)
                    newest_file = recent_files[0][0]
                    
                    # Rename to target filename if needed
                    if newest_file != target_filename:
                        src_path = os.path.join(download_dir, newest_file)
                        dst_path = os.path.join(download_dir, target_filename)

                        
                        try:
                            # Remove target if it exists
                            if os.path.exists(dst_path):
                                os.remove(dst_path)
                                
                            # Move file to target location
                            os.rename(src_path, dst_path)
                            logger.info(f"Renamed '{newest_file}' to '{target_filename}'")
                        except Exception as e:
                            logger.error(f"Failed to move file: {str(e)}")
                            return False

                    
                    return True
            
            # Wait before checking again
            time.sleep(2)
        
        logger.warning(f"Download timeout after {timeout} seconds")
        return False

    def _download_file(self, url, path):
        """Download file with progress tracking and retry logic"""
        logger.info(f"Starting download: {os.path.basename(path)}")
        max_retries = 1
        current_try = 0
        
        while current_try < max_retries:
            try:
                current_try += 1
                
                # Set up headers for download
                headers = {
                    'User-Agent': self.driver.execute_script("return navigator.userAgent"),
                    'Referer': url,
                    'Accept': 'video/webm,video/mp4,video/*,*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Range': 'bytes=0-',  # Support resume
                }
                
                # Download with progress bar
                with self.sess.get(url, stream=True, headers=headers) as r:
                    r.raise_for_status()
                    total = int(r.headers.get('content-length', 0))
                    
                    # Create parent directories
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
    
    def _cleanup(self, directory=None):
        """Clean up any partially downloaded files and files starting with 'Anime'"""
        # Use default download directory if none specified
        if directory is None:
            directory = self.dl_dir
            
        try:
            processed_files = set()
            
            # Find and remove all partial downloads (.crdownload files)
            for root, _, _ in os.walk(directory):
                crdownload_files = glob.glob(os.path.join(root, "*.crdownload"))
                
                for file_path in crdownload_files:
                    try:
                        if file_path not in processed_files:
                            # Check if file is still being written
                            size_before = os.path.getsize(file_path)
                            time.sleep(2)
                            size_after = os.path.getsize(file_path)
                            
                            if size_before == size_after:  # File not being written to
                                os.remove(file_path)
                                logger.info(f"Removed partial download: {file_path}")
                                processed_files.add(file_path)
                            else:
                                logger.info(f"Skipping active download: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to remove {file_path}: {str(e)}")
            
            # Find and remove files starting with "Anime"
            for root, _, files in os.walk(directory):
                anime_files = [f for f in files if f.startswith("Anime")]
                
                for filename in anime_files:
                    file_path = os.path.join(root, filename)
                    try:
                        if file_path not in processed_files:
                            # Check if it's a video file that might still be downloading
                            if filename.endswith(('.mp4', '.mkv', '.avi')):
                                # Check if file is still being written
                                size_before = os.path.getsize(file_path)
                                time.sleep(2)
                                size_after = os.path.getsize(file_path)
                                
                                if size_before == size_after:  # File not being written to
                                    os.remove(file_path)
                                    logger.info(f"Removed file starting with 'Anime': {file_path}")
                                    processed_files.add(file_path)
                                else:
                                    logger.info(f"Skipping active file: {file_path}")
                            else:
                                # For non-video files, remove immediately
                                os.remove(file_path)
                                logger.info(f"Removed file starting with 'Anime': {file_path}")
                                processed_files.add(file_path)
                    except Exception as e:
                        logger.error(f"Failed to remove {file_path}: {str(e)}")
                        
            return None
        
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
            return 0

    def download_episode(self, episode_url, output_path, quality_pref=1080, prefer_dub=False):
        """Process and download a single episode"""
        logger.info(f"Processing episode: {episode_url}")
        
        # Step 1: Get download link from episode page
        pahe_link = self._extract_download_links(episode_url, quality_pref, prefer_dub)
        if not pahe_link:
            logger.error("Failed to extract download link from episode page")
            return False
        
        # Step 2: Navigate to kwik from pahe link
        kwik_link = self._get_kwik_link(pahe_link)
        if not kwik_link:
            logger.error("Failed to get kwik link")
            return False
        
        # Step 3: Handle the actual download
        return self._handle_kwik_download(kwik_link, output_path)

    def download(self, anime_info, ep_range, quality, prefer_dub=False):
        """Main download controller"""
        title, session_id = anime_info
        start_ep, end_ep = ep_range
        logger.info(f"Starting download for: {title} ({'dubbed' if prefer_dub else 'subbed'})")
        
        # Fetch episodes in the specified range
        eps = self.fetch_episodes(session_id, start_ep, end_ep)
        if not eps:
            logger.error("No episodes found in the specified range")
            return
            
        # Prepare output directory
        sanitized = re.sub(r'[\\/*?:"<>|]', '', title)
        dl_dir = os.path.join(self.dl_dir, sanitized)
        os.makedirs(dl_dir, exist_ok=True)
        
        # Download each episode
        success = 0
        total_eps = len(eps)
        
        for num, url in sorted(eps.items()):
            fname = f"{sanitized} - Episode {num}.mp4"
            path = os.path.join(dl_dir, fname)
            
            if os.path.exists(path):
                logger.info(f"Skipping existing episode {num}")
                success += 1
                continue
            
            logger.info(f"Processing episode {num} ({success+1}/{total_eps})")
            if self.download_episode(url, path, quality, prefer_dub):
                success += 1
                self._random_delay()
                
        logger.info(f"Completed: {success}/{total_eps} episodes downloaded")
        
        # Clean up any partial downloads
        self._cleanup(dl_dir)
            
    def __del__(self):
        """Clean up resources when the object is destroyed"""
        if hasattr(self, 'driver') and self.driver is not None:
            try:
                self.driver.quit()
                logger.info("Browser driver closed properly")
            except Exception as e:
                logger.error(f"Error closing browser: {str(e)}")


def main():
    """Main entry point with argument parsing"""
    parser = argparse.ArgumentParser(
        description="AnimePahe Downloader - Download anime episodes",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-n", "--name", required=True, help="Anime title to search for")
    parser.add_argument("-s", "--start", type=int, default=1, help="Start episode number")
    parser.add_argument("-e", "--end", type=int, help="End episode number (defaults to all available)")
    parser.add_argument("-q", "--quality", type=int, default=1080, help="Preferred video quality (e.g., 1080, 720)")
    parser.add_argument("-d", "--dir", default="downloads", help="Output directory for downloads")
    parser.add_argument("--dub", action="store_true", help="Prefer dubbed version if available")
    parser.add_argument("--search-only", action="store_true", help="Only perform a search and print results as JSON")

    args = parser.parse_args()

    logger.info("=== Starting Downloader ===")

    if args.search_only:
        # Use a lightweight version of AnimeDownloader without browser
        try:
            dl = AnimeDownloader(args.dir, skip_browser=False)
            results = dl.search(args.name)

            if not results:
                logger.error("No results found!")
                return
            
            print(json.dumps(results))  # <-- JSON output for external use
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            print(json.dumps({}))
        return
    else:
        # Proceed with download mode
        dl = AnimeDownloader(args.dir)
        
        try:
            # Search for anime
            results = dl.search(args.name)
            if not results:
                logger.error("No results found for the given title")
                return
                
            # Select first result
            title, session_id = next(iter(results.items()))
            logger.info(f"Selected title: {title}")
            
            # Start download
            dl.download(
                (title, session_id), 
                (args.start, args.end or float('inf')),  # Handle case when end is not specified
                args.quality, 
                args.dub
            )
            logger.info("=== Download completed successfully ===")

        except KeyboardInterrupt:
            logger.info("Download interrupted by user")
        except Exception as e:
            logger.error(f"Fatal error: {str(e)}")
        finally:
            # Ensure browser is closed properly
            if dl.driver is not None:
                dl.driver.quit()
                logger.info("Browser resources released")


if __name__ == "__main__":
    main()