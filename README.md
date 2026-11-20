# Solana Paper Trading Bot

Live DexScreener market data with simulated trades only. No wallet, private keys, signing, or real orders.

## Railway setup
1. Deploy this repository from GitHub.
2. Set start command to `python bot.py`.
3. Add a Railway Volume mounted at `/data`.
4. Add Variables:
   - `DASH_USER` = a username you choose
   - `DASH_PASSWORD` = a unique random password (at least 16 characters)
   - `DB_PATH` = `/data/paperbot.sqlite3`
   - `START_CASH` = `70` (optional)
5. In Networking, generate a public domain. Open it in Safari and sign in with the dashboard username/password.
6. After files are uploaded and Railway is connected, change this GitHub repository back to **Private**.

## What it does
Scans recent Solana token profiles and pairs using DexScreener. Filters by liquidity, 1-hour volume and age; paper-buys positive 1-hour momentum candidates. Defaults: $5 simulated entry, up to 4 positions, 20% stop-loss and 35% take-profit. Settings can be changed with environment variables.

This is an experimental demonstration, not a validated or profitable strategy. DexScreener prices are not guaranteed executable prices. Simulated fills omit fees, slippage, failed transactions, MEV and liquidity impact. Basic filters do not prove a token is safe. Do not treat the output as investment advice.

## Local test
`DASH_PASSWORD='choose-a-long-random-password' python bot.py`
