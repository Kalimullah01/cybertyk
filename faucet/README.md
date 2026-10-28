# Wartale BCH faucet helper

A command-line helper for the Bitcoin Cash faucet at
`https://test.wartale.com/faucet.asp?instance=dev%5Ftest&action=1`.

## How the faucet works

1. `GET` the page to see the balance, the reward and whether you can claim now.
2. `POST` your BCH address in the `addr` field. The site then redirects you to a captcha page.
3. Solve the captcha and press **Vote**. The payout arrives within about 30 minutes.

You can claim once per IP every 12 hours.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium   # only needed for `claim`
```

## Usage

```bash
python faucet_helper.py status          # balance / reward / claimable
python faucet_helper.py status --json
python faucet_helper.py claim -a bitcoincash:qq...
```

`claim` validates the address and checks the faucet's status and a local 12h
cooldown (stored in `~/.wartale_faucet.json`). It then opens a real browser
with the address already submitted. **You solve the captcha yourself.** The
tool does not bypass the captcha or the per-IP limit.
