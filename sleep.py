#!/usr/bin/env python3
import os
import logging
import sys
import json
import time
import shutil
import hashlib
import zipfile
import requests
from datetime import datetime
from urllib.parse import unquote
from bs4 import BeautifulSoup

# ─── Configuration ─────────────────────────────────────────────────────────────
EZSHARE_BASE       = "http://192.168.4.1"
DOWNLOAD_DIR       = "downloads"
ZIP_OUTPUT         = "cpapdata.zip"
WHITELIST          = [".edf", ".crc", ".json", ".tgt", ".log"]
EZSHARE_PROFILE    = "ezshare"
HOME_WIFI_PROFILE  = "homewifi"
UPLOAD_STATE_FILE  = "upload_state.txt"
LOG_FILE           = "uploader.log"

def resolve_url(href: str) -> str:
    """
    Resolve an EzShare listing href to a full URL.
    """
    href = href.strip()
    if href.lower().startswith("http"):
        return href
    return f"{EZSHARE_BASE.rstrip('/')}/{href.lstrip('/')}"

def remote_hash_folder(date_str: str) -> str:
    """
    HEAD each whitelisted file in DATALOG/<date_str> and
    build a SHA-256 over "name:size\n" entries.
    """
    log(f"START remote_hash_folder({date_str})")
    
    # 1) Get the DATALOG link
    r = requests.get(f"{EZSHARE_BASE}/dir", timeout=10)
    r.raise_for_status()
    root_soup = BeautifulSoup(r.text, "html.parser")
    datalog_href = next(
        (a["href"] for a in root_soup.find_all("a") if a.text.strip() == "DATALOG"),
        None
    )
    if not datalog_href:
        raise RuntimeError("Could not find DATALOG link on /dir")

    # 2) Locate the specific date folder
    r2 = requests.get(resolve_url(datalog_href), timeout=10)
    r2.raise_for_status()
    dl_soup = BeautifulSoup(r2.text, "html.parser")
    date_href = next(
        (a["href"] for a in dl_soup.find_all("a") if a.text.strip() == date_str),
        None
    )
    if not date_href:
        raise RuntimeError(f"Could not find folder for date {date_str}")

    # 3) Scrape that folder and HEAD each file
    r3 = requests.get(resolve_url(date_href), timeout=10)
    r3.raise_for_status()
    ds = BeautifulSoup(r3.text, "html.parser")
    sha = hashlib.sha256()
    sess = requests.Session()
    for a in ds.find_all("a"):
        name = a.text.strip()
        href = a.get("href", "")
        if "download?file=" not in href or not any(name.lower().endswith(ext) for ext in WHITELIST):
            continue
        file_url = resolve_url(href)
        log(f"  HEAD {name} → {file_url}")
        head = sess.head(file_url, timeout=5)
        head.raise_for_status()
        size = head.headers.get("Content-Length", "0")
        sha.update(f"{name}:{size}\n".encode("utf-8"))

    h = sha.hexdigest()
    log(f"✅ Remote hash: {h}")
    log("END remote_hash_folder")
    return h

# Create a top‐level logger
logger = logging.getLogger("uploader")
logger.setLevel(logging.INFO)

# Formatter matching your existing timestamp style
formatter = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")

# Console handler (stdout)
ch = logging.StreamHandler(sys.stdout)
ch.setFormatter(formatter)
logger.addHandler(ch)

# File handler (appends to uploader.log)
fh = logging.FileHandler(LOG_FILE, mode="a")
fh.setFormatter(formatter)
logger.addHandler(fh)

# Replace your log() function:
def log(msg):
    logger.info(msg)

def get_token_from_config():
    log("START get_token_from_config")
    try:
        cfg = json.load(open("config.json"))
        data = {
            "grant_type":    "password",
            "client_id":     cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "username":      cfg["username"],
            "password":      cfg["password"],
            "scope":         "read write"
        }
        r = requests.post("https://sleephq.com/oauth/token", data=data, timeout=10)
        r.raise_for_status()
        token = r.json()["access_token"]
        log("✅ Token retrieved")
        with open("sleephq_token.txt", "w") as f:
            f.write(token)
        return token
    except Exception as e:
        log(f"❌ Failed to retrieve token: {e}")
        return None
    finally:
        log("END get_token_from_config")

