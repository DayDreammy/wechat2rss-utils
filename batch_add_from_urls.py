#!/usr/bin/env python3

import argparse
import logging
import os
import random
import sys
import time
from typing import List, Tuple, Optional

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Batch add WeChat Official Account subscriptions to Wechat2RSS from a list of article URLs. "
            "Respects rate limits by inserting randomized delays and uses retries with exponential backoff."
        )
    )
    parser.add_argument(
        "--base-url",
        required=False,
        default=os.environ.get("WECHAT2RSS_BASE_URL") or os.environ.get("BASE_URL"),
        help=(
            "Base URL of your Wechat2RSS service, e.g. https://rss.example.com or http://127.0.0.1:8000. "
            "Can also be provided via WECHAT2RSS_BASE_URL or BASE_URL env vars."
        ),
    )
    parser.add_argument(
        "--token",
        required=False,
        default=os.environ.get("RSS_TOKEN") or os.environ.get("WECHAT2RSS_TOKEN") or os.environ.get("TOKEN"),
        help=(
            "RSS_TOKEN for API auth (passed as k=token). Can also be provided via RSS_TOKEN/WECHAT2RSS_TOKEN/TOKEN env vars."
        ),
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a text file containing WeChat article URLs (one per line). Lines starting with # are ignored.",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=8.0,
        help="Minimum seconds to sleep between requests (default: 8.0)",
    )
    parser.add_argument(
        "--max-interval",
        type=float,
        default=15.0,
        help="Maximum seconds to sleep between requests (default: 15.0)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds (default: 15.0)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum number of retries per URL on transient errors (default: 5)",
    )
    parser.add_argument(
        "--backoff-base",
        type=float,
        default=1.5,
        help="Exponential backoff base for retries (default: 1.5)",
    )
    parser.add_argument(
        "--jitter",
        type=float,
        default=0.3,
        help="Add +/- percentage jitter to backoff delays (0.3 = +/-30%) (default: 0.3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not perform API calls; only parse and print what would be done.",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Deduplicate identical URLs in the input before processing.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional path to write a log file (in addition to console).",
    )
    args = parser.parse_args()

    if not args.base_url:
        parser.error("--base-url is required (or set WECHAT2RSS_BASE_URL/BASE_URL env var)")
    if not args.token:
        parser.error("--token is required (or set RSS_TOKEN/WECHAT2RSS_TOKEN/TOKEN env var)")

    return args


def setup_logging(log_file: Optional[str]) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def read_urls(input_path: str, dedupe: bool) -> List[str]:
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    urls: List[str] = []
    with open(input_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)

    if dedupe:
        # Preserve order while deduping
        seen = set()
        deduped: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        return deduped

    return urls


def sleep_within(min_s: float, max_s: float) -> None:
    if max_s <= 0:
        return
    if min_s < 0:
        min_s = 0
    if max_s < min_s:
        max_s = min_s
    delay = random.uniform(min_s, max_s)
    logging.info(f"Sleeping {delay:.2f}s to respect rate limits...")
    time.sleep(delay)


def calc_backoff_delay(attempt: int, base: float, jitter: float) -> float:
    # attempt starts at 1 for first retry
    delay = base ** attempt
    # Apply +/- jitter percentage
    if jitter > 0:
        delta = delay * jitter
        delay = random.uniform(max(0.0, delay - delta), delay + delta)
    # Cap a reasonable upper bound to avoid extremely long sleeps
    return min(delay, 300.0)  # 5 minutes cap


def add_url_once(session: requests.Session, base_url: str, token: str, url: str, timeout: float) -> Tuple[bool, str]:
    endpoint = base_url.rstrip("/") + "/addurl"
    params = {
        "k": token,
        "url": url,
    }
    try:
        resp = session.get(endpoint, params=params, timeout=timeout)
    except requests.RequestException as e:
        return False, f"Network error: {e}"

    if resp.status_code == 200:
        # Expect JSON with {"err": "", "data": "http://xxx"}
        try:
            payload = resp.json()
        except ValueError:
            return False, f"Invalid JSON response: {resp.text[:200]}"

        err = str(payload.get("err", ""))
        if err:
            return False, f"API error: {err}"

        data = payload.get("data")
        if not isinstance(data, str) or not data:
            return False, f"Unexpected API response: {payload}"
        return True, data

    # Handle common transient statuses
    if resp.status_code in (429, 502, 503, 504):
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"

    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


