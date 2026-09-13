# Mutual Fund Tracker — Home Assistant integration

The **Mutual Fund Tracker** integration connects Home Assistant to the Mutual Fund Tracker app. The app performs portfolio, NAV, SIP and import processing; the integration exposes the resulting data as Home Assistant entities and provides refresh/export controls.

> **The app and integration are separate components.** Install and run the Mutual Fund Tracker app first. The integration does not download mutual-fund NAV data itself.

## Installation

### HACS (recommended)

1. Open **HACS → Integrations**.
2. Add `https://github.com/sheminasalam/mutual-fund-tracker` as a **Custom repository** and select **Integration** if the repository is not already in HACS.
3. Install **Mutual Fund Tracker**.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add Integration** and add **Mutual Fund Tracker**.

### Manual

Copy `custom_components/mutual_fund_tracker/` into `/config/custom_components/mutual_fund_tracker/` and restart Home Assistant.

## Data source

The integration reads the app's exported local state file. It does not contact AMFI/MFAPI directly and therefore does not create additional NAV-provider traffic.

The app exports its state to one of the following paths, depending on installation/environment:

- `/share/mutual_fund_tracker/integration_state.json`
- `/config/mutual_fund_tracker/integration_state.json`

## Entities

The integration provides portfolio sensors such as:

- Total Value
- Total Invested
- Total Profit
- Total Profit %
- Daily Change
- Monthly Change
- Yearly Change
- Portfolio XIRR
- Fund Count

Status/control entities include:

- **NSE Today**
- **NSE Tomorrow**
- **NSE Trading**
- **NSE Yesterday Status**
- **NAV Refresh Error**
- **SIP Executed Today**
- **Delayed NAV Update**
- **Refresh NAV**
- **Export Portfolio PNG**

## Custom Lovelace card

The repository includes the **Mutual Fund Tracker** custom Lovelace card. It reads the integration entities and does not access the app database directly.

## Troubleshooting

If the integration shows stale values, first verify that the app is running and its `integration_state.json` is being updated. Then reload the integration or restart Home Assistant if necessary.

For the full application documentation and import instructions, see the repository [README](../../README.md).
