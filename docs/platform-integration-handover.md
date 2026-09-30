# Platform-managed scoreboards: implementation handover

Date: 2026-09-30. Status: implementation plan, not implemented platform functionality.
Canonical home: `KaralabeZeller/y-scores`, `docs/platform-integration-handover.md`.
Platform checkout: `D:\Dev\Y-Sports`; device checkout: `D:\Dev\y-scores`.

## Objective and accepted requirements

Extend the working scoreboard so its local admin page can enable platform control.
The local administrator sets a human-readable name and a unique device ID. The name
can change; the ID is immutable after setup. The Pi registers/reports these to
Y-Sports. A signed-in management operator searches by name or ID, pairs the device
to their user account, sees it in My devices, and assigns a match or court. Court
assignment automatically follows upcoming/live matches without further Pi setup.
Preserve local match/court/schedule/logo/blank selection as an explicit fallback.

This plan supersedes `Y-Sports/docs/scoreboard-devices-implementation-plan.md` where
it differs, especially its Pi 3/4 target, two-panel geometry, competition-owned
pairing, device source location, and mandatory Wi-Fi setup scope. Do not implement
that earlier plan in parallel.

## Current working baseline (inspect again before editing)

- Standalone private repo: https://github.com/KaralabeZeller/y-scores . Baseline
  commit `ab8465a`, release `v0.1.0`; existing Python tests and GitHub CI passed at
  handover. No device registry, account pairing, or cloud control exists yet.
- Raspberry Pi 5, three Waveshare P2.5 64x64 panels on separate bonnet ports;
  logical display 192x64. Piomatter 1.0.0, Active3BGR, order `2,1,0`, rotations
  `180,0,0`. Subsequent uncommitted work adds configurable daisy chaining in `matrix_output.py`. The Pi test configuration uses chain order `2,1,0` and rotations `180,180,180` (corrected from the physical display photo); verify current `/etc/y-scores.env` and physical wiring before further deployment.
- Local admin: `http://pantracker.local:8080/`. SSH host `pantracker.local`, user
  `zeller`. Obtain credentials from the operator; do not put them in this file.
- Runtime: `y-scores.service`, unprivileged user `yscores`, GPIO group access;
  `/opt/y-scores/current`, versioned `/opt/y-scores/releases`, and `previous` link.
  State in `/var/lib/y-scores`; environment in `/etc/y-scores.env`. Installer,
  migration and rollback have been exercised on the Pi. Preserve all state.
- Old `/home/zeller/scoreboard-test` and disabled `scoreboard-device.service` are
  historical backups. Never run both matrix processes. Original monorepo
  `scoreboard-device/` is a prototype, not the development target.
- Existing app reads public API at `https://y-sports.xyz/api-next`. Match snapshots
  use the public v2 media type, ~200 ms polling cadence over a reused connection;
  rendering runs separately. Local court routing currently uses a 10-second cache.
- Clock projection uses server anchors and monotonic time. Penalties use effective
  playing time, floor whole seconds, and yellow RGB on both sides. Team name,
  score, and timeout markers use primary team color. Physical color mismatch has
  not been conclusively resolved; identical RGB values are not proof of calibration.
- Freeze/mark live data stale after 15 seconds without fresh snapshots. Pause
  latency was reduced; zero network delay is not guaranteed. Preserve timer fixes.
- Example previously tested match: `7ccdc25d-c50e-49a1-be46-cba1cf9d482d`; do not
  hardcode it or rely on its current score/status. Fresh devices boot to logo.
- Enclosure intent: three detachable modules, Pi behind centre, separate supply,
  upright/tilted desk feet. No CAD/STL exists; enclosure is outside this change.

## Proposed product decisions

These defaults make the request implementable; they are design proposals, not
claims about existing APIs. Proceed with them unless the user changes direction.

### Identity and lifecycle

