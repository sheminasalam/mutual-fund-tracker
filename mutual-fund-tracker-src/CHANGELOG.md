# Changelog

## v1.0.0
- First stable release.
- Includes the complete Mutual Fund Tracker Home Assistant app, Home Assistant integration, custom Lovelace card, CAMS/KFintech AI-assisted import workflow, SIP/NAV processing, and current AMFI NAV handling.
- Includes the AMFI historical-cache migration and the latest UI/export improvements from the 0.x development line.

v0.1.247
- Fix AMFI historical NAV cache migration by using a new cache namespace after the historical URL format change.

## v0.1.245
- Fix **SIP Executed Today** for delayed live SIPs whose NAV becomes available the following morning.
- Store new live executions against the established 23:00–22:59:59 logical reporting day instead of the raw calendar execution date.
- Keep a compatibility path for existing live records that already contain the raw following calendar date, without changing financial transaction history.

## v0.1.240
- Use the app/backend SIP execution data as the single source of truth for **SIP Executed Today** in the Home Assistant integration.
- Remove duplicate 23:00 logical-date calculation from the integration binary sensor; the backend already owns the 23:00–22:59:59 reporting window.
- Expose `reporting_date`, `count`, `total_amount`, and `details` from the integration sensor.
- Update the custom card to use the integration-provided count and total amount instead of recalculating them from the detail list.
- Preserve v0.1.237 as the functional baseline for all other behavior.

## v0.1.236
- Removed only the previous-day NSE-open presentation gate from SIP Executed Today; it again reflects today's dedicated live SIP execution records regardless of the previous day's NSE status.

## v0.1.235
- Change the custom card metric label from Today to Day Change.
- Show total Profit percentage in parentheses beside the Profit amount.

## v0.1.234
- Add Month Change to the custom card investor metric row.

## v0.1.233
- Custom card: remove the redundant investor/latest-NAV line beneath the title.
- Custom card: resolve the investor latest-NAV date using the current `latest_nav_date` entity with `nav_date` as a compatibility fallback.

## v0.1.232
- Corrected **Next Expected SIP** pending-cycle selection to compare each fund's SIP date against that fund's own latest NAV date.
- SIP dates on or before a fund's latest NAV are excluded; dates after it remain candidates.
- Preserved the existing holiday batching and expected-date rules, including non-working SIP dates rolling forward to the next NSE working day.

## v0.1.231
- Surgically filter **SIP Executed Today** display records when the immediately preceding calendar day was NSE-closed.
- Keep all underlying SIP transactions and live execution records unchanged; this is a sensor/integration presentation rule only.
- Saturday remains eligible when Friday was an NSE trading day.

## v0.1.230
- Fixed SIP duplicate detection when an imported SIP transaction falls on an effective NAV date in the following calendar month.
- Centralized SIP-cycle candidate-date handling so both recovery and final duplicate guards use the same scheduled/effective-date rule.

## v0.1.229
- Fixed SIP-cycle duplicate detection for imported SIP transactions recorded on the effective NAV/transaction date.
- Prevented import reconciliation and normal SIP execution from creating a second financial SIP for the same monthly cycle when a real imported SIP transaction already exists.
- Imported-cycle recognition repairs `last_sip_cycle` only and does not create `live_sip_executions`, preserving SIP Executed Today semantics.

## v0.1.228
- Fix HA entity-id collision for `Total Profit %`: use explicit `*_total_profit_pct` object IDs and migrate legacy `*_total_profit_2` entities safely.
- Keep custom-card compatibility with both the new and legacy percentage-profit entity IDs.

## v0.1.227
- Fixed custom-card NSE Tomorrow status label spacing so the switch, label, and reason text cannot overlap.

## v0.1.226
- Reworked Next Expected SIP to use a deterministic active-SIP schedule projection independent of execution bookkeeping.
- Removed the previous due/delayed execution-state-driven selection path for Next Expected SIP only.
- Preserved SIP execution, Upcoming SIP, Executed SIP, and SIP Executed Today logic unchanged.

## v0.1.217

- Updated the custom card NSE status strip to use the named **NSE Today**, **NSE Tomorrow**, and **NSE Trading** sensors, including the dedicated tomorrow sensor.
- Updated the Home Assistant integration version to 0.1.217.

