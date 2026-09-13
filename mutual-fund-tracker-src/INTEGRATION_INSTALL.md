# Home Assistant integration installation

The Mutual Fund Tracker project has two components: the **Mutual Fund Tracker app** and the **Home Assistant integration**. Install and start the app first.

## HACS

1. Open **HACS → Integrations**.
2. Add `https://github.com/sheminasalam/mutual-fund-tracker` as a custom repository with category **Integration** if needed.
3. Install **Mutual Fund Tracker**.
4. Restart Home Assistant.
5. Add **Mutual Fund Tracker** from **Settings → Devices & services**.

## Manual installation

Copy `custom_components/mutual_fund_tracker` to `/config/custom_components/mutual_fund_tracker` and restart Home Assistant.

## State file

The app exports live integration state to one of these paths:

- `/share/mutual_fund_tracker/integration_state.json`
- `/config/mutual_fund_tracker/integration_state.json`

The integration polls the state file and exposes the portfolio, SIP, NAV refresh and NSE entities. It does not access the AMFI/MFAPI endpoints directly.