1. Local PIN-authenticated setup offers a name (1-64 trimmed characters) and UUID
   field, prefilled with a generated UUIDv4. The administrator may edit/paste a
   valid UUID before selecting **Save identity**. Explain that it cannot change.
2. Save identity atomically in a separate versioned `device-identity.json`, mode
   0600, before registration. Reject later ID changes in the local API as well as
   the UI. Renaming does not change ID, credentials, pairing, or assignments.
3. Registration enforces global uniqueness. A duplicate ID without valid device
   credentials is a conflict, never an upsert or replacement. Surface recovery
   instructions; do not silently generate a different ID after it was locked.
4. Keep name locally authoritative in v1: rename in local admin, persist a metadata
   revision, sync it to the platform. Management displays the reported name and
   ID; no competing cloud rename writer. Names need not be unique.
5. Ownership is one user account per device in v1. Use a stable internal account
   reference or issuer+subject, not mutable email or display name. Sharing and
   organization fleets can follow later without changing public device IDs.
6. Updating, rolling back, unpairing, credential rotation and normal reinstall
   preserve identity. Cloning a provisioned SD card must not create a second unit
   with the same ID/secret; ship images without device state. Loss of credentials
   needs explicit authenticated recovery, not registration by ID alone.

### Discover and pair

- Platform control is an opt-in local switch. Registration and all communication
  are outbound HTTPS; no public inbound Pi port or cloud access to LAN addresses.
- **Allow pairing** opens a 10-minute discoverability window on an unowned device.
  Authenticated operators can search exact UUID or name text (minimum 3 characters,
  bounded results/rate limits). Only devices with an active window are discoverable.
  Expose name, ID suffix/full ID as needed and capability summary, not owner data,
  IP addresses, network details or credentials. My devices search is owner-scoped.
- Operator selects the matching device and requests pairing. Both management and
  local admin show a short-lived matching verification code. Local admin shows
  requester identity and requires explicit **Approve pairing** or **Reject**.
  A known device ID/name or verification code alone never grants ownership.
- Backend atomically accepts at most one approved claimant; pending requests expire
  or can be cancelled. Rate-limit request creation; do not allow replacement spam.
  Retried approval returns the same outcome; reboot/reconnect resumes session state.
- After approval, the device appears in the account's My devices. No Keycloak user
  password/token is stored on the Pi. Its credential authenticates only this device.
- Owner can unpair. This atomically clears assignment, closes pending requests,
  increments control revision and returns device to unowned/logo when connected.
  Keep its registration credential valid only for limited unowned-device operations.
  A separate **Revoke device credential** blocks all device access, including active
  streams; recovering that state requires an explicit operator-assisted procedure.
- An offline Pi cannot receive unpair immediately. Management shows it pending;
  assignment data expires locally after the freshness timeout. Re-pairing requires
  a new local pairing window and approval. Remote ownership transfer is out of v1.

### Who controls the screen

Separate `controlSource` (LOCAL or PLATFORM) from display mode. Do not overload the
current `mode` field to imply ownership. Local intensity, wiring, rotation and
network settings stay local; platform v1 controls only displayed content.

| Situation | Display behavior |
| --- | --- |
| LOCAL | Existing manual match/court/schedule/logo/blank settings apply |
| PLATFORM, unpaired/unassigned | Logo/status; never inherit an old match |
| PLATFORM, assigned | Apply latest valid platform snapshot |
| Local takeover while paired | Retain cloud assignment, render local selection, report LOCAL_OVERRIDE |
| Return to platform | Fetch fresh authoritative state before replacing local screen |
| Connection lost | Mark stale/freeze after 15 seconds; do not silently enter local mode |
| Restart offline in platform mode | Offline/logo status; do not extrapolate a cached live clock |

Local takeover remains available through the PIN-protected admin page. Management
shows desired assignment separately from reported output and pending/offline state.
Reconnection must not cancel an explicit local takeover. Returning to platform is
an explicit local action in v1. Unpair/revoke clears cached authorized match data.