## v0.1.216
- Increased graph maximum/minimum date text to match the statistic value font size and color, with additional spacing for clearer distinction.

## v0.1.214
- Increased Maximum/Median/Minimum graph statistic typography while reducing vertical padding/line-height so the statistics table does not become taller.

## v0.1.213
- Corrected current-month Executed SIP summary/table classification to recognize any real non-zero SIP transaction for the cycle, not only the dedicated live execution transaction.
- Kept zero-value deleted SIP markers excluded from Executed and Upcoming summaries.
- No changes to Next Expected SIP, SIP Executed Today, or the Home Assistant integration schema.

## v0.1.212

- Fix multi-parameter single-fund graph tooltip labels to show parameter names.

## v0.1.210
- Historical graph: when one fund has multiple selected parameters, render them in one combined chart with a shared INR axis and a separate percentage axis when Profit % is selected.
- Historical graph: show compact Maximum/Median/Minimum statistics for every selected parameter on single-fund multi-parameter charts.

- v0.1.209: Fix historical graph tooltip placement so the tooltip never covers the hover crosshair; retain all v0.1.208 graph markers and Tomorrow Open UI behavior.
## v0.1.208
- Improve historical graph hover markers and tooltip placement so the selected date/value remains visually traceable across all series.
- Improve Tomorrow Open header visibility in dark mode with brighter green text while preserving the existing closed-state behavior.

## v0.1.207
- Corrected safe reversal of sell/switch-out deletions when the holding cost comes from an authoritative imported balance.
- Separated SIP cycle-accounted markers from live executed-SIP status so deleted zero-value SIP markers block re-execution without appearing as executed.

## v0.1.206

- Corrected sell and switch-out invested-cost accounting to reduce cost pro-rata with remaining units across live transactions, multi-folio import reconstruction, and historical graph reconstruction.
- Deleting a live SIP now converts the financial transaction into a zero-value skipped marker, removes live execution records, keeps the SIP cycle accounted, hides the zero marker from transaction history, and explains the skipped-cycle behavior in the confirmation dialog.

## v0.1.205

- Change the header's NSE Tomorrow label to show "Tomorrow Open" when tomorrow is open and "Tomorrow Closed" when it is closed.
- Change the AI import prompt to require the JSON in a single fenced `json` code block so the chat UI download/save control can be used.

## v0.1.202

- Hardened the AMFI reference-NAV architecture: scheme-specific latest/previous/month/year records, atomic reference updates, bounded historical-date resolution, and persistent AMFI historical-date caching.
- Startup now warms AMFI latest/reference history without creating rows for unrelated AMFI schemes.
- Preserved MFAPI strictly for targeted historical NAV operations.

## 0.1.201
- Replace normal previous/month/year NAV reference sourcing with cached AMFI historical date files and a per-scheme comprehensive NAV reference table. MFAPI remains for targeted historical operations such as delayed SIPs and transaction history.

## 0.1.200
- Show "Tomorrow Closed" in the header whenever NSE Tomorrow is closed, while showing "Functional" when tomorrow is open.
- Tightened header status spacing so the NIFTY status text remains fully visible.
- No changes to NSE holiday/trading-day detection or Home Assistant entity logic.

## 0.1.197

- Fix import SIP confirmation mapping after preview expansion: repeated preview expansion now preserves holding/folio-scoped `_import_ref` keys, preventing valid selections from becoming unmatched during Review changes.

## 0.1.194

- Fix import preview SIP-status lookup: Database-level preview now resolves the holding/folio-scoped import decision correctly instead of calling a Tracker-only helper.
- Preserve the existing scheme-code fallback for older import clients.

## 0.1.193

- Use AMFI NAVAll.txt consistently for all latest/current NAV acquisition paths, including fresh/import initialization. Historical NAV retrieval remains on MFAPI.
- Make SIP active/inactive import decisions holding/folio-specific so multi-folio schemes can have independent SIP status.
- Recompute SIP recency/status after multi-folio expansion so stale folios are not treated as the active folio.

## 0.1.192

- Use AMFI NAVAll.txt as the primary latest-NAV source. Historical NAV retrieval remains on MFAPI; no SIP execution logic or historical lookup logic was changed.

## 0.1.189
- Fix custom card resolution of current Home Assistant aggregate entity suffixes (total_value, total_invested, total_profit, daily/monthly/yearly change, portfolio_xirr) without restoring redundant integration attributes.

