# OPT SIGNALS — Targeted Engineering Gap Queue

Every real, evidence-backed gap surviving the completed provider audit, ranked
by live-trading impact first. This is not a feature backlog — every row here
traces to a specific confirmed parser/assembler/execution gap found during
replay validation (see `docs/opt_signals_audit_ledger.json` and
`docs/opt_signals_capability_matrix.md`). No speculative work is included.

Provider-enablement decisions (e.g. "turn on channel 167") are operational/
business calls, not engineering gaps, and are intentionally excluded — see
the engineering dashboard's Operational Summary instead.

| # | Gap | Live Impact | Shared-Component Risk | Complexity | Effort |
|---|---|---|---|---|---|
| 1 | Shared parser false-positive hardening | **High** | **High** | Medium | Medium |
| 2 | Entry-timing extraction unimplemented | Medium-High | Low | Medium | Medium-High |
| 3 | MessageEdited/cancellation live-fire confidence | High (if wrong) | Low | Low | Low |
| 4 | Channel 372 dedicated adapter | Low | Low-Medium | Medium | Medium |
| 5 | 216 martingale-continuation attribution | Low-Medium | Medium-High | Medium-High | Medium |
| 6 | Interactive-bot button/callback capture (passive logging only) | Medium | Low | Low-Medium | Low-Medium |
| 7 | Reply-thread persistence for replay verification | Low | Low | Low | Low |

## 1. Shared parser false-positive hardening

`parsers/signal_parser.py`'s `parse_signal` scans the entire message text for
a direction keyword unanchored, once a labeled asset field (`Pair:`,
`Currency pair:`, etc.) is found. Confirmed real failure: channel 372's
session-opening narrative ("...look for **sell** opportunities...") was
parsed as a complete standalone BUY/SELL signal purely from an incidental
English word in prose, not a trade instruction.

This is a **shared-component** bug — `parse_signal` is used by all 16
providers, including the two currently DEMO/OBSERVATION_ONLY live paths (162,
207). No live failure has occurred yet (neither 162 nor 207's real message
formats combine a labeled field with narrative prose), but the failure mode
is general, not provider-specific, so it is ranked highest.

**Why not fixed already:** any change to `parse_signal`'s direction-matching
requires re-running smoke revalidation against all 14 VERIFIED providers
(per the ledger's smart-revalidation policy) plus the full regression suite,
and a same-day fix could not be produced with enough confidence not to
introduce new false negatives on an already-hardened, live-adjacent path.

**Candidate fix direction:** anchor the unlabeled/trailing BUY/SELL search to
short, structured lines (mirroring how every real verified provider actually
formats direction — a standalone word or short field, never mid-sentence)
rather than the whole message body. Needs a corpus-wide test against
`tests/fixtures/provider_corpus.py` before merging.

## 2. Entry-timing extraction unimplemented

No field in `parse_signal`'s output represents a stated future entry time
(e.g. Martin Trader's `Entry: 09:00`). AXIM acts on message receipt, not on
a provider's stated entry timestamp. Confirmed by reading the parser and its
own fixture (`mt_signal_complete` never asserts an entry-time field).

**Live impact if unaddressed:** any provider that states a scheduled entry
time distinct from message-arrival time (163 Martin Trader is the only
confirmed real example) could have its trades executed at the wrong moment
if ever enabled. Currently NOT_ASSIGNED, so no active risk today.

**Complexity note:** this needs a product decision, not just a parsing
change — should AXIM delay execution until the stated time, reject signals
whose entry time has already passed, or something else? Scope the design
before implementing.

## 3. MessageEdited/cancellation live-fire confidence

`SignalAssembler.handle_edit` and `telegram_listener.edit_handler` are
built and unit-tested, but zero real edit or cancellation events have
occurred across any of the 16 providers' captured history — the path is
unexercised by real evidence. The consequence of a real, silent failure here
(a corrected/cancelled signal executing anyway) is high, but the remaining
work is verification, not construction: watch for the first organic edit
event, or proactively build additional synthetic tests using each verified
provider's real message vocabulary to raise confidence pre-emptively.

## 4. Channel 372 dedicated adapter

Real format requires session-scoped carried-asset state (an announcement
that persists for the whole trading session, not the current ~300s
per-signal timeout), recognition of this provider's specific
announcement/result-echo/pair-change vocabulary, and exclusion of
"`PAIR ✅/❌`" result-echoes from being misread as new announcements. Fully
scoped in the ledger's channel 372 entry. Zero live risk today — channel is
disabled and never onboarded.

## 5. 216 (Pocket Option Signals) martingale-continuation attribution

~190 real "More up/down X minute" continuation messages never repeat their
own asset and arrive after the original sequence already resolved. Correctly
rejected today (no valid context to attach to) — the core signal is never
lost, only an optional secondary reinforcement. Fixing this requires the
assembler to remember the last **completed** asset per channel for a short
grace window, a genuinely new mechanism with real false-merge risk if built
carelessly. Deliberately not attempted per "do not add broad guessing rules
simply to increase coverage."

## 6. Interactive-bot button/callback capture (passive logging only)

AXIM's passive message capture does not currently log inline-keyboard
button labels or `callback_data` at all, so there is no way to even inspect
what buttons a bot presents without live manual interaction. Adding
passive logging of `message.buttons`/`callback_data` (capture only, no
auto-clicking) would unblock future evaluation of any interactive bot,
including 16 (Go+, Fund archived — low priority) and 370 (Trading Booster
Bot — additionally gated behind a real-money new-account-and-deposit
authorization decision that is the user's to make, not an engineering
task). Scoped here as capture infrastructure only; a button-driving adapter
is out of scope without explicit direction.

## 7. Reply-thread persistence for replay verification

`channel_messages` does not persist `reply_to_message_id`, so historical
replay can never confirm the reply-thread correlation path
(`pending_by_reply_to`) even though the **live** listener already wires it
correctly from Telethon (`event.message.reply_to.reply_to_msg_id`). Adding
the column would let future replay audits actually verify this path instead
of leaving it permanently unconfirmable. Zero live-execution impact — purely
an audit-confidence gap.
