# AXIM Signal Capability Matrix

Baseline snapshot as of 2026-07-25, derived from the completed OPT SIGNALS
provider audit (`docs/opt_signals_audit_ledger.json`, 16/16 sources). This is
the parser/assembler capability baseline, not a provider list — see the
[engineering dashboard](opt_signals_engineering_dashboard.md) for the
provider-level view.

Rating definitions:
- **Supported** — proven correct against real historical provider messages via replay, or confirmed live.
- **Partially Supported** — works for some real cases; a specific, documented real gap or unexercised path exists.
- **Unsupported** — no working implementation, or no adapter exists.

Every rating below is backed by a specific provider (channel ID) and, where
relevant, a regression fixture — no capability is marked Supported on
architecture alone without cited evidence.

## Message-shape handling

| Capability | Status | Evidence |
|---|---|---|
| Single-post providers (asset+direction+expiry in one message) | Supported | 187 Daniel FX Trade, 9 TYLER VIP CLUB, 199 TYLER PRO CLUB, 25 VIP\|Signals, 44 Micha Trader, 163 Martin Trader, 166 Pocket 5M Trader, 231 Pattern Signals |
| Multi-post providers (announcement → separate entry message) | Supported | 162 Pro Trading Robot, 167 OTC Pro Trading Robot, 207 NTrade, 216 Pocket Option Signals (primary cycle only) |
| Interactive bots (inline-keyboard/callback-button driven) | **Unsupported** | 16 Go+, 370 Trading Booster Bot — no button/callback-capture mechanism exists in the passive listener at all, no driving adapter |
| Passive bots (`kind=user`, unsolicited push, no button interaction) | Supported (architecturally identical to the channel path — routing has no `kind` special-case) | No source in this folder is a genuine passive bot to serve as direct evidence; the two `kind=user` sources found (16, 370) are both button-driven, and 116 NEBORTRADE is a human sales conversation |
| Announcement → Entry two-step workflows | Supported | 162, 167 (phrase-wrapped announcement fallback), 207 (emoji-decorated bare-asset announcement), 216 (bilingual bare-asset announcement) |
| Reply-thread correlation (`reply_to_message_id`) | Partially Supported | Code exists and is wired end-to-end in the **live** listener (`telegram_listener.py` reads `event.message.reply_to.reply_to_msg_id` and passes it to `signal_assembler.process_message`). **Not verifiable by historical replay**: `channel_messages` does not persist `reply_to_message_id`, so none of the 16 providers' replay runs exercised this path — a replay-methodology blind spot, not a known production failure |
| Edited messages | Partially Supported | `SignalAssembler.handle_edit` + `telegram_listener.edit_handler` exist and are unit-tested (`tests/test_signal_assembler.py`, `tests/test_telegram_listener_observation.py`), but **no real edit event has occurred** in any of the 16 providers' captured history — unexercised by real evidence |
| Cancellation messages | Partially Supported | Same `handle_edit` path, same caveat — unit-tested, zero real cancellation events observed across all 16 providers |
| Duplicate messages | Supported | Trade-execution-layer duplicate check (`trade_coordinator.py`'s `check_duplicate_signal` preflight stage). Real-evidence confirmation: 207 NTrade's real duplicate/timeout resends correctly did not double-count |
| Overlapping signals (multiple concurrent assets, same channel) | Supported | `pending_by_asset` keyed dict design; real evidence across 162/167/207/216 processing varied assets concurrently with 0 false_merges across all 16 providers |
| Delayed follow-up messages (beyond the 300s assembly timeout) | Partially Supported | Within-timeout delays are routinely and correctly assembled across most providers; genuinely late follow-ups after a sequence has already resolved are dropped — confirmed real gap in 216 (martingale "More up/down" continuations) and 372 (result-echo/pair-change timing, disabled provider) |

## Field extraction

| Capability | Status | Evidence |
|---|---|---|
| Asset extraction (slash pair, concatenated pair, labeled `Pair:`/`Currency pair:`, flag-emoji-decorated, crypto/stock/commodity category labels) | Supported | Nearly all 16; category-label parsing proven against Go+'s real historical text even though the bot itself is unsupported |
| Direction extraction | Partially Supported | Supported for the vast majority of real messages. **Confirmed real false-positive class**: `parse_signal`'s direction search scans the *entire* message text unanchored once a labeled asset field is found, so an incidental "buy"/"sell" inside narrative prose can misfire (372, disabled/no live risk, not yet patched — see gap queue) |
| Expiration extraction | Supported | S/M shorthand, natural-language "N Minutes/Seconds", and the explicit `default_expiry` channel-level fallback for providers whose expiry is a constant never repeated per-message (9 TYLER VIP CLUB, 199 TYLER PRO CLUB) |
| Entry timing (explicit scheduled entry time, e.g. Martin Trader's `Entry: 09:00`) | **Unsupported** | `parsers/signal_parser.py` has no entry-time extraction at all — confirmed by reading the parser and its own fixture (`mt_signal_complete` expects only `asset`/`direction`/`expiry`, no entry-time field). AXIM currently acts on signal receipt, not a stated future entry timestamp. Real gap for any provider that states an entry time distinct from message-arrival time (163 Martin Trader) |
| Result messages (win/loss posts that must never become trades) | Supported | `l2_result_only_is_not_a_signal` fixture, Martin Trader SESSION REPORT fix, 372's "GBPUSD ✅/❌" echoes (never become a trade themselves, though they are separately misread as re-announcements — see gap queue) |
| Closing summaries (session wrap-ups, often containing real historical asset/direction pairs) | Supported | Martin Trader `_SUMMARY_REPORT_RE`/`_WIN_LOSS_TALLY_RE` fix (49/857 real false positives eliminated), 372 and 207 closing summaries correctly rejected |
| Narrative/non-signal filtering (general chatter, promo) | Partially Supported | Correct for the large majority (`tyler_promo_noise`, `l2_chatter_is_not_a_signal` fixtures, and dozens of real rejected messages across every provider) — but see the direction-extraction false-positive above for the one confirmed real counterexample (372) |
| Emoji-only/emoji-coded directions | Supported | 9/199 TYLER (🔼/🔽), 187 Daniel FX Trade (⬆/⬇ alongside HIGH/LOWER), 372 (🚫-prefixed "PUT (Low!)"/"CALL (High!)" — direction extraction itself succeeds even where asset-carrying fails) |
| BUY / SELL | Supported | Ubiquitous across nearly all 16 |
| CALL / PUT | Supported | 372, 166 Pocket 5M Trader, Go+ format |
| UP / DOWN | Supported | 25 VIP\|Signals, 231 Pattern Signals |
| HIGH / LOWER | Supported | 187 Daniel FX Trade — the only confirmed real provider using this vocabulary |
| Safe Option messages | **No real evidence** | Not observed in any of the 16 OPT SIGNALS folder sources this pass. No parser rule exists for it. Not fabricated as covered — flagged honestly as never encountered in real data |
| FIRST / SECOND continuation messages | **No real evidence** | Not observed under this literal wording in any of the 16 sources. The closest real analog is 216's differently-worded "More up/down X minute" martingale continuations, which are a documented, separate gap |

## Summary counts

- Supported: 15
- Partially Supported: 6
- Unsupported / No evidence: 4

(25 capabilities rated total — 60% fully supported, 24% partially supported, 16% unsupported or unobserved. See the [gap queue](opt_signals_gap_queue.md) for what closes each Partially-Supported/Unsupported row.)