## 0.1.188
- Made NIFTY polling use Yahoo Finance as the primary live source with the published Google Sheets CSV as fallback; removed the stale Google Visualization endpoint.
- Decoupled NIFTY state publication from the polling thread using a dedicated coalescing writer thread so a slow integration-state write cannot stop future NIFTY fetches.

## 0.1.186
- Reduced Home Assistant SIP/NAV sensor attributes to focused detail lists; removed redundant parallel fund/amount/count/total attributes from SIP entities.

# v0.1.185

- Delayed NAV Update now evaluates every holding, including funds without an active SIP, because NAV freshness is required for portfolio valuation as well as SIP processing.
- Added regression coverage for non-SIP holdings.

# v0.1.184

- Fix Delayed NAV Update boundary: status is ON when today reaches the delayed threshold.
- Present Delayed NAV Update as binary ON/OFF in the web app and card while retaining fund details.

# v0.1.183

- Expose Delayed NAV Update as a Home Assistant binary sensor.
- Keep the existing app/card display and fund-level attributes.

- Renamed the delayed-SIP sensor and UI label to "Delayed NAV Update"; detection logic unchanged.
# v0.1.182
- Renamed the delayed-SIP sensor and UI label to "Delayed NAV Update"; detection logic unchanged.

# v0.1.181

- Added shared Delayed SIP status classification and Home Assistant sensor with fund-level attributes.
- Added Executed SIP / Non-executed SIP table filters.
- Added Delayed SIP details to the web investor summary and Home Assistant card without changing the existing SIP execution logic.
- Kept Upcoming SIP and Next Expected SIP derived from the shared SIP status builder.

# v0.1.180

- Reworked SIP status classification into one shared per-profile cycle list.
- Added Executed SIP and Upcoming SIP sensors.
- Reworked Next Expected SIP to prioritize due/delayed cycles and otherwise compute the next NSE-open execution date.
- Preserved existing Today Executed SIP sensor and UI.
- No changes to NIFTY logic or UI layout.

# v0.1.179

- Simplified live SIP execution so normal and delayed NAVs share one final transaction path.
- When the latest NAV is later than the effective SIP date, the app checks the forward NAV history and uses the earliest applicable NAV.
- Added lightweight execution-history recovery so an already-recorded SIP repairs its cycle/log markers instead of creating a duplicate transaction.
- Kept `last_sip_cycle` as the existing cycle/cache marker; execution history is checked before it is used as the gate.
- Removed no existing SIP/NAV functionality beyond the redundant execution-gap restriction already removed in v0.1.178.

# v0.1.178

- SIP execution now resolves a pending SIP against the NAV closest to the effective SIP date when the triggering NAV date differs, using a targeted historical NAV lookup.
- Removed the arbitrary 3-trading-day SIP NAV gap limit; delayed NAV publication no longer causes a valid pending SIP to be abandoned or use an unrelated newer NAV.
- Existing SIP duplicate protection and normal latest-NAV refresh behavior remain unchanged.

# v0.1.177

- Decoupled NIFTY/market-status publishing from the full portfolio integration export so slow portfolio, XIRR, and SIP calculations cannot delay the 60-second NIFTY polling loop.
- Added a lightweight state publisher that updates only `market_status` while preserving the existing UI and state-file contract.
- Made integration-state revision increments atomic across full and lightweight exports.

# v0.1.176

- Make the known-working published NIFTY CSV endpoint the primary source and retain the Visualization CSV as backup.
- Keep existing NIFTY change detection, scheduler timing, fallback acceptance, and UI behavior unchanged.

# v0.1.174

## NIFTY 50 reliability
- Validated each Google Sheets response before accepting it, so unusable primary responses such as `#N/A` now fall through to the configured fallback source.
- Kept an existing valid primary quote authoritative; fallback data cannot silently replace it.
- Separated fetch time (`timestamp`) from actual numeric-data change time (`data_changed_at`) while preserving the existing UI and API field names.
- NIFTY quote changes are determined only from `(value, change, percent_change)`, preventing unchanged data from being reported as a fresh quote.
- Anchored the polling deadline to fetch start time so network latency no longer stretches the configured NIFTY interval.

# v0.1.173