## Assignment and automatic court selection

Assignments: LOGO, BLANK, MATCH, COURT, SCHEDULE; null means unassigned. Multiple
devices may follow one match/court. One device has one assignment in v1. Temporary
match overrides layered over court assignments are deferred; ordinary reassignment
is sufficient for the first release.

- MATCH: a match ID, validated against the operator's existing operational access.
- COURT/SCHEDULE: typed scope `{kind: TOURNAMENT | LEAGUE_EVENT, id}`, court ID
  (optional only for whole-scope schedule), timezone and explicit date/time window.
  Tournament and league court entities are distinct; validate court membership.
- Ownership alone does not grant access to match data. Require both device ownership
  and the existing match/court operational permissions on every mutation. Recheck
  assignments when access is removed, on snapshot delivery and during reconciliation.
  Invalid authority removes protected content rather than falling back to public API.
- Backend resolves court selection; platform-mode Pi must not independently select
  from schedules. Keep existing local resolver solely for LOCAL mode.
- Keep the current eligible active match, including pause, interval and timeout.
  If multiple matches are active, retain the current one and flag ambiguity; with
  no previous selection show a conflict screen. Never silently pick by list order.
- On completion hold final result for 60 seconds, unless another match becomes
  active. Persist completion/hold anchor so restart does not reset the hold.
- Otherwise choose earliest eligible unstarted fixture, stable ID tie-break.
  Delayed unstarted matches remain eligible; scheduled time does not start a game.
  Exclude cancelled/deleted matches. No match means court name/No upcoming match.
- Respect assignment window in UTC with event timezone presentation. Keep an active
  overrun until completion; court move/deletion or explicit reassignment re-resolves.
- React to match state, schedule, court, access changes and timer expiry; periodically
  reconcile to recover missed events. Use services internally, never self-HTTP calls.

## Backend implementation (Y-Sports/backend-next)

Read applicable AGENTS.md, including OpenAPI and migration guides, before editing.
Suggested new `com.ysports.platform.scoreboards` module with api/application/domain/
 infrastructure packages; confirm Modulith boundaries first. Expose narrow adapters
 from competition modules for projection, permission checks and schedule queries.
Do not let device credentials issue match commands.

Inspect/reuse:
- `identity/application/{CurrentUser,AccessPolicyService,ResourceAccessService,AccessCapability}.java`
- `bootstrap/config/SecurityConfig.java` and `SecurityConfigTest`
- `competitions/runtime/api/PublicMatchOperationV2Controller.java`
- `competitions/runtime/application/{PublicMatchOperationQueryService,PublicMatchOperationV2FeedService}.java`
- `competitions/runtime/domain/CourtEntity.java` and
  `competitions/league/domain/LeagueCourtEntity.java`.

Append-only Flyway schema proposal:

| Record | Required content |
| --- | --- |
| scoreboard_device | immutable UUID PK, reported name + metadata revision, credential hash/version, lifecycle, nullable owner subject, capability/protocol/software versions, timestamps, optimistic version |
| scoreboard_pairing | device FK, requester subject, window/request IDs, code digest, expiry, state, decision timestamps; transactional single-owner constraint |
| scoreboard_assignment | device unique FK, mode, typed scope/court or match, window/timezone, settings JSON, revision, actor/time; exclusive-mode constraints |
| scoreboard_device_status | last seen, boot ID, control source, applied revision, actual match/mode, bounded error code; throttle writes |
| audit | registration/claim/unpair/revoke/assignment/local takeover events using existing audit conventions |

Device credential: random high-entropy opaque secret; store only a hash on server,
keep secret in protected Pi state, send in Authorization headers. For retry-safe
bootstrap the Pi persists UUID + random registration credential before first POST;
server stores its hash. Retrying an identical UUID+credential can return existing
registration; a different credential cannot claim/overwrite it. Rate-limit anonymous
registration and clean up abandoned unowned records without allowing ID hijacking.
No credentials in URL, logs, diagnostics, frontend bundles or repository.