def add_id_once(session: requests.Session, base_url: str, token: str, biz_id: str, timeout: float) -> Tuple[bool, str]:
    endpoint = base_url.rstrip("/") + f"/add/{biz_id}"
    params = {"k": token}
    try:
        resp = session.get(endpoint, params=params, timeout=timeout)
    except requests.RequestException as e:
        return False, f"Network error: {e}"

    if resp.status_code == 200:
        try:
            payload = resp.json()
        except ValueError:
            return False, f"Invalid JSON response: {resp.text[:200]}"

        err = str(payload.get("err", ""))
        if err:
            return False, f"API error: {err}"

        data = payload.get("data")
        if not isinstance(data, str) or not data:
            return False, f"Unexpected API response: {payload}"
        return True, data

    if resp.status_code in (429, 502, 503, 504):
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"

    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


def process_urls(
    urls: List[str],
    base_url: str,
    token: str,
    min_interval: float,
    max_interval: float,
    timeout: float,
    max_retries: int,
    backoff_base: float,
    jitter: float,
    dry_run: bool,
) -> None:
    session = requests.Session()

    total = len(urls)
    success_count = 0
    fail_count = 0

    for idx, item in enumerate(urls, start=1):
        prefix = f"[{idx}/{total}]"
        text = item.strip()
        is_numeric_id = text.isdigit()

        if dry_run:
            what = "ID" if is_numeric_id else "URL"
            logging.info(f"{prefix} DRY RUN would add {what}: {text}")
            sleep_within(min_interval, max_interval)
            continue

        attempt = 0
        while True:
            attempt += 1
            if is_numeric_id:
                ok, msg = add_id_once(session, base_url, token, text, timeout)
            else:
                ok, msg = add_url_once(session, base_url, token, text, timeout)

            if ok:
                success_count += 1
                logging.info(f"{prefix} Added OK -> feed: {msg}")
                break

            if attempt > max_retries:
                fail_count += 1
                logging.error(f"{prefix} Failed after {max_retries} retries: {msg}")
                break

            lower_msg = msg.lower()
            extra_wait = 0.0
            if any(k in lower_msg for k in ["429", "rate", "limit", "风控", "频率", "too many"]):
                extra_wait = random.uniform(20.0, 60.0)

            delay = calc_backoff_delay(attempt, backoff_base, jitter) + extra_wait
            logging.warning(
                f"{prefix} Attempt {attempt} failed: {msg}. Retrying in {delay:.1f}s..."
            )
            time.sleep(delay)

        sleep_within(min_interval, max_interval)

    logging.info(f"Done. Success: {success_count}, Failed: {fail_count}, Total: {total}")


def main() -> None:
    args = parse_args()
    setup_logging(args.log_file)

    try:
        urls = read_urls(args.input, dedupe=args.dedupe)
    except Exception as e:
        logging.error(f"Failed to read input: {e}")
        sys.exit(2)

    if not urls:
        logging.warning("No URLs to process. Exiting.")
        return

    logging.info(
        "Starting batch add: %d URLs; interval %.1f-%.1fs; max_retries=%d; dry_run=%s",
        len(urls), args.min_interval, args.max_interval, args.max_retries, args.dry_run,
    )

    process_urls(
        urls=urls,
        base_url=args.base_url,
        token=args.token,
        min_interval=args.min_interval,
        max_interval=args.max_interval,
        timeout=args.timeout,
        max_retries=args.max_retries,
        backoff_base=args.backoff_base,
        jitter=args.jitter,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main() 