## NIFTY 50 polling and countdown reliability
- Added cache-busting and explicit no-cache headers to every Google Sheets CSV request so the NIFTY poll cannot reuse a stale published CSV response.
- Exposed the server-side next NIFTY update deadline to the web UI.
- Replaced the relative “Updated … ago” header text with a live countdown such as “Next update in 42s” or “Next update in 4m 17s” while the market is open and NIFTY polling is enabled.
- Countdown switches to “Update stopped” when the market closes or NIFTY polling is disabled.
- Kept the existing market-hours boundary and configured polling interval logic unchanged.

# v0.1.171

## NSE / NIFTY 50 scheduler reliability
- Fixed NIFTY 50 polling so market-open transition at 09:00 IST triggers an immediate quote fetch, and polling stops at 15:30 IST.
- The NIFTY polling cadence now uses a monotonic clock, so wall-clock adjustments cannot stretch or shorten the configured interval.
- Changing the NIFTY interval while the market is open takes effect immediately instead of waiting for the previous deadline.
- Reduced market-status polling to 5 seconds so the 09:00/15:30 transitions are detected promptly.
- Prevented the market-status loop from rewriting the full Home Assistant integration state every few seconds just because `status_checked_at` changed; full exports now occur only on meaningful market-state or NIFTY-quote changes.

# v0.1.169

- Simplified the table classification filters by removing Structure, Asset Class, Management, and Horizon / Goal filters that offered only a single usable selection in practice.
- Changed Scheme Category to a standard single-select dropdown consistent with the other table filters.
- Kept Direct/Regular, Growth/IDCW, SIP status, return status, and numeric Parameter/Relationship/Value filtering unchanged.

# v0.1.168

- Fixed the automatic NAV refresh scheduler so it uses the persisted global successful-refresh timestamp and configured interval reliably.
- Automatic NAV refresh no longer gets its countdown postponed by per-holding refresh timestamps or an in-progress/previous refresh race.
- Added scheduler regression coverage for due/not-due behavior and first-run-without-success behavior.

# v0.1.166

- Added a SIP Amount total to the portfolio table totals row, summing currently active SIP amounts and respecting active table filters.
- Updated the AI import prompt to request a downloadable JSON file named from the investor name, with pasted JSON retained only as a fallback when the AI platform cannot create files.
- Updated the import README instructions to prefer downloading the AI-generated JSON file directly.

# v0.1.165

## Historical graph interaction improvements
- Added mouse-following vertical crosshair and date/value tooltip to historical line charts.
- Added maximum, median and minimum statistics for single-fund graph views, calculated from full-resolution historical data before display downsampling.
- Max/min statistics include the corresponding date; median is reported across the selected period.
- Multiple-fund graphs continue to omit the single-fund summary statistics to avoid misleading aggregation.

# v0.1.164

- Replaced the current-state Graphs tab with historical line graphs backed by a local historical-NAV cache and daily portfolio snapshots.
- Added multi-fund selection, multi-parameter selection (Total Value, Total Invested, Day Change, Profit %), and 1 Month / 30 Days / 3 Months / 6 Months / 1 Year / Inception periods.
- Historical data is fetched lazily and incrementally; long series are downsampled at query time.
- Graph snapshot caches are invalidated automatically when transactions are added or deleted so historical charts do not become stale.
- Warm the all-schemes MFAPI latest-NAV snapshot at application startup.
- Reuse a recent latest-NAV snapshot for operational workflows within the two-hour cache window.
- Skip redundant full NAV refreshes after imports/profile operations when the last successful refresh is less than two hours old.
- Manual and scheduled full NAV refreshes still force a fresh latest-NAV snapshot.

# v0.1.163

- Replaced the current-state Graphs tab with historical line graphs backed by a local historical-NAV cache and daily portfolio snapshots.
- Added multi-fund selection, multi-parameter selection (Total Value, Total Invested, Day Change, Profit %), and 1 Month / 30 Days / 3 Months / 6 Months / 1 Year / Inception periods.
- Historical data is fetched lazily and incrementally; long series are downsampled at query time.
- Graph snapshot caches are invalidated automatically when transactions are added or deleted so historical charts do not become stale.
- Warm the all-schemes MFAPI latest-NAV snapshot at application startup.
- Reuse a recent latest-NAV snapshot for operational workflows within the two-hour cache window.
- Skip redundant full NAV refreshes after imports/profile operations when the last successful refresh is less than two hours old.
- Manual and scheduled full NAV refreshes still force a fresh latest-NAV snapshot.