Human APIs use normal Keycloak JWT and owner/resource checks. Device endpoints use
an explicitly isolated device authentication path; registration is the only anonymous
bootstrap endpoint. Classify matchers deliberately, test token-type separation and
revocation. Pairing decisions require device authentication plus local PIN approval.

## Proposed API contract (all under /api-next)

Finalize schemas in new module-scoped `openapi/scoreboards.yaml`; reference management
operations from `management.yaml`. Ensure build/generator configurations include it.
These routes do not exist yet; names below form a coherent starting contract.

| Method/path | Auth and purpose |
| --- | --- |
| POST /scoreboard-device/registrations | Bootstrap; UUID, name, credential, metadata revision, capabilities; retry-safe |
| PUT /scoreboard-device/metadata | Device; sync name/capabilities with metadata revision |
| POST /scoreboard-device/pairing-window | Device; open/close bounded discovery window |
| GET /scoreboard-device/pairing-requests | Device; pending request, verified requester display, comparison code |
| POST /scoreboard-device/pairing-requests/{requestId}/decision | Device; approve/reject, explicit local action |
| GET /scoreboard-devices/discover?q=... | Human; limited discovery only during active windows |
| POST /scoreboard-devices/{id}/pairing-requests | Human; request ownership, idempotency key |
| GET /scoreboard-devices/{id}/pairing-requests/{requestId} | Requester; progress/result only |
| DELETE /scoreboard-devices/{id}/pairing-requests/{requestId} | Requester; cancel pending request |
| GET /scoreboard-devices | Human; own devices, paginated/filterable |
| GET /scoreboard-devices/{id} | Owner; desired/reported state, last seen and warnings |
| PUT /scoreboard-devices/{id}/assignment | Owner + target authority; body includes expectedRevision |
| POST /scoreboard-devices/{id}/unpair | Owner; expectedRevision, clear ownership and assignment |
| POST /scoreboard-devices/{id}/revoke | Owner; revoke credential and assignment |
| GET /scoreboard-device/snapshot | Device; own full resolved display/control snapshot |
| GET /scoreboard-device/feed | Device; SSE full snapshots, reconnect cursor |
| POST /scoreboard-device/heartbeat | Device; bootId, appliedRevision, actual match/mode, control source, bounded errors |

Return 409 for immutable-ID collision, ownership race or stale expectedRevision;
401 for invalid credentials, 403/404 per existing resource concealment conventions,
410 for expired pairing, 422 for invalid assignment, 429 with retry hints for limits.
Use stable machine-readable error codes and documented idempotency semantics.
Pairing polling returns pending status without credentials. Credentials are never
returned to a human browser. Token rotation/recovery contract is a later explicit
addition; do not improvise insecure reset-by-ID endpoints.

Snapshot v1 should include `protocolVersion`, `serverGeneration`, `streamSequence`,
`serverTime`, `deviceId`, `ownershipState`, `assignmentRevision`, assignment, resolved
screen state, selected match ID/revision, team names/colors, score, period/rules,
clock running/anchors, effective-time anchors, active suspension number/expiry,
timeouts used, authoritative active-timeout anchors when supported, upcoming match
and hold expiry. Reuse current v2 timing semantics; do not infer timeout duration.
Use named schemas/discriminated screen payloads, UTC ISO timestamps, integer ms,
explicit nullable fields, and shared JSON fixtures for Java/Python/TypeScript.

Assignment revision is independent of match revision. An ACK means validated and
applied/rendered, not merely downloaded. Full snapshot on connect/reconnect and
server-generation change prevents lower-revision new matches being discarded.
Protocol-major incompatibility leaves last safe/logo display and reports an error.

