#!/usr/bin/env python3
"""
Free iPhone Duo pickup bot (runs on GitHub Actions, no computer needed).

All day (7:00 AM to 9:30 PM Austin time) it watches Apple's public in-store
pickup info for Domain Northside and Barton Creek, checking about every
20 seconds, and pushes an urgent alert to your phone through the free ntfy
app the moment your model shows up.

GitHub limits one run to 6 hours, so each run watches for up to ~5h40m and
the workflow starts a fresh run as soon as the previous one ends.

Settings come from GitHub repository secrets:
  NTFY_TOPIC  your private ntfy topic name (required)
  DUO_PART    part number(s), comma-separated, e.g. "MXXXXLL/A"
              (leave empty the first time: the bot will look up the
               part numbers and send them to your phone)
"""

import datetime as dt
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
PARTS = [p.strip() for p in os.environ.get("DUO_PART", "").split(",") if p.strip()]
ZIP = os.environ.get("DUO_ZIP", "78758")
STORE_KEYWORDS = ["domain", "barton"]
NAMES = {"MK1F4LL/A": "256GB Night Sky", "MK1H4LL/A": "512GB Night Sky"}

TZ = ZoneInfo("America/Chicago")
DAY_START = dt.time(7, 0)            # start watching (Austin time)
DAY_END = dt.time(21, 30)            # stop watching (Austin time)
MAX_RUN = dt.timedelta(hours=5, minutes=40)  # stay under GitHub's 6h limit
CHECK_EVERY = 20  # seconds, plus a little random jitter

BUY_PAGE = "https://www.apple.com/shop/buy-iphone/iphone-duo"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/html;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def notify(title, message, priority="urgent"):
    req = urllib.request.Request(
        f"https://ntfy.sh/{urllib.parse.quote(NTFY_TOPIC)}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "iphone",
                 "Click": BUY_PAGE},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"could not send alert: {e}")


def find_parts():
    html = get(BUY_PAGE)
    found = []
    for p in sorted(set(re.findall(r"\bM[A-Z0-9]{3,4}LL/A\b", html))):
        i = html.find(p)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[max(0, i - 300):i + 300]))
        hints = dict.fromkeys(re.findall(r"(256GB|512GB|1TB|2TB|Night Sky|Star White)", text))
        found.append(f"{p}  {' / '.join(hints)}")
    return found


def check():
    params = {"fae": "true", "pl": "true", "mts.0": "regular", "location": ZIP, "searchNearby": "true"}
    for i, p in enumerate(PARTS):
        params[f"parts.{i}"] = p
    data = json.loads(get("https://www.apple.com/shop/fulfillment-messages?"
                          + urllib.parse.urlencode(params)))
    hits = []
    for s in data["body"]["content"]["pickupMessage"]["stores"]:
        name = s.get("storeName", "")
        if not any(k in name.lower() for k in STORE_KEYWORDS):
            continue
        for p in PARTS:
            info = s.get("partsAvailability", {}).get(p, {})
            if info.get("pickupDisplay") == "available":
                hits.append(f"{name}: {NAMES.get(p, p)} - {info.get('pickupSearchQuote', 'Available')}")
    return hits


def main():
    if not NTFY_TOPIC:
        raise SystemExit("Add the NTFY_TOPIC secret first.")

    manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

    # First-time setup: no part number yet, so look them up and send them.
    if not PARTS:
        try:
            parts = find_parts()
            msg = "\n".join(parts) if parts else "Couldn't find part numbers automatically."
        except Exception as e:
            msg = f"Lookup failed: {e}"
        notify("iPhone Duo part numbers", msg, priority="default")
        print(msg)
        return

    now = dt.datetime.now(TZ)
    day_start = now.replace(hour=DAY_START.hour, minute=DAY_START.minute, second=0, microsecond=0)
    day_end = now.replace(hour=DAY_END.hour, minute=DAY_END.minute, second=0, microsecond=0)

    if manual:
        # Test run: check for 3 minutes right now and confirm alerts work.
        end = now + dt.timedelta(minutes=3)
        notify("Duo bot test", "Alerts are working. Running a 3-minute test check.", "default")
    else:
        if now < day_start - dt.timedelta(minutes=30) or now >= day_end:
            print("Outside watching hours, exiting.")
            return
        if now < day_start:
            time.sleep((day_start - now).total_seconds())
        end = min(dt.datetime.now(TZ) + MAX_RUN, day_end)
        print(f"Watching until {end:%H:%M} Austin time.")

    alerted, errors = set(), 0
    while dt.datetime.now(TZ) < end:
        stamp = dt.datetime.now(TZ).strftime("%H:%M:%S")
        try:
            hits = check()
            errors = 0
            if hits:
                print(f"[{stamp}] IN STOCK: {hits}")
                new = [h for h in hits if h not in alerted]
                if new:
                    notify("iPhone Duo available for pickup!",
                           "\n".join(new) + "\nReserve NOW in the Apple Store app.")
                    alerted.update(new)
            else:
                print(f"[{stamp}] not in stock")
                alerted.clear()
        except Exception as e:
            errors += 1
            print(f"[{stamp}] check failed: {e}")
            if errors == 3:
                notify("Duo bot problem", f"Checks are failing: {e}", "default")
        time.sleep(CHECK_EVERY + random.uniform(0, 5))

    if manual:
        notify("Duo bot test finished", "Test done. The bot will keep checking automatically.", "low")


if __name__ == "__main__":
    main()