# v0.1.161

- Reworked full-portfolio current NAV refresh to use one MFAPI `/mf/latest` snapshot request, with scheme-code lookup and safe historical-reference fallback. Historical NAV lookups for SIP, Lumpsum, imports, and transaction previews remain unchanged.
- Preserved existing SIP execution semantics by passing the exact latest NAV record into the live SIP check.

# v0.1.160

- Added numeric Parameter / Relationship / Value fund filtering to the portfolio table.
- Numeric filters integrate with existing search/SIP/return filters and the Graphs tab.

# v0.1.159
- Added Table / Graphs tabs to the portfolio view.
- Added fund filters for search, SIP status, and total-return status.
- Added current-state Value vs Invested and Profit/Loss graphs that respect active filters.
- Filtered table totals now reflect the visible funds; XIRR is shown as unavailable for filtered subsets because it is not additive.
- Added a module logger for the existing integration-state error path so a state-write failure cannot trigger a secondary `NameError` in the market-status thread.


## UI actions, app metadata, and release documentation
- Removed the separate Add Investor button from the Investor Profile bar.
- Moved Add Investor into the existing top-right **+** menu alongside Import JSON and Add Mutual Fund.
- Removed `stage: experimental` from the Home Assistant app configuration so the app is no longer marked Experimental in Home Assistant.
- Added prominent documentation for the Custom Lovelace card, calculated portfolio metrics, NSE status, configurable NIFTY updates, and dark mode.

# v0.1.156
- Fixed stale Home Assistant integration/card state when multiple integration snapshot paths exist by selecting the newest valid snapshot.
- Added monotonic `state_revision` to exported integration state for deterministic change detection.
- Hardened dual-location state writes so a failure in one location does not prevent writing another valid location.
- No changes to portfolio, merge, NAV, SIP execution, app UI, or custom-card business logic.

# v0.1.155
- Hardened import SIP-status propagation: explicit Stopped / inactive choices are applied immediately, carried in affected holding state, and enforced again against persisted holding state before any post-import reconciliation.
- Normalized SIP status mapping keys to avoid scheme-code formatting mismatches.
- Added regression coverage for inactive SIP import decisions through the full import-to-final-execution path.

- Fixed Import JSON SIP-status propagation so an explicit Stopped / inactive choice is passed into the final post-import SIP reconciliation step.
- Added a backend safety check that prevents an explicitly inactive SIP from being executed even if a stale reconciliation preview or holding state still marks it active.
- Added regression coverage for the exact import failure where a fund selected as Stopped / inactive still raised “SIP decision is required”.

# v0.1.153

- Fixed import SIP reconciliation so an explicit Stopped / inactive choice is authoritative during backend validation and cannot produce a false "SIP decision is required" error.
- Fixed investor selector ordering to show investors in original creation order (oldest first), with stable ID tie-break.

# v0.1.152

## Import SIP status propagation and dark-mode investor identity
- Respect an explicit **Stopped / inactive** SIP choice from the Import JSON flow when processing recent SIP reconciliation records; such a fund can no longer be auto-executed merely because its NAV is the latest available NAV.
- Keep the normal/live SIP execution process unchanged.
- Show `Will not execute · SIP inactive` in the import reconciliation UI when the corresponding SIP activity decision is set to inactive.
- Completed the dark-mode styling for the **Investor identity from statement** section, including the action select, investor details, headings, hints, and option colors.
- No changes to database/API date formats or normal SIP execution logic.

# v0.1.151

## Dark-mode Import JSON dialog
- Added complete dark-mode styling for the Import JSON dialog and its nested warning, identity, preview, reconciliation, and transaction-list surfaces.
- Kept import behavior and backend logic unchanged.

# v0.1.150