Transport: outbound authenticated SSE for platform snapshots; full snapshot fallback
polling with bounded retries if feed unavailable. Do not simply reuse the current
one-second public SSE polling loop: retain current timer responsiveness by publishing
committed changes promptly and reconciling periodically. Keep rendering independent.
Use heartbeat every 15 seconds, device offline after 45; live data stale at 15 seconds.
Transport keepalive alone must not validate stale match data: snapshots need a fresh
successful projection timestamp. Reject old-generation/out-of-order messages, estimate
network timing conservatively, project with monotonic time, freeze promptly on a
received pause. Measure latency; do not promise elimination of network delay.
Review nginx dev/test/production SSE buffering, timeouts, proxy headers and log
redaction. No browser-to-Pi connection is required for management control.

## Y-Scores implementation

Existing files: `device_admin.py` (Device/router/HTTP/PIN auth), `device_model.py`
(config/local court resolver), `device_screens.py` (screens), `live_state.py`
(time projection), `live_scoreboard.py` (feed/renderer/driver), `admin.html`, `admin.js`.

1. Add versioned identity/credential storage and one-shot identity API; keep secret
   fields out of `/api/status` and preview. Add migration without touching existing
   display config or PIN. Current validator rejects unknown fields, so keep cloud
   identity and state separate from legacy `device-config.json` for rollback safety.
2. Add platform client/background worker with registration, metadata sync, pairing,
   snapshot/SSE/reconnect and heartbeat. Separate identity transport from render loop.
   Use configurable trusted API origin; do not attach credentials to redirects or
   an arbitrary URL supplied by a cloud snapshot. Store credentials per environment.
3. Add a control coordinator selecting LOCAL vs PLATFORM, with atomic assignment
   application and generation cancellation. A delayed response from a previous match
   must never replace the newly selected match. Do not run two hardware writers.
4. Extend local admin: Identity (editable name, locked ID/copy), platform switch,
   registration/owner/connection status, pairing window and approval, applied vs
   desired assignment, local takeover/return. Explain immutable ID before saving.
   Preserve existing PIN/CSRF protections and local settings/preview experience.
5. Render platform data using current projection and layout; respect team colors,
   unified penalties, timeout counts, intervals and overflow. Add unpaired/offline/
   conflict/upcoming/final screen fixtures at actual 192x64 resolution.
6. Preserve identity, credentials, ownership cache and controlSource through installs.
   Document that rollback to v0.1.0 cannot enforce cloud mode: prefer a compatible
   release, or explicitly restore LOCAL/logo; never claim old code understands new
   state. No automatic remote code execution/update endpoint in this feature.

## Management frontend implementation

New `frontend/apps/management/src/features/scoreboards/` vertical slice: route entry,
API adapter over generated client, query keys/hooks, device list/detail, pairing flow,
assignment forms. Keep new logic out of legacy page/service/hook buckets.

- Add **My devices** to `app/router/routeManifest.tsx` with lazy routes and direct
  detail links; align `config/pageAccess.ts`, backend access capabilities and help.
- List: name + immutable ID, online/last seen, platform/local status, desired target,
  actual screen/match, pending revision/error; empty state explains local setup.
- **Pair device**: name/UUID search, handle duplicate names, select/request, display
  verification code and waiting-for-local-approval state, cancellation/expiry/retry.
  A successful request is not yet a successful pairing.
- Detail: choose match or typed tournament/league-event court, schedule/logo/blank,
  date/window/timezone where needed, apply/unassign, unpair/revoke. Match selectors
  only offer permitted targets; backend remains authoritative. Handle 409 conflicts
  by refreshing and asking the operator to reapply deliberately.
- Match Center and court/schedule workspace gain **Assign display** using devices
  owned by the operator; both invoke the same mutation and update shared queries.
- Show local override and offline/pending state without claiming remote success.
  Ownership and account filters are not competition filters.
