"""
Scraper for Broxtowe Borough Council bin collection schedule.
Full automation process: start -> enter postcode/address -> select address -> Next -> scrape results
"""

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import traceback
import json


def load_users(filepath):
    with open(filepath, "r") as f:
        return json.load(f)


def save_user(data, filepath):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)


def find_first_present(driver, wait, by_value_pairs, timeout_each=10):
    """Try a list of (By, value) until one is present. Returns (By, value, element) or (None, None, None)."""
    for by, val in by_value_pairs:
        try:
            el = WebDriverWait(driver, timeout_each).until(EC.presence_of_element_located((by, val)))
            return by, val, el
        except Exception:
            continue
    return None, None, None


def scrape_bin_collection(start_url, postcode=None, address_to_match=None, headless=True, debug=False):
    options = Options()
    if headless:
        # Chrome 109+ uses --headless=new for better headless support; change if needed
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1400,900")
    # optional: avoid detection (not guaranteed)
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, 20)

    exact_address = ''

    try:
        driver.get(start_url)
        if debug:
            print("[info] opened start url:", start_url)

        # --- 1) Try to find a visible address/postcode input ---
        # Try a list of likely input IDs / selectors
        candidates = [
            (By.ID, "ctl00_ContentPlaceHolder1_FF5683TB"),      # observed earlier as visible address box
            (By.ID, "ctl00_ContentPlaceHolder1_txtPostcode"),
            (By.ID, "ctl00_ContentPlaceHolder1_txtSearchAddress"),
            (By.ID, "ctl00_ContentPlaceHolder1_txtSearch"),
            (By.NAME, "ctl00$ContentPlaceHolder1$FF5683TB"),
            (By.CSS_SELECTOR, "input[type='text']"),            # fallback: any text input (last resort)
        ]

        by, val, input_el = find_first_present(driver, wait, candidates, timeout_each=1)
        if not input_el:
            # If nothing found, dump a small snapshot of the page for debugging
            html_snippet = driver.page_source[:4000]
            raise RuntimeError("Could not find any visible text input on start page. Page snapshot:\n" + html_snippet)

        if debug:
            print(f"[info] Found input element by={by} val={val} id={input_el.get_attribute('id')}")

        # --- 2) Enter the postcode or address (prefer postcode if available) ---
        # Clear and send the postcode or address
        search_value = postcode if postcode else address_to_match
        if not search_value:
            raise ValueError("Provide at least postcode or address_to_match")

        input_el.clear()
        input_el.send_keys(search_value)
        time.sleep(0.8)   # small pause so any JS/autocomplete can react
        # Try pressing Enter to trigger lookup/suggestions
        input_el.send_keys("\n")

        if debug:
            print("[info] Sent search value:", search_value)

        # --- 3) Wait for either: address dropdown OR the Next/Submit button to be clickable OR direct result ---
        # Look for common address dropdown/select control
        dropdown_candidates = [
            (By.ID, "ctl00_ContentPlaceHolder1_ddlAddress"),
            (By.CSS_SELECTOR, "select"),  # fallback: any select on the page
        ]

        # Also the Next/Submit button id known from HTML
        next_btn_selector = (By.ID, "ctl00_ContentPlaceHolder1_btnSubmit")

        # Wait up to N seconds for something meaningful to appear
        # First, wait shortly for a select dropdown to appear
        select_by, select_val, select_el = find_first_present(driver, wait, dropdown_candidates, timeout_each=1)
        if select_el:
            if debug:
                print("[info] Address dropdown found:", select_val)
            # Use Select API if it's an actual <select>
            try:
                sel = Select(select_el)
                # If address_to_match provided: try to find option containing that text
                option_index = None
                if address_to_match:
                    for i, opt in enumerate(sel.options):
                        if address_to_match.lower() in opt.text.lower():
                            option_index = i
                            break
                # If not found, pick first non-empty option (skip index 0 if it's placeholder)
                if option_index is None:
                    # choose first option that looks like an address
                    for i, opt in enumerate(sel.options):
                        txt = opt.text.strip()
                        if txt and "select" not in txt.lower():
                            option_index = i
                            break
                if option_index is None:
                    raise RuntimeError("No usable address options in dropdown")
                sel.select_by_index(option_index)
                if debug:
                    print(f"[info] selected address option index {option_index}: {sel.options[option_index].text.strip()}")
                exact_address = sel.options[option_index].text.strip()
                time.sleep(0.6)
                # Now click Next/Continue button (may be a different button; try known IDs)
                try:
                    next_btn = driver.find_element(*next_btn_selector)
                    driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
                    time.sleep(0.2)
                    next_btn.click()
                except Exception:
                    # fallback: try a button with text "Next" or "Continue"
                    btns = driver.find_elements(By.TAG_NAME, "button")
                    clicked = False
                    for b in btns:
                        try:
                            txt = b.text.strip().lower()
                            if "next" in txt or "continue" in txt or "find" in txt:
                                b.click()
                                clicked = True
                                break
                        except Exception:
                            continue
                    if not clicked:
                        raise RuntimeError("Could not click Next/Continue after selecting address")
            except Exception as e:
                raise

        else:
            # No select dropdown found
            if debug:
                print("[info] No address dropdown found")
            
            result = {
                "address": 'not found',
                "bins": []
            }

            return result, exact_address

            # Maybe the page performs postback and returns results or shows Next button
            # Wait for Next button to be clickable
            # try:
            #     next_btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable(next_btn_selector))
            #     # use script click to avoid problems
            #     driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
            #     time.sleep(0.2)
            #     next_btn.click()
            #     if debug:
            #         print("[info] Clicked Next button")
            # except Exception:
            #     # Maybe pressing Enter already loaded results; continue to wait for results
            #     if debug:
            #         print("[info] Could not find clickable Next button; proceeding to wait for results")

        # --- 4) Wait for the results table to appear ---
        # The results table container id from page is: ctl00_ContentPlaceHolder1_FF5686FormGroup
        results_div = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder1_FF5686FormGroup"))
        )
        if debug:
            print("[info] Results div found")

        # --- 5) Parse the results (address block + table) ---
        # Address block
        try:
            address_div = driver.find_element(By.CLASS_NAME, "ss_AddressDetails")
            raw_addr_html = address_div.get_attribute("innerHTML")
            address = BeautifulSoup(raw_addr_html, "html.parser").get_text(separator=", ").strip()
        except Exception:
            address = None

        # Table parsing
        table_html = results_div.get_attribute("innerHTML")
        soup = BeautifulSoup(table_html, "html.parser")
        table = soup.find("table")
        parsed = []
        if table:
            rows = table.find_all("tr")
            # first row expected to be header
            headers = [td.get_text(strip=True) for td in rows[0].find_all(["td","th"])]
            for r in rows[1:]:
                cols = [td.get_text(strip=True) for td in r.find_all("td")]
                if not cols:
                    continue
                # if header count doesn't match, still pair by index
                rowdict = {}
                for i, c in enumerate(cols):
                    key = headers[i] if i < len(headers) else f"col{i}"
                    rowdict[key] = c
                parsed.append(rowdict)

        result = {
            "address": address,
            "bins": parsed
        }

        return result, exact_address

    except Exception as e:
        print("[error] Exception during scrape:", str(e))
        traceback.print_exc()
        raise
    finally:
        try:
            driver.quit()
        except Exception:
            pass