## Import-time SIP reconciliation
- Limit consolidated-statement imports to statements generated within the last 10 days.
- Detect a scheduled SIP at/after the statement coverage date and reconcile it through the import flow.
- Automatically reconcile a SIP when its applicable NAV is already the latest available NAV for that fund; record it as a live/current SIP so SIP Executed Today reflects it.
- For SIPs whose applicable NAV is older than the fund's latest NAV, require an explicit Executed or Skipped decision during import.
- Historical import-reconciled SIPs are recorded as financial transactions without adding them to the live SIP execution store.
- A skipped historical SIP is marked accounted so the normal scheduler cannot resurrect it.
- Preserve the existing v0.1.145 normal SIP execution process and legacy post-import latest-NAV behavior.
- Added regression coverage for the 10-day import limit, auto-current SIP, historical Executed/Skipped choices, and live-status isolation.

# v0.1.149

- Added Home Assistant-only NSE Yesterday Status binary sensor.
- Renamed NSE Market Open entity to NSE Today Status.
- Renamed NSE Market Status entity to NSE Trading Status.
- Updated Home Assistant entity unique IDs accordingly.
- No app/card UI changes.

# v0.1.144

## Dark mode investor details fix
- Fixed the **Investor Details** table in dark mode so the table background, header cells, data cells, borders, and secondary text use the dark theme colors.
- Preserved all v0.1.143 dark-mode behavior; no backend/API/database logic changed.

## Dark mode for web UI
- Added a theme toggle icon between Settings and Exit in the web-app header.
- Added persistent light/dark theme preference using browser local storage.
- Added dark styling for the web application, including tables, forms, modals, investor sections, settings, progress boxes and menus.
- Custom Lovelace card styling is intentionally unchanged.

# v0.1.142
- Fixed mobile fund-editor scrolling: **Edit SIP** and **Transact** now scroll within the modal body on narrow screens.
- Kept the **Transactions** tab as the dedicated vertical transaction-table scroll area.
- Added tab-aware modal scrolling so switching tabs does not leave the body locked.

# v0.1.140
- Split the fund editor workflow into three tabs: **Edit SIP**, **Transact**, and **Transactions**.
- Moved the transaction-entry form to **Transact** and kept only the transaction history table in **Transactions**.
- Made the transaction history table the only vertical scrolling area in the transaction-history tab to avoid nested modal scrollbars.

# v0.1.139
- Simplified fund-editor modal scrolling so the modal stays within the viewport and the transaction list does not create a second nested vertical scrollbar.
- Kept the editor compatible with the responsive mobile layout and existing transaction actions.

# v0.1.138
- Improved fund-name splitting in the web table for names where the plan separator is written without surrounding spaces (for example `Fund-Direct Plan-Growth`).
- The existing two-line fund-name presentation is preserved; no backend/API/database behavior changed.

# v0.1.137

- Reworked the fund table for responsive use: restored natural column sizing, kept the table horizontally scrollable on touch/desktop, and hid the native scrollbar while retaining the synchronized floating scrollbar.
- Fixed the mobile fund editor layout: contained the modal within the viewport, stacked two-column form fields on narrow screens, and made the modal body independently scrollable.
- Preserved all existing fund-table, combined editor, date-display, NSE/NIFTY, NAV, SIP, integration, and portfolio behavior.

# v0.1.136

- Reworked the fund table presentation so fund names wrap into a primary line plus continuation line, allowing the table to fit the viewport without a horizontal scrollbar.
- Combined fund-table Edit and View actions into one Edit action that opens a tabbed fund editor with **Edit SIP** and **Transactions** tabs, with Delete Fund in the modal header.
- Standardized displayed calendar dates in the web app to **DD/MM/YYYY** while preserving ISO `YYYY-MM-DD` values for all API/database operations. Native date inputs explicitly use the `en-GB` page locale.

# v0.1.134

## Web-app action button consistency
- Updated the fund table Edit and View actions to use the same blue rounded-square icon-button style as the rest of the modernized web UI.
- Changed the Export PNG control from an icon-only button to a clear `Export PNG` text button to avoid ambiguity.
- Preserved all existing button handlers and behavior.

# v0.1.133

- Restyled the remaining web-app action buttons to match the new blue rounded-square control style: Refresh NAV, Add Investor, Edit Investor, Export PNG, and sort direction.
- Kept all existing button handlers and functionality unchanged; only presentation, icons, and accessibility metadata were updated.
- Sort direction now uses the compact blue up/down icon button while retaining the existing ascending/descending behavior.
- Added regression coverage for the unified button styling and sort-direction labels.

# v0.1.132

