# Cumulative match clock handover for Y Sports

The requested behaviour is a match clock that continues across periods on the
Pi scoreboard and every Y-Sports Match Center surface. In a match with two
30-minute periods, the first period ends at 30:00, the break stays at 30:00, and
the second period starts at 30:00 and runs towards 60:00. Selecting the next
period must not start play automatically. Breaks and timeouts must not add match
time.

This is follow-up work. The current Y-Scores change implements only the approved
display layout and leaves all clock semantics unchanged. No backend or
Management runtime changes accompany this handover.

## Display work completed in Y Scores

The centre panel shows the configured period markers at the top, the existing
large match clock at row 14, and timeout seconds at row 45 in the requesting
team's colour. Black and other dark countdown digits receive a white outline.
There is no TO text, arrow, team name, progress bar or decorative animation.
Markers fit 1–10 regular periods, with the active period white and others dim.
Overtime is labelled OT1/OT2; larger regular-period configurations use a compact
period number. Manual mode supports up to ten configured periods.

The renderer receives `period_count` and `timeout_team` in addition to the
existing clock fields. For live matches, these come from
`configuration.regularPeriodCount` and the effective timeout event's `teamSide`.
Manual mode uses its stored settings and active timeout owner. Countdown and
owner come from the same event after revision and correction filtering; missing
ownership uses yellow rather than inventing a requesting team. These are internal
Pi view fields, not new server API fields.

## Backend assessment

The current contract contains enough information to derive a cumulative display
for matches whose completed periods have their configured duration. No database
migration or change to stored elapsed fields is inherently necessary for that
display-only case. Frontend changes are necessary because both display values
and operator inputs currently use period-relative time.

Prefer an explicit, shared cumulative-display contract or projection, backed by
tests, rather than changing the meaning of existing fields. An additive backend
field such as `periodDisplayOffsetMs` or `matchDisplayElapsedAnchorMs` can make
multiple clients consistent, but those names are proposals, not existing API
fields. If the team chooses to derive the offset in clients, standardize and test
the same policy in every consumer, including the Pi. Backend changes become
necessary if the selected policy needs historical period offsets that the
snapshot cannot supply, or if commands are to accept cumulative input directly.

Keep these existing concepts separate:

| Value | Current purpose | Cumulative-clock treatment |
| --- | --- | --- |
| `periodElapsedAnchorMs` | Elapsed time inside the current period | Preserve its meaning and period-local bounds |
| `effectiveElapsedAnchorMs` | Effective match time used by suspensions | Do not substitute it for the displayed clock without auditing correction semantics |
| `clockAnchorAt` and `serverTime` | Running-clock projection | Sample once and clamp at the current period duration |
| `periodDeadlineAt` | Automatic period ending | Continue to calculate from remaining time in the current period |
| Event `periodElapsedMs` plus `period` | Event position within a period | Keep stored values local; convert labels and operator input |
| Suspension `expiresAtEffectiveElapsedMs` | Expiry on the effective-time axis | Preserve across breaks, display changes and period transitions |

The current Java reducer sets `periodElapsedMs` to zero on
`PERIOD_INITIALIZED`, while retaining effective time. Keep that domain behaviour;
add the cumulative display above it. Changing the reducer to store 30:00 as the
second period's local start would break period limits and deadlines.

## Proposed display and input rules

For one-based period `p`, regular-period count `R`, regular duration `D`, and
overtime duration `O`, all durations in milliseconds:

```text
periodOffset(p) = min(p - 1, R) * D + max(0, p - 1 - R) * O
matchDisplayElapsed = periodOffset(p) + clampedPeriodElapsed
eventDisplayElapsed = periodOffset(event.period) + event.periodElapsedMs
periodInputElapsed = operatorMatchTime - periodOffset(selectedPeriod)
```

Examples for two 30-minute periods followed by 5-minute overtime periods:

| Situation | Period-local value | Display |
| --- | --- | --- |
| End of first period and ensuing break | P1 30:00 | 30:00 |
| Second period initialized, still paused | P2 00:00 | 30:00 |
| Twelve minutes into second period | P2 12:00 | 42:00 |
| End of second period | P2 30:00 | 60:00 |
| First overtime initialized, still paused | OT1 00:00 | 60:00 |
| Second overtime initialized | OT2 00:00 | 65:00 |

Use the snapshot's configured durations, not hardcoded handball lengths. For a
four-by-ten-minute match the period starts are 00:00, 10:00, 20:00 and 30:00.
Do not format via a time-of-day API that wraps at 60 minutes.

Clock dialogs must show cumulative values and convert them back to local values
before existing commands are sent. In period two, entering 42:00 means a
12:00 period-local correction. Validate input against the selected period's
cumulative range, and retain the explicit period selection at shared boundaries
such as 30:00. Apply the same conversion to editing/backdating goals, cards,
timeouts, suspensions and replacement events wherever event time is editable.

Continue to use local time for period progress bars, final-five-minute timeout
rules and deadline calculations. Continue to use effective time for suspension
remaining time. Do not feed cumulative display values into those rules.

### Decisions to settle before implementation

- **Early period completion and duration edits:** the formula above assumes full
  configured preceding periods. If a period can end early, decide whether the next
  displayed start is the scheduled boundary or the actual previous display value.
  If durations can change during play, decide whether earlier offsets are frozen.
  Actual historical offsets may require server projection/contract changes.
- **Clock corrections and penalties:** Management currently changes effective
  elapsed time by the period correction delta in `sendCommand`; the Pi's local
  manual corrections deliberately preserve served penalty time. Define the
  intended policy before consolidating these behaviours. Do not silently change
  either policy as a side effect of display conversion.
- **Countdown mode:** the Pi also supports counting down within a period. The
  requested continuing clock is an elapsed count-up display; keep countdown
  semantics unchanged unless a separate product decision explicitly changes it.
- **Compatibility:** retain the current semantics of existing snapshot/event
  fields. If additive fields are introduced, document fallbacks for older clients
  and regenerate OpenAPI clients through the repository scripts.

## Y Sports implementation map

Paths below are relative to the locally inspected `D:/Dev/Y-Sports` checkout.
The Y-Scores guide refers to the server as y-sports-core; confirm the destination
branch and active repository layout before implementation. The active server
code inspected here is under `backend-next/`.

### Backend and contracts

Under `backend-next/src/main/java/com/ysports/platform/competitions/runtime/`:

- `domain/operations/MatchOperationReducer.java`: period initialization and clock
  corrections; preserve local-period state.
- `application/MatchOperationCommandService.java`: materialized elapsed time,
  next-period commands, corrections, deadlines and event timestamps.
- `api/MatchOperationApiDtos.java` and `api/PublicMatchOperationApiDtos.java`:
  authenticated/public snapshots and event contracts. Keep scoreboard-device
  responses and streamed deltas consistent with any additive clock fields.
- `application/MatchProtocolService.java`: protocol projections and any exported
  match-time labels that depend on elapsed time.

Contract source: `backend-next/src/main/resources/openapi/tournaments.yaml`.
Reducer tests: `backend-next/src/test/java/com/ysports/platform/competitions/runtime/domain/operations/MatchOperationReducerTest.java`.

### Management displays and commands

Under `frontend/apps/management/src/`:

- `hooks/useMatchClockAnchor.ts`: currently distinguishes period and effective
  clocks; introduce an explicit cumulative display without changing those meanings.
- `services/MatchOperationsService.ts`: current adapter maps `elapsedMs` to
  `periodElapsedAnchorMs`; command and event-edit payloads remain period-local.
  Avoid globally remapping this overloaded field without auditing its consumers.
