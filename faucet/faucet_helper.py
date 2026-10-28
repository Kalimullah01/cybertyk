#!/usr/bin/env python3
"""Wartale BCH faucet helper.

Commands:
  status                 Show faucet balance, reward and whether a claim is available.
  claim --address ADDR   Open a real browser, fill in the address and submit it.
                         You solve the captcha and press "Vote" yourself.

The faucet allows one claim per IP every 12 hours and protects claims with a
captcha. This tool respects both: it never solves captchas or rotates IPs, and
it keeps a local cooldown so you don't submit early.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://test.wartale.com"
FAUCET_PATH = "/faucet.asp?instance=dev%5Ftest&action=1"
FAUCET_URL = BASE_URL + FAUCET_PATH
COOLDOWN_SECONDS = 12 * 60 * 60
STATE_FILE = Path.home() / ".wartale_faucet.json"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) wartale-faucet-helper/1.0"

# CashAddr (with or without the "bitcoincash:" prefix) or legacy base58 address.
CASHADDR_RE = re.compile(r"^(bitcoincash:)?[qp][02-9ac-hj-np-z]{41}$")
LEGACY_RE = re.compile(r"^[13][1-9A-HJ-NP-Za-km-z]{25,34}$")


@dataclass
class FaucetStatus:
    balance: str | None
    reward: str | None
    claimable: bool
    message: str


def is_valid_bch_address(addr: str) -> bool:
    addr = addr.strip()
    return bool(CASHADDR_RE.match(addr.lower()) or LEGACY_RE.match(addr))


def parse_status(html: str) -> FaucetStatus:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    balance = re.search(r"Faucet Balance:\s*(\$[\d.,]+)", text)
    reward = re.search(r"gives you\s*(\$[\d.,]+)", text)

    # The availability line is the last paragraph before the form.
    message = ""
    for p in soup.find_all("p"):
        t = p.get_text(" ", strip=True)
        if "claim the Faucet" in t:
            message = t
    claimable = "right now" in message.lower()
    has_form = soup.find("form", id="do_action") is not None

    return FaucetStatus(
        balance=balance.group(1) if balance else None,
        reward=reward.group(1) if reward else None,
        claimable=claimable and has_form,
        message=message or "Could not find the availability message on the page.",
    )


def fetch_status(timeout: float = 30) -> FaucetStatus:
    resp = requests.get(FAUCET_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return parse_status(resp.text)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def cooldown_remaining(state: dict) -> int:
    last = state.get("last_claim_ts", 0)
    return max(0, int(last + COOLDOWN_SECONDS - time.time()))


def fmt_seconds(s: int) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m}m {sec}s"


def cmd_status(args: argparse.Namespace) -> int:
    status = fetch_status()
    if args.json:
        print(json.dumps(asdict(status), indent=2))
    else:
        print(f"Balance   : {status.balance or 'unknown'}")
        print(f"Reward    : {status.reward or 'unknown'}")
        print(f"Claimable : {'yes' if status.claimable else 'no'}")
        print(f"Message   : {status.message}")
    remaining = cooldown_remaining(load_state())
    if remaining:
        print(f"Local cooldown: {fmt_seconds(remaining)} until your next claim.")
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    addr = args.address.strip()
    if not is_valid_bch_address(addr):
        print(f"Not a valid Bitcoin Cash address: {addr!r}", file=sys.stderr)
        return 2

    state = load_state()
    remaining = cooldown_remaining(state)
    if remaining and not args.ignore_cooldown:
        print(f"Cooldown active: wait {fmt_seconds(remaining)} (12h per IP).")
        return 1

    status = fetch_status()
    if not status.claimable:
        print(f"The faucet says you can't claim right now: {status.message}")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # No Playwright (e.g. Termux on Android): use the system browser instead.
        return claim_with_system_browser(addr, state)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(FAUCET_URL)
        page.fill("#addr", addr)
        page.click("form#do_action input[type=submit]")
        page.wait_for_load_state()

        print("\nAddress submitted. In the browser window:")
        print("  1. Solve the captcha")
        print("  2. Press 'Vote'")
        input("Press Enter here once you're done (or close the browser)... ")

        try:
            final_text = page.inner_text("body")
        except Exception:
            final_text = ""
        browser.close()

    if final_text:
        print("\nLast page said:\n" + final_text.strip()[:1000])

    state.update(last_claim_ts=int(time.time()), last_address=addr)
    save_state(state)
    print(f"\nClaim recorded. Next claim allowed in {fmt_seconds(COOLDOWN_SECONDS)}.")
    return 0


def claim_with_system_browser(addr: str, state: dict) -> int:
    """Copy the address to the clipboard and open the faucet in the default browser."""
    if shutil.which("termux-clipboard-set"):
        subprocess.run(["termux-clipboard-set", addr], check=False)
        print("Address copied to clipboard.")
    else:
        print(f"Copy this address: {addr}")

    if shutil.which("termux-open-url"):
        subprocess.run(["termux-open-url", FAUCET_URL], check=False)
    else:
        webbrowser.open(FAUCET_URL)

    print("\nIn the browser:")
    print("  1. Paste your address and press 'Claim Faucet!'")
    print("  2. Solve the captcha")
    print("  3. Press 'Vote'")
    answer = input("Did the claim go through? [y/N] ").strip().lower()
    if answer != "y":
        print("Not recorded.")
        return 1

    state.update(last_claim_ts=int(time.time()), last_address=addr)
    save_state(state)
    print(f"Claim recorded. Next claim allowed in {fmt_seconds(COOLDOWN_SECONDS)}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="show faucet status")
    s.add_argument("--json", action="store_true", help="print status as JSON")
    s.set_defaults(func=cmd_status)

    c = sub.add_parser("claim", help="submit your address; you solve the captcha")
    c.add_argument("--address", "-a", required=True, help="your BCH address")
    c.add_argument("--ignore-cooldown", action="store_true",
                   help="skip the local 12h check (the server still enforces it)")
    c.set_defaults(func=cmd_claim)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except requests.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
