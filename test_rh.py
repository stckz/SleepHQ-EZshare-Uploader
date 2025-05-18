#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import time
import subprocess
import hashlib
import requests
from datetime import datetime
from urllib.parse import unquote
from bs4 import BeautifulSoup

# ─── Configuration ─────────────────────────────────────────────────────────────
EZSHARE_BASE       = "http://192.168.4.1"
WHITELIST          = [".edf", ".crc", ".json", ".tgt", ".log"]
OUTPUT_FILE        = "test_hash.txt"
EZSHARE_PROFILE    = "ezshare"
HOME_WIFI_PROFILE  = "homewifi"

def log(msg):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    # ← Add these two lines to also write into logtesthash.txt
    with open("logtesthash.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")

def switch_wifi(profile):
    log(f"Switching to Wi-Fi profile '{profile}'…")
    res = subprocess.run(
        ["nmcli", "connection", "up", profile],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        log(f"ERROR: cannot switch to {profile}: {res.stderr.strip()}")
        sys.exit(1)
    log(f"✅ Now on '{profile}'")
    time.sleep(5)

def resolve_url(href: str) -> str:
    """
    If href is an absolute URL, return it.
    Otherwise prefix with EZSHARE_BASE.
    """
    href = href.strip()
    if href.lower().startswith("http"):
        return href
    # drop any leading slash so we don't get // in URL
    return f"{EZSHARE_BASE.rstrip('/')}/{href.lstrip('/')}"

def remote_hash_folder(date: str) -> str:
    # 1) Find the DATALOG folder link on the root /dir page
    r = requests.get(f"{EZSHARE_BASE}/dir", timeout=10)
    r.raise_for_status()
    root = BeautifulSoup(r.text, "html.parser")
    datalog_href = next(
        (a["href"] for a in root.find_all("a") if a.text.strip()=="DATALOG"),
        None
    )
    if not datalog_href:
        raise RuntimeError("Could not find DATALOG link on /dir")

    # 2) Find the link for the specific date under DATALOG
    r2 = requests.get(resolve_url(datalog_href), timeout=10)
    r2.raise_for_status()
    dl = BeautifulSoup(r2.text, "html.parser")
    date_href = next(
        (a["href"] for a in dl.find_all("a") if a.text.strip()==date),
        None
    )
    if not date_href:
        raise RuntimeError(f"Could not find folder for date {date}")

    # 3) Scrape that date-folder page
    r3 = requests.get(resolve_url(date_href), timeout=10)
    r3.raise_for_status()
    ds = BeautifulSoup(r3.text, "html.parser")

    # 4) HEAD each whitelisted file, build hash of "name:size\n"
    sha = hashlib.sha256()
    sess = requests.Session()
    for a in ds.find_all("a"):
        name = a.text.strip()
        href = a.get("href","")
        if "download?file=" not in href:
            continue
        if not any(name.lower().endswith(ext) for ext in WHITELIST):
            continue

        file_url = resolve_url(href)
        log(f"  HEAD {name} → {file_url}")
        head = sess.head(file_url, timeout=5)
        head.raise_for_status()
        size = head.headers.get("Content-Length", "0")
        sha.update(f"{name}:{size}\n".encode("utf-8"))

    return sha.hexdigest()

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 test_remote_hash.py <YYYYMMDD>")
        sys.exit(1)
    date = sys.argv[1]

    # Switch onto the CPAP card
    switch_wifi(EZSHARE_PROFILE)

    try:
        log(f"Computing remote hash for DATALOG/{date}…")
        h = remote_hash_folder(date)
        log(f"✔ Remote hash: {h}")

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(h + "\n")
        log(f"Hash written to {OUTPUT_FILE}")

    except Exception as e:
        log(f"❌ Error: {e}")
        sys.exit(1)

    finally:
        switch_wifi(HOME_WIFI_PROFILE)

if __name__ == "__main__":
    main()