- `pages/MatchCenter/MatchCenterPage.tsx` and its `components/MatchCenterScoreboard.tsx`,
  `MatchCenterDialogs.tsx`, `MatchCenterEventsPanel.tsx`: display, correction
  dialogs, event editing and timeline labels.
- `features/match-center/ui/MatchCenterConsole.tsx`, `MatchDeskPanels.tsx`,
  `MatchDeskTimeline.tsx`, `EventTimeline.tsx`, `TimelineMarker.tsx`, and
  `matchDeskTypes.ts`: console clock, dialogs/panels, event times, grouped labels,
  tooltips and accessibility labels.
- `features/match-center/model/eventTimeline.ts` and `handballRules.ts`: distinguish
  cumulative labels from local-period sorting, geometry and timeout eligibility.
- `pages/MatchCenter/offlineCapture.ts`: keep captured event payloads correctly
  period-local and verify reconnect/replay under the new displayed clock.
- `components/MatchCenter/MatchCenterModal.tsx`: audit whether this older/demo
  surface is reachable, and align it if retained.
- `services/PublicMatchOverlayService.ts`: overlay clock projection; audit all
  consumers rather than fixing only the operator console.

Audit tournament and friendly/ad-hoc routes, desktop and mobile variants,
fullscreen scoreboards, overlays, event histories, export/report labels and all
clock/event-time input controls. Keep English and Spanish labels and validation
messages in sync. Search for consumers of `elapsedMs`, `periodElapsedMs`,
`displayElapsedMs`, `formatTime`, `formatDeskTime` and `timelineTime` to finish the
inventory; the listed paths are starting points, not a claim that every route
has been traced.

### Shared and public consumers

- `frontend/packages/api-client/src/publicMatchOperationsV2.ts`: legacy adapters
  currently expose the local-period value as both `elapsedMs` and `periodElapsedMs`.
- `frontend/apps/public/src/hooks/usePublicMatchCenter.ts`: currently derives a
  period-local displayed clock and a distinct effective clock.
- `frontend/apps/public/src/pages/Scores/FriendlyMatchCenter.tsx`: visible match
  clock and event labels; audit other users of the same hook/service.
- In Y-Scores: `live_state.py`, `manual_match.py`, and the Manual clock-entry
  handling in `admin.js`/`admin.html`. Ship cumulative behaviour in a separate
  coordinated change; do not change the current layout release's timing rules.

## Acceptance checks

1. At a full period end, the display holds its boundary during the break. Next
   period initialization leaves it unchanged and paused; Start advances from it.
2. The Pi, every Management Match Center view, public centre, overlay and protocol
   labels agree for the same revision and sampled instant, including overtime.
3. A second-period 42:00 clock edit and backdated event become local 12:00 inputs.
   Invalid cumulative ranges are rejected without changing state. Undo/replace
   and replay produce the same values after reload.
4. Test two, four and ten configured periods; differing regular/overtime
   durations; displays over 99 minutes; late snapshots; reconnect; offline
   capture; stale-state freezing; browser background/resume; match switching.
5. A penalty crossing a period boundary retains the correct remainder. Breaks,
   timeouts and display offsets do not serve penalty time. Test the separately
   agreed correction policy, final-five-minute rules, and automatic deadlines.
6. Verify count-up and existing countdown manual modes, restart recovery, retained
   device settings and rollback with older clients. Use fake clocks for boundary
   tests rather than tests that depend on wall-clock timing.

Run the repository's applicable backend verification and frontend lint,
type-check, build and tests; validate both producer and consumers for contract
changes. For Y-Scores run `python -m unittest discover -p 'test_*.py'`,
`node --check admin.js`, and `bash -n scripts/install.sh scripts/rollback.sh`.
Roll out compatible contracts first if changed, then shared frontend consumers,
then the Pi cumulative-clock change. Preserve `/etc/y-scores.env`, device settings,
PINs and manual state; no runtime deployment is part of this handover.
