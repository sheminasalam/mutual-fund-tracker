# Mutual Fund Tracker Documentation

The complete current user guide and import procedure is maintained in:

**[README.md](README.md)**

It covers:
- India-only scope and market assumptions.
- CAMS/KFintech consolidated-statement retrieval.
- PDF → external AI → compact JSON → import workflow.
- Investor matching and multi-statement merges.
- Recent SIP reconciliation, including Stopped / inactive handling.
- SIP and Lumpsum fund creation.
- Fund editing and transaction entry.
- NAV refresh and NIFTY 50 behavior.
- Home Assistant integration entities.
- Dark mode, export, warnings, and troubleshooting.

## SIP execution rule

Normal live SIP execution is independent from historical statement import reconciliation.

A live SIP executes only when the normal scheduler's established NAV/date eligibility rules are satisfied. The dedicated live SIP execution record is used for **SIP Executed Today**.

During import, recent missing SIPs are handled separately. An active SIP whose applicable NAV is already the latest NAV for that fund may be recorded automatically as a current/latest SIP; an older-gap SIP requires an explicit Executed or Skipped decision. A SIP explicitly marked **Stopped / inactive** always takes precedence and is never auto-executed during import.
