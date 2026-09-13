Convert the attached CAMS/KFintech Consolidated Account Statement into the COMPACT Mutual Fund Tracker JSON format below.

The goal is to keep the JSON VERY SMALL. Do NOT output one JSON transaction object for every monthly SIP. Instead compress continuous monthly SIPs into sip_blocks.

IMPORTANT RULES:
1. Return the complete JSON only inside ONE fenced Markdown code block labeled `json`. Do not add explanations, commentary, or any text before or after the code block. The code block is the PRIMARY and REQUIRED deliverable. The JSON inside the code block must be valid JSON only, with no Markdown or prose inside it. Use the code block's download/save control to make the JSON downloadable; do not rely on a separate file attachment.
2. Extract investor identity from the consolidated statement cover page. Put one or more investors in a top-level investors array. If the statement has one investor, use one object with ref="investor_1". If names/contact details differ and the statement clearly represents multiple investors, create separate investor objects.
3. Preserve full investor name, all email IDs visible on the statement, phone/mobile, PAN when shown, and postal address. Do not invent missing details.
4. One fund object per scheme_code per investor_ref. If the same scheme has multiple folios, consolidate them into one fund object for that investor and list all folios.
5. Preserve the statement's scheme name, ISIN, folios, closing unit balance, Total Cost Value, valuation date, NAV and market value.
6. For recurring monthly SIP series, use history.sip_blocks. Each block represents ONE SIP per month, for count consecutive months, from start_date to end_date.
7. A sip_block MUST be split whenever the SIP amount changes, charges change materially, SIP schedule day changes, or another condition makes the series non-contiguous.
8. Be VERY CAREFUL about SIP amount changes mid-scheme. For example, if a fund was ₹4,999.75 per month and later became ₹2,499.88, output TWO sip_blocks, not one block with an average amount.
9. If the statement shows a skipped, rejected, cancelled, duplicate, extra or otherwise irregular SIP, do not hide that inside a normal block. Break the block around it and put the irregular transaction in history.transactions.
10. If there are two SIP purchases in the same month, do not represent them as one monthly block. Use exact history.transactions for the exceptional month(s).
11. sip_day is the SCHEDULED SIP day, not necessarily the actual transaction date. Infer it from the recurring pattern in the statement. If a transaction is on the next working day because the scheduled day was a holiday, keep sip_day as the scheduled day and use the actual recurring transaction start/end dates for validation.
12. start_date and end_date in a sip_block are the FIRST and LAST ACTUAL TRANSACTION DATES shown in the statement for that block. count is the number of SIP transactions in that block.
13. The add-on will reconstruct each SIP's actual NAV/allotment date by finding the first available NAV on or after the scheduled SIP date. Therefore, do not fabricate per-transaction NAVs for compressed SIP blocks.
14. For a SIP block, amount is the principal invested in each transaction, excluding stamp duty/charges. charges is the per-transaction charge if it is constant throughout that block. If the charge changes, split the block.
15. For irregular purchases/lumpsums, redemptions, switch-ins, switch-outs, fees, rejected/cancelled items and other non-recurring financial rows, use exact history.transactions rows.
16. For purchases/SIPs, exact transaction amount should be principal; charges go in charges; cashflow is the actual investor cash outflow and is normally -(amount + charges).
17. For redemptions/sells, use positive amount, negative units, and positive cashflow equal to proceeds net of charges/TDS only when explicitly available.
18. Switch-in and switch-out are non-cash portfolio movements: cashflow 0; switch-in positive units; switch-out negative units.
19. Rejected/cancelled transactions should not double-count an investment. Use an exact adjustment/reversal only when needed to reproduce the statement's unit balance.
20. Ignore purely administrative rows such as address updates, nominee updates, KYC updates and similar non-financial events.
21. Put the current/latest SIP instruction in the fund's sip object. If the SIP is no longer active/stopped, set enabled to false but preserve the last known SIP amount and scheduled day when available. Do not reinterpret its historical transactions as lumpsum.
22. Preserve folio numbers at both fund and transaction/block level.
23. The JSON must contain the MFAPI/AMFI numeric scheme_code. Resolve it from fund name/ISIN using reliable public sources if necessary. Do not guess. If it cannot be verified, put the code in unresolved and leave scheme_code empty.
24. closing_balance.cost_value comes from Total Cost Value in the statement. Do not replace it with an arithmetic sum if the statement gives a closing value.
25. closing_balance.units comes from Closing Unit Balance.
26. Do not use today's NAV in place of the statement's valuation NAV.
27. The add-on expands sip_blocks back into individual transactions before calculating XIRR.
28. If the statement is consolidated using an email address and the cover page shows that email, include it in investors[].emails. If multiple statement emails are being merged for the same person, list all of them in that one investor's emails array when the source provides them.