def fetch_team_id(token):
    log("START fetch_team_id")
    try:
        r = requests.get(
            "https://sleephq.com/api/v1/teams",
            headers={"Authorization": f"Bearer {token}"}, timeout=10
        )
        r.raise_for_status()
        teams = r.json().get("data", [])
        if not teams:
            log("❌ No teams found")
            return None
        t = teams[0]
        log(f"✅ Using team {t['attributes']['name']} (ID {t['id']})")
        return t["id"]
    except Exception as e:
        log(f"❌ Failed to fetch team ID: {e}")
        return None
    finally:
        log("END fetch_team_id")

def switch_wifi(profile):
    log(f"START switch_wifi({profile})")
    res = __import__("subprocess").run(
        ["nmcli", "connection", "up", profile],
        capture_output=True, text=True
    )
    if res.returncode == 0:
        log(f"✅ Switched to WiFi: {profile}")
        time.sleep(5)
        log(f"END switch_wifi({profile})")
        return True
    else:
        log(f"❌ WiFi switch failed: {res.stderr.strip()}")
        return False

def read_last_uploaded_info():
    log("START read_last_uploaded_info")
    if not os.path.exists(UPLOAD_STATE_FILE):
        log("⚠️  No previous state")
        return None, None
    last_date = last_hash = None
    for line in open(UPLOAD_STATE_FILE):
        if line.startswith("date="):
            last_date = line.strip().split("=",1)[1]
        elif line.startswith("hash="):
            last_hash = line.strip().split("=",1)[1]
    log(f"✅ Read state: date={last_date!r}, hash={last_hash!r}")
    log("END read_last_uploaded_info")
    return last_date, last_hash

def save_uploaded_info(date_str, folder_hash):
    log("START save_uploaded_info")
    with open(UPLOAD_STATE_FILE, "w") as f:
        f.write(f"date={date_str}\nhash={folder_hash}\n")
    log(f"✅ Saved state: date={date_str}, hash={folder_hash}")
    log("END save_uploaded_info")

def hash_folder(path):
    log(f"START hash_folder({path})")
    sha = hashlib.sha256()
    for root, _, files in os.walk(path):
        for fname in sorted(files):
            fp = os.path.join(root, fname)
            with open(fp, "rb") as f:
                while chunk := f.read(4096):
                    sha.update(chunk)
    h = sha.hexdigest()
    log(f"✅ Folder hash: {h}")
    log("END hash_folder")
    return h
    
def zip_folder(zip_name):
    log(f"START zip_folder({zip_name})")
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(DOWNLOAD_DIR):
            for fname in files:
                full = os.path.join(root, fname)
                # Compute the archive name relative to DOWNLOAD_DIR
                arc = os.path.relpath(full, DOWNLOAD_DIR)
                # Split off extension and lowercase it
                base, ext = os.path.splitext(arc)
                arc_lower = base + ext.lower()
                log(f"    🗜 Adding {arc} as {arc_lower}")
                zf.write(full, arc_lower)
    log("✅ ZIP created")
    log("END zip_folder")

def create_import(token, team_id):
    log("📨 Creating import session...")
    url = f"https://sleephq.com/api/v1/teams/{team_id}/imports"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.api+json"
    }
    data = {"programatic": False}
    try:
        r = requests.post(url, headers=headers, json=data, timeout=10)
        r.raise_for_status()
        import_id = r.json()["data"]["id"]
        log(f"✅ Import ID: {import_id}")
        return import_id
    except Exception as e:
        log(f"❌ Failed to create import session: {e}")
        return None