- Use existing theme/components/TanStack Query/auth integration. Add English and
  Spanish labels and help; test direct URL access and unauthorized states.

## Delivery sequence and acceptance gates

1. **Contracts and fixtures:** confirm stable account binding and target operational
   permissions, map tournament and league schedules/statuses, pin protocol schemas,
   identity/pairing state machine, migration strategy and cross-language fixtures.
   Record unresolved mapping details before implementing routes; do not guess court IDs.
2. **Identity and pairing vertical slice:** backend tables/auth/register/discovery/
   approval + Pi local identity/platform settings + My devices UI. Gate: operator
   finds Pi by name/ID, local approval claims it exactly once; rename syncs while ID
   remains fixed across reboot/update. A second user cannot take it over.
3. **Fixed match delivery:** assignment API + authenticated snapshot/feed + Pi
   coordinator + management picker. Gate: live score, pause/resume, penalties and
   primary colors match authoritative state; desired/applied status is visible.
4. **Court and display modes:** deterministic backend resolver, upcoming/final/idle,
   court/schedule UI integration, logo/blank. Gate: an entire fixture transition runs
   unattended; overlapping/cancelled/moved/delayed fixtures behave as specified.
5. **Recovery and release:** local takeover, disconnect/reboot, unpair/revoke,
   permission removal, version compatibility, deployment docs and device acceptance.
   Deploy backend/frontend via existing GitHub CI/image/production workflow; deploy
   Pi separately through versioned y-scores release installer. Pilot one device.

Verification checklist:
- Backend: duplicate bootstrap/retry, pairing expiry/race/cancel/approval, device vs
  user token isolation, owner isolation, ID immutability, target access and later
  removal, assignment conflicts, resolver edge cases and revocation of open streams.
- Device: state migration/reboot, immutable ID API, retry-safe registration, pending
  pairing restore, old snapshot rejection, clock/penalty boundaries, paused clock,
  offline freeze, explicit local override, no-network startup, protected secrets.
- Frontend: discover/pair all states, ambiguous names, empty/list/detail, assignment
  success/error/conflict, role checks, owner scope, desired/reported mismatch, EN/ES.
- Contract: regenerate with `corepack pnpm generate:next` from
  `frontend/packages/api-client`; never hand-edit generated code. Type-check all
  affected consumers and verify representative Python protocol fixture parsing.
- Backend: focused tests then `backend-next/mvnw.cmd clean verify` (JDK 25), and
  validate Flyway changes with PostgreSQL/dev stack. Do not reverse migrations.
- Management from `frontend`: `corepack pnpm --filter @y-sports/management` with
  `type-check`, `build`, `test`, `validate:architecture`, `validate:quality`,
  `validate:bundle`, `validate:help-content`. Browser through existing nginx URL.
- Y-Scores: `python -m unittest discover -p 'test_*.py'`, `node --check admin.js`,
  run `bash -n` separately for `scripts/install.sh` and `scripts/rollback.sh`.
- Hardware: verify all three tiles/orientations, both penalties and team colors,
  match switch, pause latency, Wi-Fi outage, reboot and compatible rollback.
  Log measured results, not assumed pass claims. Preserve existing low intensity.

## Scope boundaries and next-agent starting point

Do not bundle daisy-chain rewiring, Pi 3/4 drivers, CAD, Wi-Fi provisioning/AP mode,
phone offline score editing, multi-owner sharing or automatic software updates.
Mobile browser local control remains available; hotspot connectivity can supply
ordinary internet. These are separate follow-up projects.

Start by reading both repository AGENTS.md files and this handover, inspecting git
status and current branch, then implement phase 1 and the pairing vertical slice.
The Y-Sports checkout contains unrelated Android work: preserve it. Do not modify
production or the running Pi merely to validate this plan. No credentials, current
PINs, private keys, or runtime state belong in commits. Update this handover with
contract decisions, completed gates and remaining work as implementation proceeds.