29. EXCLUDE CLOSED/EMPTY FUNDS: If closing_balance.units is 0 AND closing_balance.cost_value is 0 (or the fund has no current holding/value), DO NOT include that fund in the funds array. Delete it from the JSON entirely. It is not an active portfolio holding even if the statement contains old historical transactions.
30. A STOPPED SIP IS STILL A SIP HISTORY: If a fund had a SIP in the past but the investor stopped it, keep all historical SIP transactions as type="sip" or in sip_blocks. Do NOT convert a stopped SIP into a lumpsum merely because sip.enabled is false. Use sip.enabled=false to mean no future automatic SIP; sip.amount and sip.day should preserve the last known SIP instruction when available.
31. LUMPSUM MEANS A GENUINELY NON-RECURRING PURCHASE: Only classify a transaction as lumpsum when the statement indicates a one-off purchase/investment. Rows whose description contains SIP, Systematic Investment, ISIP, instalment/installment, or similar recurring-investment wording must remain type="sip", even when the SIP is now inactive.
32. If a stopped SIP has no transaction in the previous completed month, still include its historical SIP blocks/transactions and set sip.enabled=false. The add-on will display it as an inactive SIP and will ask the user to confirm Active vs Stopped only when the status is ambiguous.
33. REDEMPTIONS MUST BE PRESERVED EXACTLY: every actual redemption/sell transaction shown in the statement must be included in history.transactions with its original CAMS transaction date, positive amount/proceeds, negative units, NAV when shown, charges/TDS when explicitly available, cashflow when available, and folio. Never omit a redemption because SIP history is compressed or because the closing balance changed. Never infer a redemption from a difference in closing units/cost value.
34. SOURCE STATEMENT GENERATION DATE: if the CAMS statement explicitly provides the date on which the statement/report was generated or downloaded, put that date in source.statement_generated_date. Do not substitute statement_to when the actual generation date is available. If the source does not provide a reliable generation date, omit statement_generated_date rather than guessing.

COMPACT JSON FORMAT:
{
  "format_version": 2,
  "source": {"type":"CAMS_CONSOLIDATED_STATEMENT","statement_from":"YYYY-MM-DD","statement_to":"YYYY-MM-DD","statement_generated_date":"YYYY-MM-DD"},
  "investors": [
    {"ref":"investor_1","name":"Full name","emails":["email@example.com"],"phone":"+91...","pan":"...","address":{"line1":"","line2":"","city":"","state":"","postal_code":"","country":"India"}}
  ],
  "unresolved": [],
  "funds": [
    {
      "investor_ref": "investor_1",
      "scheme_code": "",
      "scheme_name": "Canonical scheme name",
      "isin": "INF...",
      "folios": ["folio1"],
      "sip": {"enabled": true, "amount": 2500, "day": 20},
      "closing_balance": {"valuation_date":"2026-08-12","units":1000.123,"cost_value":125000,"nav":125.0,"market_value":130000},
      "history": {
        "sip_blocks": [
          {"amount":4999.75,"charges":0.25,"count":34,"start_date":"2021-03-10","end_date":"2023-12-11","sip_day":10,"folio":"folio1"},
          {"amount":2499.88,"charges":0.12,"count":30,"start_date":"2024-01-10","end_date":"2026-06-10","sip_day":10,"folio":"folio1"}
        ],
        "transactions": [
          {"date":"2026-02-05","type":"lumpsum","amount":50000,"units":350.123,"nav":142.80,"charges":0,"cashflow":-50000,"folio":"folio1","note":"Lumpsum purchase"}
        ]
      }
    }
  ]
}

Before returning, verify:
- JSON is valid.
- No scheme is duplicated.
- Every continuous SIP amount period is represented by its own block.
- SIP amount changes are split into separate blocks.
- SIP day changes are split into separate blocks.
- Charge/stamp-duty changes that materially affect cashflow are split into separate blocks.
- Skipped/rejected/cancelled/duplicate monthly transactions are represented by block boundaries and/or exact transactions.
- count matches the number of SIP transactions in the block.
- start_date and end_date are the actual first/last transaction dates in the statement for that block.
- If scheme_code is provided, it is only a hint; the importer will verify/correct it using AMFI + ISIN/name.
- closing units/cost value match the statement.

20. IMPORTANT: A missing SIP transaction in the previous completed month is not proof of cancellation. The add-on will ask the user to confirm Active vs Stopped during import. Do not fabricate evidence of activity.