def upload_zip(token, import_id, zip_file):
    log("☁️ Uploading ZIP to import session...")
    url = f"https://sleephq.com/api/v1/imports/{import_id}/files"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with open(zip_file, "rb") as f:
            files = {"file": (os.path.basename(zip_file), f)}
            data = {"name": os.path.basename(zip_file), "path": "/", "content_hash": hash_folder(os.path.join(DOWNLOAD_DIR, os.listdir(os.path.join(DOWNLOAD_DIR, 'DATALOG'))[-1]))}
            r = requests.post(url, headers=headers, data=data, files=files, timeout=60)
            r.raise_for_status()
            log("✅ File uploaded.")
    except Exception as e:
        log(f"❌ Upload failed: {e}")

def process_import(token, import_id):
    log("⚙️ Processing import on SleepHQ...")
    url = f"https://sleephq.com/api/v1/imports/{import_id}/process_files"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        r = requests.post(url, headers=headers, timeout=10)
        r.raise_for_status()
        log("✅ Import processing started.")
    except Exception as e:
        log(f"❌ Failed to start import processing: {e}")

def append_upload_log(date_str, folder_hash, status, duration_sec):
    entry = {
        "date": date_str,
        "hash": folder_hash,
        "status": status,
        "duration_sec": duration_sec,
        "timestamp": datetime.utcnow().isoformat()
    }
    try:
        with open("upload_history.json", "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log(f"❌ Failed to log upload history: {e}")

def download_file(href, dest_dir, label):
    url = href if href.startswith("http") else f"{EZSHARE_BASE}/{href}"
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, label)
    log(f"    ⬇️ Downloading: {label}")
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)