- Removed the browser-visible horizontal scrollbar from the main fund table; the floating synchronized scrollbar remains.
- Added a configurable NIFTY 50 update interval in Settings: Off, 1, 5, 10, 15, 30 or 60 minutes.
- NIFTY polling now occurs only while the Market sensor is open (09:00–15:30 Asia/Kolkata).
- Market-status checking remains independent of NIFTY polling so opening/closing state stays responsive even when NIFTY updates are disabled or infrequent.
- Replaced the NIFTY status timestamp with relative text such as “Updated 1 minute ago”; when the market is closed or NIFTY polling is disabled, the status reads “Update stopped”.
- All v0.1.131 Add Mutual Fund behavior is retained unchanged.

# v0.1.131

## Optimize Add Mutual Fund NAV refresh
- Changed the Add Mutual Fund SIP/Lumpsum completion workflow to refresh only the newly added fund's NAV instead of triggering a full portfolio NAV refresh.
- Kept the normal manual NAV Refresh button and automatic scheduler unchanged; they still refresh all funds.
- After the new fund is saved, the app fetches the latest NAV for only that scheme, recalculates the affected portfolio, and updates Home Assistant integration state.
- Preserved SIP execution behavior for the newly added fund through the existing single-fund refresh path.
- Added regression coverage proving Add Mutual Fund does not call `refresh_all()` and instead refreshes only the new holding.

# v0.1.130

## Add Mutual Fund SIP workflow and smoother progress
- Extended the NAV-preview/confirmation workflow to SIP acquisition in Add Mutual Fund.
- Added asynchronous SIP NAV-history preview with progress reporting and explicit confirmation before saving historical SIP transactions.
- Added SIP add background job/status handling and immediate table refresh after the fund is saved.
- Fixed the Add Mutual Fund progress pause during the final full NAV refresh by monitoring the live refresh state instead of blocking the UI job at a fixed progress value.
- Preserved the v0.1.129 lumpsum flow and all unrelated functionality.

# v0.1.129

## Fix Add Mutual Fund lumpsum amount entry
- Removed a duplicate legacy `submitAdd()` handler that overrode the new lumpsum preview/job flow.
- The legacy handler still queried the removed `.ls-nav` input and caused `Cannot read properties of null (reading 'value')` when entering a lumpsum amount.
- The intended v0.1.128 NAV-date + amount-or-units → NAV preview → confirmation → background add flow is now the only active Add Mutual Fund submit handler.
- Added regression coverage to ensure the handler is unique and never references `.ls-nav`.

# v0.1.128

- Refined the Add Mutual Fund → Lumpsum flow only, using v0.1.127 as the baseline.
- Removed the confusing Current units held / Total amount invested inputs from the Lumpsum path; those values are derived from the transaction rows.
- Renamed the Lumpsum date input to NAV date.
- Lumpsum entries now require exactly one of Amount or Units; NAV is fetched and the missing value is calculated before confirmation, matching the Purchase/Sell preview model.
- Added a per-entry NAV lookup progress bar and explicit calculated-value confirmation before recording the fund.
- Added a background add job so the holding is saved first, the table refreshes immediately, and the latest NAV refresh completes before the operation is finalised.
- Added regression coverage for conflicting amount/units, missing NAV confirmation, NAV-derived values, transaction creation, progress-job completion, and the new UI flow.
- No changes to NSE/NIFTY, portfolio, SIP, import, Home Assistant integration, or custom card logic.

# v0.1.127

- Refined the web-app NSE/Market status information block.
- Changed the first-line label from `NIFTY 50` to `Nifty50` and kept the line black.
- When the market is closed, the first line now shows `Market Closed`.
- Removed the separate third `Market: ...` information line.
- The second line now shows the NSE reason; tomorrow's holiday is shown explicitly when applicable.
- Retained the final `Last status check` line.

# v0.1.125

- Replaced the direct NIFTY 50 endpoint with the published Google Sheets CSV proxy backed by the user's GOOGLEFINANCE sheet.
- Kept the existing app/card layout and existing Market sensor; only the NIFTY data source and display formatting changed.
- NIFTY now displays as `NIFTY 50 index(change)` using only index value and daily point change; percentage change is not shown.
- Positive NIFTY change is green and negative change is red in the web UI and custom card.
- Added defensive CSV parsing and regression tests for headers, invalid CSV, positive/negative display, and proxy configuration.