def main():
    log("=== START main ===")
    start_time = time.time()
    try:
        # 1) Auth & Team
        token = get_token_from_config()
        if not token: return
        team_id = fetch_team_id(token)
        if not team_id: return

        # 2) Switch to EZShare Wi-Fi
        if not switch_wifi(EZSHARE_PROFILE):
            log("Aborting: cannot reach EZShare WiFi")
            return

        # Ensure download root exists
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        # 3) Read last state
        last_date, last_hash = read_last_uploaded_info()

        # 4) Scrape root directory listing
        log("⏳ Fetching root directory…")
        r = requests.get(f"{EZSHARE_BASE}/dir", timeout=10)
        r.raise_for_status()
        root_soup = BeautifulSoup(r.text, "html.parser")

        # Find root-level files, DATALOG and SETTINGS hrefs
        datalog_href = settings_href = None
        for a in root_soup.find_all("a"):
            label = a.text.strip()
            href  = a.get("href","")
            if label == "DATALOG":
                datalog_href = href
            elif label == "SETTINGS":
                settings_href = href

        # 5) Scrape DATALOG listing to get each date-folder href
        log("⏳ Fetching DATALOG listing…")
        r = requests.get(f"{EZSHARE_BASE}/{datalog_href}", timeout=10)
        r.raise_for_status()
        datalog_soup = BeautifulSoup(r.text, "html.parser")
        remote_date_hrefs = {}
        for a in datalog_soup.find_all("a"):
            label = a.text.strip()
            href  = a.get("href","")
            if label.isdigit() and len(label)==8:
                remote_date_hrefs[label] = href
        remote_dates = sorted(remote_date_hrefs.keys())
        log(f"✅ Remote DATALOG folders: {remote_dates}")

        # 6) Determine forced date (optional override)
        forced_date = os.environ.get("FORCE_DATE")
        if forced_date:
            new_dates = [d for d in remote_dates if d >= forced_date]
            changed = True  # force re-upload path
            log(f"▶ FORCE_DATE override: new_dates = {new_dates}")
        else:
            new_dates = [d for d in remote_dates if last_date is None or d > last_date]
            log(f"▶ new_dates = {new_dates}")

        # 7) REMOTE-HASH-CHECK last_date
        changed = False
        if last_date and last_date in remote_date_hrefs and last_hash:
            log(f"▶ Remote hash-checking DATALOG/{last_date}")
            current_hash = remote_hash_folder(last_date)
            if current_hash != last_hash:
                log("🔄 Change detected in last_date folder")
                changed = True
            else:
                log("✅ No change in last_date folder")

        # 8) Bail if nothing new and unchanged
        if not new_dates and not changed:
            log("✅ No new data and no changes detected. Exiting.")
            return

        # 9) Clean up before full download
        shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        if os.path.exists(ZIP_OUTPUT):
            os.remove(ZIP_OUTPUT)
        log("✅ Cleaned previous downloads")

        # 10) Determine start_date
        start_date = forced_date or (last_date if changed else min(new_dates))
        log(f"▶ start_date = {start_date}")

        # 11) Download root files
        log("▶ Downloading root files")
        for a in root_soup.find_all("a"):
            href  = a.get("href","")
            label = a.text.strip()
            if "download?file=" in href and any(label.lower().endswith(ext) for ext in WHITELIST):
                download_file(href, DOWNLOAD_DIR, label)

        # 12) Download SETTINGS folder
        log("▶ Downloading SETTINGS folder")
        settings_dir = os.path.join(DOWNLOAD_DIR, "SETTINGS")
        os.makedirs(settings_dir, exist_ok=True)
        r = requests.get(f"{EZSHARE_BASE}/{settings_href}", timeout=10)
        r.raise_for_status()
        set_soup = BeautifulSoup(r.text, "html.parser")
        for a in set_soup.find_all("a"):
            href  = a.get("href","")
            label = a.text.strip()
            if "download?file=" in href and any(label.lower().endswith(ext) for ext in WHITELIST):
                download_file(href, settings_dir, label)

        # 13) Download DATALOG ≥ start_date
        for date in remote_dates:
            if date < start_date:
                log(f"⏩ Skipping DATALOG/{date}")
                continue
            log(f"⬇️ Downloading DATALOG/{date}")
            target = os.path.join(DOWNLOAD_DIR, "DATALOG", date)
            os.makedirs(target, exist_ok=True)
            r = requests.get(f"{EZSHARE_BASE}/{remote_date_hrefs[date]}", timeout=10)
            r.raise_for_status()
            date_soup = BeautifulSoup(r.text, "html.parser")
            for a in date_soup.find_all("a"):
                href  = a.get("href","")
                label = a.text.strip()
                if "download?file=" in href and any(label.lower().endswith(ext) for ext in WHITELIST):
                    download_file(href, target, label)

        # 14) Zip, switch home, upload & save state
        zip_folder(ZIP_OUTPUT)
        latest = sorted(os.listdir(os.path.join(DOWNLOAD_DIR, "DATALOG")))[-1]
        new_hash = remote_hash_folder(latest)
        if not switch_wifi(HOME_WIFI_PROFILE):
            log("❌ Could not switch back to home WiFi.")
            return
        time.sleep(5)

        import_id = create_import(token, team_id)
        if import_id:
            start_time = time.time()
            upload_zip(token, import_id, ZIP_OUTPUT)
            process_import(token, import_id)
            duration = round(time.time() - start_time)
            upload_hash = hash_folder(os.path.join(DOWNLOAD_DIR, os.listdir(os.path.join(DOWNLOAD_DIR, 'DATALOG'))[-1]))
            save_uploaded_info(latest, new_hash)
            append_upload_log(latest, upload_hash, "success", duration)

    except Exception as e:
        error_msg = f"{datetime.now().isoformat()} - {str(e)}"
        with open("upload_errors.log", "a") as errf:
            errf.write(error_msg + "\n")
        log(f"❌ Unexpected error: {e}")
    finally:
        log("🔄 Restoring home WiFi…")
        switch_wifi(HOME_WIFI_PROFILE)
        log("=== END main ===")

if __name__ == "__main__":
    if "--force-date" in sys.argv:
        idx = sys.argv.index("--force-date") + 1
        if idx < len(sys.argv):
            forced_date = sys.argv[idx]
            os.environ["FORCE_DATE"] = forced_date
    main()