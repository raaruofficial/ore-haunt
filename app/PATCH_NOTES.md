# HAUNT Patch Notes

Running log of every audit finding and its fix. Newest first.

## 2026-10-07 — HUD-date UTC fix (changelog entry)
- The player HUD was building its date from the round's estimated `date` field with mixed timezones ("Oct 6 · 00:00 UTC" for an Oct 7 08:42 UTC round). Now uses the first deploy's on-chain `block_time`, explicit UTC: "Oct 7 · 08:42 UTC". Picker card, HUD, and detail all sync from the same timestamp.

## 2026-10-07 — ROI-claim correction
- Correction to the "no ROI text remains" note: the `lt_dep`/`lt_ore` conditional at `build_app.py:477` is still present — it's a dead branch (bundle never emits those fields), not deleted code. Noted accurately here; the branch renders nothing.

## 2026-10-07 — Full audit patch pass (all 6 agents' findings)

### Data & pipeline (backend)
- **#1 Concurrent writers** — Added `flock` lockfiles to `watcher.py` and `indexer.py`; a second process now exits instead of appending. Repaired 726 stale old-schema records in `data/deploys_2026-10-07.jsonl` (migrated `amount_lamports` per-square → total + `amount_per_square_lamports`); recovered ~15.6 SOL of undercounted volume. 0 stale remain.
- **#2 Zero-value events** — `make_bundles.py` now builds all aggregates (leaders, replays, counts) from *funded* deploys only (`amount_lamports > 0` and squares non-empty). 18,141 zero-events excluded from counts; raw JSONL untouched. Replay "514 deploys" → ~230 honest funded deploys.
- **#3 True round dating** — `days.json` and replay dates now derived from `round_id` (anchor: round 431031 @ 2026-10-07T06:57:54Z, ~77.5s/round verified across replays) instead of `collected_at` fetch time. Grid now honestly shows Oct 4 (568) · Oct 5 (427) · Oct 6 (685) · Oct 7 (405).
- **#4 Watcher 1000-sig pagination** — polls now page with `before` until <1000 or 10k cap; backlog can no longer be silently skipped.
- **Watcher livelock** — batch commit check moved out of the `continue` path; always runs.
- **Watcher skip-and-continue** — one flaky `getTransaction` no longer aborts the run; failed sigs recorded to `state/retry_sigs.json` and retried next poll.
- **Indexer gap retry** — transient parse faults (`bad_length_*`, `id_mismatch_*`) get one retry pass; only `account_closed` is treated as permanent.
- **#5 Auto-rebuild pipeline** — new `ore-bundle-builder` cron (every 30m) runs `build_if_fresh.sh`: rebuilds `bundles/` + `haunt.html` only when watermarks advanced. App can no longer go stale silently.
- **#12 Dynamic counts** — `bundles/meta.json` now ships `{built_at, events_total, events_funded, rounds}` for the app to interpolate (ABOUT hardcode removal is in the app pass).
- Dropped meaningless `days.json.miners` sum (double-counted).

### App (complete — verified in built haunt.html, 2026-10-07 ~09:20Z)
All items below confirmed present via 29 automated checks + jsdom-free smoke harness (autoplay+loop, tab pause/resume, mute sync, scrub-to-end reveal, leader-after-scrub, day grid/scrubbers, profile, goReplays in-place, data honesty, dead-code scan). File now 344KB (was ~618KB).
- Engine-aware `show()`: pause rAF + suspend AudioContext on tab leave, resume on return.
- Mute button inits from `Audio_.muted`.
- Adaptive canvas tile labels (contrast) + tile indices.
- Round explorer section cleanup (copy fix).
- Drop `rds` from fingerprints (~132KB).
- Dossier wallet word-break (360px overflow).
- ABOUT uses `D.rounds.count`; "Deploy events" → funded count from `D.meta.events_funded`.
- `goReplays()` no longer yanks when already on the replay tab.
- "150 miners" → "top 150 by volume".
- Scrub-to-end runs silent `doReveal()`; leader recomputed on scrub; scrubber doesn't fight finger.
- Whale threshold on per-square SOL + 300ms throttle.
- Explicit ▶ play buttons on detail cards (no whole-card tap).
- Chevrons on day cards.
- Staleness indicator ("data X min old", amber >1h) from `D.meta.built_at`.
- "ROI %" branches removed — fresh `leaders.json` carries no `roi`/`lt_dep`/`lt_ore` fields, so the dead ROI copy (which could never render) was deleted rather than renamed; no ROI text remains in the app. Intel note no longer claims "lifetime stats".
- HUD miner count now reads `R.agg.m` (same look, honest data; was showing "—").
- Dead code removed: `replay()`, `.dpill`, `.rngbtn.on`, `.pulse`, `#replayGrid .tile`, `.tile.hot`, `.tag.auto`.
- Fingerprints only for 150 leaderboard wallets.
- `setTension()` hardened (guards `droneFilter` null) — found by verification harness.
- Engine source and baked copy byte-identical (`replay_engine.js` == embedded).

### Product changes (user-directed, this session)
- 3-tab restructure: Intel / Replay / About (Rounds merged into Replay; replay list removed).
- Replay autoplays on open and loops (4s aftermath → restart).
- Date navigator: 3×3 grid (last 9 days) + date scrubber + replay scrubber + gold indicator HUD + round detail cards (tap-to-expand removed; detail always visible, defaults to today).
- No dropdowns / hidden menus anywhere.

## Audit source
Full per-agent reports: `AUDIT_FINDINGS.md` (6 agents: navigation, replay UX, intel/perf, processes, pipeline, authenticator).

## 2026-10-07 — Design QA polish pass
- Indicator HUD (`#rhud`) wraps at ≤380px (was overflow risk at 360px).
- Scrubbers: 44px+ touch targets (padding + touch-action).
- Mute button: 44px min-height.
- Dead palette tokens deployed: violet = audited/verified accents (ABOUT trust banner), red = staleness escalation (>6h).
- ABOUT now opens with a "✓ AUDITED CHAIN DATA" trust banner (was buried).
- Label declutter: "browse by date · last 9 days" merged; "rounds" micro-label dropped from day cards.
- Intro title: letterspaced, "auto-playing" chrome removed.
- Reveal particles stagger over 0.6s (fountain, not flash).
- Particle pool: ring-cursor steal (was random).
- Engine comment corrected (no phantom "tension" phase).
- **Bug caught by verification:** fix #8 broke initial autoplay (`goReplays` saw the default-visible tab and never opened a player). Fixed: in-place only when `ENG` exists.

## 2026-10-07 — Thermal heat color-coding
- Tiles now render on a 5-stop thermal scale (cold deep-blue → blue → green → amber → red-hot) instead of the old dark→green ramp. The leading tile literally glows hottest; label contrast threshold moved to match (dark text on amber/red).

## 2026-10-07 — Round explorer 6×5 grid
- Replaced the 60-row round list with a 6×5 grid of the 30 most recent rounds. Each cell: winning tile (large, amber), round #, SOL total. Tap → full heat map detail.

## 2026-10-07 — ReplayConsole module (novel replay picker)
- Replaced the blind replay scrubber with a visual filmstrip: 8 mini-cards per day, each showing time, winner tile, deploy count, and a SOL-weight bar. Tap to pick; HUD + detail follow. Self-contained `ReplayConsole` module (render/pick).

## 2026-10-07 — Stale aftermath removed
- The previous loop's aftermath panel stayed visible while the next haunting played (two results showing at once). Now cleared via `onReset` on every loop restart.

## 2026-10-07 — Replay picker above player
- Moved the replay filmstrip to the very top of the Replay tab (above the player). Tap a card → loads directly into the player. Label shows the selected day; updates when the 3×3 grid changes days. Removed the now-dead ReplayConsole module.

## 2026-10-07 — Round explorer own tab
- Round explorer moved out of the Replay tab into its own ROUNDS tab (4 tabs: Intel / Replay / Rounds / About). Round detail back button targets the explorer.

## 2026-10-07 — Replay picker restore (bug)
- The dead-code cleanup regex for ReplayConsole over-matched and deleted `renderTopPicker` too. Restored. Lesson: verify deletions with a targeted check, not just "no errors".

## 2026-10-07 — 7-day grid + range dropdown + picker correspondence
- Date navigator: 7-day grid (7 columns) with dropdown for 7 days / 2 / 3 / 4 weeks. Day cards compacted for the narrow grid.
- Top picker now highlights the currently-playing replay (amber) and shows the selected day; refreshes on openReplay.

## 2026-10-07 — Picker scroll-to-player
- Tapping a replay card now scrolls the player into view (was updating off-screen). Initial autoplay doesn't scroll.

## 2026-10-07 — Date navigator removed
- Removed the entire "browse by date" section (7-day grid, dropdown, scrubber, day detail). Top picker now shows all replays directly. Dead functions cleaned.

## 2026-10-07 — Widget PDA hardened (all holes plugged)
- Fixed byte-order bug in isOnEd25519Curve (was big-endian, Ed25519 is little-endian). Verified: test wallet's miner PDA is at nonce 254 — old code would have returned invalid nonce-255.
- b58decode now validates: rejects empty input, invalid chars, >32 byte decodes (was silently truncating/misdecoding).
- getAccountData now handles HTTP errors, RPC errors, and bad base64 gracefully (returns null instead of throwing).

## 2026-10-07 — Rounds tab: three lenses
- Replaced chronological 6×5 grid with curated sections over last 100 rounds (~2h): biggest pots (by SOL), most crowded (by miners), tile streaks (wins per tile). Tap any round for heat map.

## 2026-10-07 — StreakModule
- Tile streaks rebuilt as a self-contained StreakModule with custom UI: ranked bar leaderboard (tile badge, gradient win bar, count) instead of generic grid cells. Top 8 tiles by wins in last 100 rounds.

## 2026-10-07 — StreakModule clickable
- Streak rows now tap through to the tile's latest replayable win (round # shown). Cross-tab navigation via playRound → Replay tab + scroll to player. Tiles without replay data show "no replay" (non-tappable).

## 2026-10-07 — Replay dedicated page (t-view)
- Tapping a picker card now opens the replay on its own page (t-view) instead of snap-scrolling. Back button returns to picker. Prev/next buttons step through replays in chronological order. Picker cards show weekday + time (e.g., "Wed 09:18").

## 2026-10-07 — Timezone converter
- Header timezone selector (PST default; EST, UTC, London, Berlin, Tokyo, Singapore, Sydney). All times app-wide (banner, picker, lenses, player HUD, clock) render in selected TZ and update on change.

## 2026-10-07 — Timezone chips
- Replaced TZ dropdown with a horizontal chip strip (PST default). One tap to switch, no open/close state, no clipping. All times app-wide update live, including Rounds tab lenses.

## 2026-10-07 — Replay navigation fixes
- wasReplay now includes t-view (audio/engine pause when leaving the player page).
- Resume logic includes t-view.
- stepReplay handles currentRound not in 7-day order (no more dead prev/next).
- Verified: init → t-view autoplay, back → picker, tab → autoplay latest, pick → t-view, leave → pause, step fwd/back works.

## 2026-10-07 — Split picker: last hour + today's top
- Replay picker now has two sections: "last hour" (chronological) and "today's top" (top 6 by SOL). Day boundary respects selected timezone. Prev/next follows the same order.

## 2026-10-07 — Picker tabs: Last Hour / Last Day
- Replay picker has proper tabs. Last Hour: all matches from past 60 min (newest first). Last Day: top 12 by SOL from past 24h. Prev/next follows the active tab. No more 7-day flat list.

## 2026-10-07 — Dates/times isolated
- Picker cards, lens cells, and player HUD now show date and time as separate, visually distinct elements (date dim, time bold). No more "Wed 09:18" jumbles.

## 2026-10-07 — Date/time isolation redesign (3 subagents)
- Picker cards: date in its own raised box (WED / OCT 7), time as separate bold element below. 100px cards, press feedback, aria labels.
- Lens cells: date as detached pill above cell, time as separate pill below. Thermal tint preserved.
- Player HUD: two rows — date in amber-ringed pill (row 1), start time in neutral pill + live green clock + miners + mute (row 2). Fits 360px.

## 2026-10-07 — Biggest pots color scheme
- Biggest pots: vibrant gold gradient (intensity scales with pot size). Most crowded: cool blue-violet gradient. Replaces the bleak thermal tint.

## 2026-10-07 — Mini replay strip (last 3)
- t-view now shows the last 3 replays as small animated canvases above the main player. Tap one to load it. Main canvas capped at 52vh so everything fits on one screen. Minis are silent (no audio chaos).

## 2026-10-07 — Replay carousel under the tile
- Mini strip (last 3) moved below the main player. Minis now actually animate (driven by main rAF loop). Pause/resume with tab navigation. Silent audio.

## 2026-10-07 — Daypart bubble tabs
- Picker now has bubble tabs: Last Hour, Morning, Afternoon, Evening, Night (with counts). Dayparts respect selected timezone. Each shows top by SOL. Prev/next follows active bubble.

## 2026-10-08 — TEKKADAN BANKAI pass (full fix/audit loop + stealth sweep + reckoning)
- Briefs: 4 teams, 3 CATASTROPHIC / 32 MAJOR / 77 MINOR. Cross-audited round-robin, iterated to clean.
- CATASTROPHIC (all fixed, SHADOW re-verified): chat XSS via message sinks, via tip-thanks/reply attribution, via reply quotes — esc() trust boundary + whoRaw/who dual-path.
- MAJOR kills: show()×openReplay recursion breaker (_replayFailed); tip identity corruption; watchdog rAF leak redone with inverted `queued` flag (first attempt was provably incomplete); chat DOM cap 300; tile-index q∈0–24 validation; mini failure placeholder; setTZ pill-only refresh; deceptive "● live" → "● demo" + simulated-preview label; simulated-tip disclosure; INFO estimates copy corrected.
- PROCESS FAILURE (caught by auditors, institutional lesson): two patch batches silently dropped (asserted before write, build reported success anyway); separate read/write cycles clobbered engine fixes twice. Lesson: per-fix post-write grep verification mandatory; assert-then-batch-write is banned.
- HELD for user: (1) strategy codes 0–3 meaning for manual/auto labels; (2) infinite auto-loop + winner jingle each cycle — battery/UX sign-off; (3) Seed Vault signMessage challenge at sign-in — STANDING LAUNCH-BLOCKER, escalates to CATASTROPHIC if handle→device binding missing at first real funds flow; (4) haunt-page-monitor DISABLED per user direction.
- Open debt: engine duplication (replay_engine.js vs baked copy — byte-identical today, consolidate later); dead @keyframes pulse; centralized chat-trim helper.

## 2026-10-12 — UI/systems/pages overhaul (designer pass)
- Full design system: token-based CSS (type scale xs–xxl, spacing, radius, gold/green/violet/red palette, --ease, shadows), one card style, .btn/.btn-ghost with press states, .chip pill system, .row list rows with rank badges + chevrons, .stat-tile grid, .tele telemetry tiles, skeleton shimmer, page-enter transitions, empty states.
- Navigation consolidated: t-round folded into t-explorer as inline detail (roundDetail/closeRoundDetail, skeleton paint); 7 sections, 5 tabs. Back buttons unified.
- Replay viewer = cinematic hero: .player card, broadcast HUD (gold date pill, live green clock, miners, mute), taller canvas (engine resize 420 → min(w*1.12,500)), aftermath as animated event overlay on the stage, "ROUND TELEMETRY · LIVE" panel separate from animation, film-reel mini strip.
- Miners: scannable leaderboard (rank badges top1–3, SOL, chevron); dossiers as player cards (gradient identicon, verified-layout + rank tags, window-stat grid, manual/automated split bar, thermal tile fingerprint via fpColor).
- Stats: lens cards with headers + count badges, breathing room, thermal heatmaps; hot-tile streak bars with kept randomness disclaimer.
- Chat: Discord-style name+timestamp, kept tip-right-column, honesty labels intact, gold Send button.
- Info: methodology as doc sections (audited data, winner rule plain-language + code, estimates honesty, limits).
- BUG FIXES found by visual QA: (1) goReplays boot order — _booted autoplay branch was dead (t-replay visible by default), app opened on empty picker; fixed. (2) --amber token missing from new CSS (19 dangling refs incl. Send button); aliased to --gold. (3) Engine label dirty-check vs clearRect: tile values visible one frame then blank — labels now draw every frame (+dark stroke for legibility). (4) Stale seed data emptied replayOrder windows → dead prev/next + empty carousel; added newest-first fallbacks in replayOrder + renderTopPicker.
- HELD decisions preserved: "Automated" strategy labels, infinite auto-loop + winner jingle, no auth UI, ● demo / simulated-preview / SIMULATED honesty labels.
- Engine/host-loop touches (all marked DESIGN-OVERHAUL, intent of every bankai fix preserved): resize taller frame; label draw fix; player/aftermath markup restyle (same IDs); goReplays boot order; replayOrder/picker stale-data fallbacks.
- Verified: 4 script blocks node --check clean; engine byte-parity vs replay_engine.js holds; all bankai markers present; zero pageerrors at 360×740 and 412×915; interaction suite (autoplay, wrap nav, detail/dossier back, chat+tip+reply, tz switch) all pass. Screenshots in overhaul/shots/.

## 2026-10-12 — UI/systems/pages overhaul (design team: designer → 2 auditors → checker)
- New token-based design system (type/spacing/radius, gold accent, one card/button/chip/row language, press states, skeleton shimmer, page-enter transitions, empty states). 7 sections, 5 tabs; t-round folded into t-explorer as inline detail.
- Replay viewer: cinematic player card, broadcast HUD, aftermath as animated overlay, telemetry visually separate, film-reel minis, wrapping prev/next, newest-first fallbacks for stale seed data.
- Miners: scannable leaderboard (rank badges, chevrons); dossiers as player cards (gradient ident, stat tiles, thermal fingerprint with uniform-spread honesty note).
- Stats: lens cards with breathing room, thermal heatmaps, hot tiles as gradient bars (single "dimmed rows have no replay" legend).
- Chat: Discord-style, tip right, honesty labels intact. Info: sectioned methodology doc.
- Bugs fixed during overhaul: dead boot autoplay (goReplays reorder), missing --amber token (invisible Send), tile labels invisible after 1 frame (_lblCache dirty-check vs clearRect — removed, draw every frame + stroke), dead prev/next + empty carousel on stale seed data.
- Design-then-audit: 2 auditors PASS, checker CONFIRMED both, verdict SHIP-WITH-POLISH. Polish applied: page transitions, fingerprint uniformity note, legend dedup, dead @keyframes pulse + dead _lblCache removed. Mini-chat pill occlusion noted but kept (dismissible, pre-existing pattern).
- Engine: byte-parity vs replay_engine.js holds; all 11 bankai markers present; all HELD decisions intact.

## 2026-10-12 — Strategic cut + additions (user-directed)
- REMOVED: mini-chat popup pill (HTML/CSS/JS/toggles all gone). REMOVED: entire simulated chat system (t-chat section, CHAT nav tab, ChatModule, dummy identities, bot replies) and simulated tipping (tip buttons/forms/confirmations) — 266 lines + 14 touchpoints stripped, zero references remain.
- StatShare.share() repointed from chat-posting to native share sheet (navigator.share → clipboard + toast fallback).
- ADDED: Profitability lens on STATS (avg pot/round, avg cost per miner per round, avg winning-tile share of pot — honest cost metrics, no invented ROI since bundle has no payout data).
- ADDED: Miner watchlist (⭐ toggle per leaderboard row + "Watching" filter chip; session-only, no persistence promised).
- Verified: headless Chromium render, no JS errors, profitability + watchlist present in DOM, node --check clean.

## 2026-10-12 — Purposeful viewer/picker loop (user-directed)
- "Browse all hauntings →" button under the film reel on the viewer — closes the loop viewer → full picker → viewer.
- Picker cards now show a "▶ WATCHING" badge on the replay that's currently playing (state-aware, not just the .on border).

## 2026-10-12 — Miner leaderboard tab overhaul (designer pass)
- Rows now signal strategy at a glance: per-row strategy tag derived from fingerprint — 🐋 Whale (top 10% SOL, gold), 🎯 Sniper (>=50% on one tile, violet), 🕸 Spreader (<=15% max tile share, green), ⚖ Balanced (plain). Plus SOL/deploy efficiency under the SOL figure. 2-line rows, no clutter.
- Sort chip row: SOL (default) / Deploys / Efficiency / Snipers (most concentrated) / Spreaders (most even) — instant re-sort; podium top-3 treatment follows the active sort.
- Dossier: new "Scout's take" card — plain-language lines derived only from fingerprint + counts (e.g. "Snipes tile 23 with 60% of their SOL."), whale/manual-auto/activity lines; new SOL/deploy stat tile. No invented stats.
- Watchlist ⭐ toggle + Watching filter kept working exactly as before, restyled into the new row layout (star kept at 44px, chevron dropped since rows are tappable).
- Verified: headless Chromium at 360×740 + 412×915, zero JS errors; screenshots reviewed (default, efficiency sort, sniper sort, watching filter, dossier top + fingerprint).

## 2026-10-12 — Monetization tiers + feature build (user-approved model)
- TIERS: Free $0 / Day pass $1/24h / Monthly $5/mo / Lifetime $50. Entitlements module with ?tier=free preview; default = founder preview (all unlocked, honest note in INFO).
- Free limits: top-25 leaderboard, 1 replay, top-25 dossiers. Paid: full 150, all 8 replays, simulator, whale watch, tax CSV.
- NEW: wallet lookup on MINERS (paste address → dossier or honest not-found). NEW: LAB tab with strategy simulator (tile picker, SOL/round, backtest vs 200 rounds — hit rate + cost only, no invented payouts). NEW: whale watch lens (most concentrated rounds). NEW: tax CSV export in dossier (aggregates + per-tile, honest disclaimer). StatShare → native share sheet.
- Leaderboard overhaul (designer → 2 auditors PASS): strategy tags (whale/sniper/spreader/balanced), 5 sort modes, top-3 podium, Scout's-take dossier narratives (verified against fingerprint data), global SOL rank badges, 2-decimal SOL, Watching chip first.
- Audit fixes: missing-fingerprint "—" tag, global rank badges.
- INFO: Plans comparison table + tier chips.

## 2026-10-12 — Product cuts + return-loop features (user: "everything but 1")
- CUT: Hot tiles lens + hottest-tile share stat (implied patterns in randomness; contradicted methodology).
- CUT: session-only watchlist (star toggles, Watching filter chip, _watch state) — returns with backend persistence + alerts as one real feature.
- NEW: Fresh intel lens (STATS) — last 20 rounds vs prior window: pot trend %, whale-pressure delta, heating/cooling tiles. Descriptive only, randomness disclaimer.
- NEW: shareable miner cards — canvas-generated dossier image (tag, stats, top tile, fingerprint heatmap) via native share / download fallback. "Share miner card" button in every dossier.
- HELD: watched-miner alerts need live data + backend (not faked). Live data still off per user.

## 2026-10-12 — Premium batch + FAQ
- NEW (premium): Similar miners on dossiers (fingerprint cosine similarity, top 5).
- NEW (premium): Miner vs miner compare — t-compare view, two wallet inputs, side-by-side cards.
- NEW (premium): Simulator A/B test — edit strategy A and B, run head-to-head, winner callout.
- NEW: FAQ accordion in INFO (10 questions, plain language, honest limits).
- Tier table updated with new premium rows.

## 2026-10-12 — Top-5 premium build (ranked by monetary value)
- NEW (premium, LAB): Shadow-a-whale — pick a top-10 miner, load their top-5 tiles as simulator picks.
- NEW (premium, STATS): Whale consensus lens — combined top-10 fingerprint heatmap + top-5 tiles by whale SOL.
- NEW (premium, STATS): Cost-per-win lens — SOL deployed per win, ranked by tile (honest cost metric).
- NEW (premium, LAB): Monte Carlo — 100 random strategies, distribution histogram, your-picks percentile.
- NEW (premium, STATS): Weekly intel report — auto-generated window summary with share button.
- Fixed: unclosed template literal in weekly report share button (caught by node --check).
- Tier table updated (14 premium features).

## 2026-10-12 — ANOMALY ultra tier ($7/day, 100 slots/day)
- NEW tier above Lifetime: Anomaly $7/day, 100 slots/day (enforced at launch). Lifetime does NOT include it.
- NEW ANOMALY tab (6th tab): anomaly radar — z-score detection across pot size, miner count, concentration, per-tile funding (stricter 4σ bar for tile floods: 5,000 observations).
- Radar UI: severity counts, severity filter chips (all/extreme/strong/notable), anomaly feed cards with z-scores, tap-through to round detail.
- Exclusive locked screen for non-members (gold, "members only", slot scarcity, ?tier=anomaly preview hint).
- INFO: Anomaly tier card. Engine verified in node vs real bundle (200 rounds → 86 anomalies (V3-corpus build of Oct 7; the live V4 window shows 28); calibration tuned).

## 2026-10-12 — Extensive feature sweep (user: cut filler / build out half-baked)
- CUT: Whale watch lens — superseded by the Anomaly radar's z-score concentration detection. Removed from STATS, tier table, and premium feature keys. Zero dangling references.
- CUT (earlier): Hot tiles lens + hottest-tile share stat; session watchlist.
- BUILD OUT: "Profitability" renamed to "Cost of play" (honest) + expanded with cheapest/priciest rounds and avg miners/round.
- BUILD OUT: Miner compare — side-by-side fingerprint heatmaps + cosine similarity score banner.

## 2026-10-12 — LAB simplified to single simulator (user: "take out the rest")
- CUT: A/B test mode, shadow-a-whale, Monte Carlo — all functions, UI, state, feature keys, tier rows removed. Zero dangling references.
- LAB is now only the single strategy simulator: tile picker + presets (corners/edges/cross/center/all/clear) + SOL/rounds + backtest with hit rate vs random baseline.

## 2026-10-12 — Round explorer overhaul + full-app design pass (designer)
### STATS: round explorer rebuilt
- NEW: real 200-round browser (was lenses-only). Search by round #, sort chips (Newest/Biggest pot/Most miners), scannable rows (tile badge, round #, SOL/miners/time, ~ for estimates).
- Lenses reorganized under "Insights" subhead; browser first. All tier gates intact.
- Detail overhaul: concentration %, pot vs window avg, miners vs avg, ⚡ anomalous badge (|z|>=2.5, links to anomaly tab). Estimates marked ~.
- rtime/rmap hoisted to module scope for reuse.
### ANOMALY tab fixed (was throwing ReferenceError — renderAnomaly undefined)
- Built detectAnomalies(): z-scores on pot/crowd/concentration + per-tile floods (stricter 4σ bar).
- Built renderAnomaly(): exclusive locked screen ($7/day, 100 slots) + live radar (86 signals: 29 extreme/17 strong/40 notable — V3-corpus build of Oct 7; the live V4 window shows 28), severity filters, feed with round links.
### Design polish (all tabs)
- :focus-visible gold outlines on interactive elements.
- .card-h .eyebrow margin rhythm unified; .tag inline-flex alignment.
- Round browser in capped scroll container (lenses stay reachable).
- Verified: node --check clean, 14/15 functional tests pass (1 timing artifact, proven via screenshot), zero JS errors, 18 screenshots at 360/412.

## 2026-10-12 — Audit fix (code auditor m1)
- _roundAnomaly badge aligned to radar feed thresholds: |z|≥2.0 for pot/crowd/concentration, z≥3 for tile floods. Badge now fires on 38/200 rounds (was 6); biggest-pot round correctly badged.


## 2026-10-12 — Full overhaul: STATS round explorer + ANOMALY rebuild (user: "overhaul everything")
- STATS: real 200-round browser (search, 3 sorts), lenses under "Insights", overhauled round detail (concentration, pot-vs-avg, anomaly badge).
- ANOMALY: was crashing (renderAnomaly referenced but undefined) — built detectAnomalies + renderAnomaly to spec; locked screen + radar + filters.
- Polish: focus-visible outlines, eyebrow rhythm, tag alignment.
- Audit fixes: anomaly badge thresholds aligned to feed (38/200 badged); wallet Find button width:auto; badge now renders anomaly tab on tap.

## 2026-10-12 — Tab consolidation: 6 tabs -> 4 (user: "3 tabs plus the info tab")
- Nav now: MINERS | REPLAY | ROUNDS | INFO.
- New ROUNDS tab merges STATS + LAB + ANOMALY as sub-views (Explore / Simulate / Radar chips).
- showIntel(sub) switches panes and renders on demand; anomaly badge rewired to showIntel('radar').
- Zero stale references to old tab IDs; all tier gates preserved.

## 2026-10-12 — Checker findings, tab consolidation (6→4)
- Fixed stale "Stats tab" copy in INFO (now "round explorer") and "Stats dates" (now "Round dates").
- Fixed FAQ "What are the plans?" advertising removed "whale watch" — now lists actual paid features.
- Verified: header trims clean, "Radar scan" rename complete, showIntel switching sound, all tier gates hold, anomaly-above-lifetime story intact as sub-pane.

## 2026-10-12 — Checker fixes (tab consolidation)
- INFO copy: "Stats tab" references updated to "round explorer" post-consolidation.
- FAQ: removed advertised "whale watch" (feature was cut; superseded by ANOMALY).
- Checker verdict: SHIP.

## 2026-10-12 — Cuts + onboarding (user: "fix what you think is best")
- CUT: Fresh intel lens (heating/cooling on random-walk data = filler).
- RENAMED: "Weekly intel report" -> "Window report" (honest: it's a window, not a week).
- ADDED: 3-screen first-run onboarding (what it is / free tier / paid tiers), localStorage dismissal.
- NOT DONE (need user decisions): payments backend, .skr companion spec, live-data direction.

## 2026-10-12 — .skr companion + payments (user: ".skr companion build it out", "payments as well")
- COMPANION: ghost companion hatches from wallet lookup; trait from mining fingerprint (Kraken/Phantom/Mist/Watcher/Rookie); SVG ghost with trait colors; XP for exploring (dossier +10, replay +15, sim +10, round +5); levels via sqrt curve; localStorage persisted.
- PAYMENTS: real Solana Pay checkout (not fake UI). Tier modal with live SOL pricing (Jupiter, $180 fallback); pay -> wallet deep link with memo; on-chain verification via RPC before unlocking; purchases stored with expiry in localStorage; Entitlements honors purchases. Treasury unset = honest "not configured" state, no fake charges. Set PAYMENTS.treasury to go live.
- Tier table chips now open checkout; locked cards have Unlock buttons.

## 2026-10-12 — Audit fixes (cuts/onboarding round)
- Onboarding slide 2: "whale tracking" -> "miner compare" (removed feature).
- Tier tables: "Weekly report" -> "Window report" (already fixed in payments build, verified 0 remnants).
- FAQ: fixed 2 IIFEs calling $() before definition — FAQ list was silently empty; now renders.

## 2026-10-12 — Audit fixes (companion + payments round)
- Code auditor 3 MAJORS fixed: (m1) tier-table IIFEs used $ before definition — tier table was empty, payment chips dead; (m2) activePurchase priority inverted — anomaly buyers locked out, now breaks on first match; (m3) verifyPay now checks lamports >= 99% of expected SOL (was memo-only).
- Minors: duplicate tier-table IIFE removed (regex cut too deep — also ate setTZ's closing brace, restored); pend.tier validated; hatch XP 50->75; SOL rate footnote shows "(estimate)" on fallback.
- Design auditor: PASS, 2 minors fixed.
- FAQ: fixed 2 more IIFEs with the same $ bug — FAQ was silently empty.

## 2026-10-12 — Checker fixes (companion + payments NO-SHIP -> SHIP)
- ROOT CAUSE (checker): FAQ/tier-table scripts ran before DOM existed — getElementById returned null, guards skipped silently. Converted to renderFAQ()/renderTierTable() functions called at boot. Tier table + FAQ now render; payment chips live.
- XSS: hatch button now uses data-w attribute instead of esc() in onclick string.
- Trait whitelisted on companion load; XP clamped to >= 0.
- Removed duplicate renderFAQ definition.

## 2026-10-12 — Timezone switcher removed
- Removed the 8-chip timezone selector from the header. It was hardcoded to LA, formatted estimated (~) round times, and ate a full header row — false precision.
- All times now use the device's local timezone. Simpler, honest.

## 2026-10-12 — Timezone auto-detect
- Timezone auto-detected from device via Intl.DateTimeFormat().resolvedOptions().timeZone, PST (America/Los_Angeles) fallback.
- No selector UI — it just follows the device. All formatters use the detected zone.

## 2026-10-12 — Replay player reorder
- Player layout: tile board first (right under the banner), then telemetry, then the round info card (date/round/time/clock/mute) at the bottom.

## 2026-10-12 — GHOST tab swap (user decision: option 3 + INFO rehoused)
- Nav: INFO tab replaced by GHOST (companion's own tab). REPLAY icon 👻 -> 🎬 so GHOST owns 👻.
- INFO page (plans/tier table, Anomaly, FAQ, methodology) unchanged in content, now reached via a 44px "i" button in the header brand row — visible on every screen. Back link returns to GHOST.
- t-ghost: companion card (moved from MINERS), Five temperaments guide, Grow-your-ghost XP card, Your-plan card linking to info page.
- Hatch flow: cmpHatch now lands on the GHOST tab; already-hatched lookup offers "View your ghost".
- Checker SHIP-WITH-FIXES: badge nowrap; small-screen brand row tightened (padding/gaps/wordmark/badge/infoBtn 40px at <=380px) — header fits 360px with no overflow.

## 2026-10-12 — Anomaly into ROUNDS (modular) + GHOST pricing UI
- AnomalyModule facade (signals/byRound/topFor/has/badgeHTML, cached) — all ROUNDS anomaly surfaces flow through it; radar reads through it too. Spin-out seed for a future standalone.
- Explore: ⚡ severity badges on flagged rounds in the list + "⚡ Anomalies only" filter chip (composes with search/sort).
- GHOST tab: real pricing — Plans card (Day $1 / Monthly $5 / Lifetime $50, Buy -> checkout, active-purchase aware) + violet Anomaly card ($7/day Unlock). Order: companion -> plans -> anomaly -> temperaments -> grow.
- User decision: anomaly NOT spun out yet — spin-out gates on live collection reopening.

## 2026-10-12 — Pay in ORE checkout rail (user: "Pay in ORE — deep cut, super obvious")
- Checkout gains a currency toggle: ◎ SOL | ⛏ ORE. ORE re-prices all tiers live (usd / Jupiter ORE rate; footnote "Pay with what you mine · 1 ORE ≈ $X (live rate)").
- PAYMENTS.oreMint = oreoU2P8bN6jkk3jbaiVxYnG1dCXcYxwhwyK9jSybcp (current ORE mint, 11 dp; cross-checked Coinbase + Phemex; config comment says re-verify at go-live). oreDecimals:11.
- payNowOre(): Solana Pay SPL link (amount 6dp + spl-token + memo + label), pending {tier, cur:'ORE', amt, memo, t}. verifyPay dispatches on the PENDING's currency tag; ORE branch verifies treasury token-balance delta (pre/postTokenBalances, mint+owner match, BigInt base units, 99% tolerance). Legacy SOL pendings unaffected.
- orePrice() has NO invented fallback: failure -> 0 -> rows show "—", Pay button replaced by an honest unavailable note.
- Audit fixes applied: .pt-price nowrap/flex-shrink (design minor); balance delta sums ALL matching token accounts (was first-match); payNowOre treasury guard.
- Audits: design PASS, code PASS (8/8 fabricated verify cases), checker SHIP (split-account cases pass post-fix). Treasury still null — no charge possible.

## 2026-10-12 — Outline tiles + CHAT tab (user directives)
- TILES OUTLINE-NOT-FILL (user: "color outline not fill, cleaner aesthetic"): replay canvas draws dark base + heat-colored stroke (width 1.5+heat*2.5, flash brightens edge, winner pulse ring kept, labels always light); DOM boards (.fp-tile round detail + fingerprints) use new fpEdge() ramp as border-color on transparent bg; sim-tile.on = transparent + 2px gold border + inset glow. fpColor deleted (dead). replay_engine.js untouched (md5 verified); only the inline copy's draw() changed.
- CHAT TAB (user: "add back a chat function, like a discord for .skr"): 5th nav tab. ChatModule (self-contained): channels #mining-floor / #strategies (postable) + #anomaly-radar (read-only feed of REAL AnomalyModule signals via rtime(), tap -> roundDetail). Identity = companion ghost handle (trait.skr) or you.skr; localStorage haunt_chat_v1; reactions (single, toggle), delete-own, 500-char cap, 200/channel cap, +2 companion XP/msg capped +10/day. Honest "local preview" banner — NO simulated users; transport boundary documented for the future backend (see AGENT_DATA_API_SPEC P4).
- Audit fixes: composer offset 57px -> 77px (was sliding behind nav — design MAJOR); list padding-bottom 170px; feed rows got a › chevron; stored-XSS hardening (esc(m.av), id whitelist-sanitize — hard gate before any backend transport); load() coerces non-object roots (array-root silent message loss); collision-proof message ids; zero-count reaction keys deleted.
- Audits: design PASS (tiles 5/5; chat 4/5 + MAJOR fixed), code PASS (26 assertions), checker SHIP.

## 2026-10-12 — Anomaly exposure in MINERS (user: "build the anomaly into miner tab" / "do what you think is best")
- Build-time derivation in build_app.py: Python replica of AnomalyModule.detect() (parity-verified EXACT: 86 signals (V3-corpus build of Oct 7; the live V4 window shows 28), tile-index granularity) joined with 133,308 deploy events for leader wallets. D.expo per wallet {p covered rounds played, a anomalous, rate, sol, hot (SOL on flooded tiles), top<=5 appearances}; coverage 164 of 200 window rounds (events end #431378) — labeled in the UI.
- Surfaces: MINERS sort chip "⚡ Anomaly" (locked -> t-about upsell; entitled -> sorts by SHRUNK rate (a+2)/(p+8), p>=5 gate); leaderboard row chip (a>=3 & rate>=0.4: entitled "⚡ NN% anomalous" violet, locked bare "⚡ anomaly-linked", no numerals); dossier "⚡ Anomaly exposure" card after Scout's take (entitled: rate/presence/SOL/hot + tappable appearance rows -> roundDetail; locked: lockedCard, digit-scanned zero leaks; no entry -> no card).
- Copy discipline: presence never causation ("Presence isn't causation" fineprint ships in the card).
- Post-audit hardening: Python _okR now mirrors JS Number.isFinite exactly; D.expo byte-identical after rebuild (md5 3d3b06cb...).
- Audits: design PASS 6/6, code PASS (independent 128-wallet recomputation, 0 mismatches), checker SHIP.

## 2026-10-12 — Gated backend verification hook (shikai: haunt-backend)
- PAYMENTS gains `backend:null` (config comment: set to the haunt-backend base URL to verify payments server-side; null = on-device verification as before).
- verifyPay(): when PAYMENTS.backend is set, it FIRST POSTs the pending ref {memo, tier, cur, amt} to {backend}/v1/pay/verify; on a 200 {verified:true} it accepts via the identical local success path (paySave + clear pending). ANY failure (unreachable, non-200, not verified) falls through to the existing on-device verification, unchanged.
- Default OFF: `backend:null` ships in the build; with it null the shipped flow is literally untouched (guard is the first check). Backend is signature-based, so a memo-only request answers 400 signature_required and the client falls back — full server-side flow activates when the client passes a tx signature.
- Verification: rebuild clean; all 4 script blocks node --check OK; D md5 unchanged (ee29390c602ae3ea9105ce7610a22d9c); replay_engine.js md5 unchanged (8425744bdebd2ed0676f9efafcd342e5); 'backend' occurrences 5 -> 12, all inside the config line + verifyPay gate (pre-existing 5 are prose/comments).
- Backend itself: ~/workspace/haunt-backend/ (stdlib-only Python; 26/26 tests incl. replay, 20-thread slot race, RFC 8032 vectors; treasury unset = settlement impossible, 503 honest).

## 2026-10-12 — SHIKAI verification addendum (ROOK witness, SHADOW seal)
- Backend (~/workspace/haunt-backend) independently re-proven: 26/26 tests rerun OK; replay/race/split-account/rolling-window all reproduced by the verifier, incl. a barrier-synchronized double-payment attempt -> exactly 1 entitlement.
- CORRECTION to earlier price-API notes: the shipped client fetches price.jup.ag/v6 (not api.jup.ag/price/v2) — and that host is DEAD (connection dies; api/lite-api hosts answer). Effect today: dormant (treasury null; ORE price honestly unavailable, SOL shows labeled estimate). ACTIVATION-GATED MAJOR: activation patch must repoint both client price fetches to lite-api.jup.ag/price/v3 incl. the new parse shape.
- Activation patch (sealed as known-required, 3 parts): (i) client takes backend quote at pay time and uses its HAUNT:<request_id> memo in the Solana Pay link; (ii) verifyPay forwards the tx signature it discovers to /v1/pay/verify; (iii) price endpoint repoint above. Until then backend-configured clients fall back to local verification — incomplete, never unsafe (proven: backend cannot return verified for any request the current client can make).
- Production gates (minors, named): /v1/slots/release unauthenticated + sequential hold IDs (griefing only); quote/claim routes unauthenticated (hold cycling, 10-min TTL bound; wire auth before production); quotes.amount is float-born (error ~1e-5 base units vs 1% tolerance — acceptable, matches client).

## 2026-10-12 — ACTIVATION PATCH (the sealed 3 parts + backend payer amendment)
- (i) QUOTE-FIRST PAY FLOW (gated on PAYMENTS.backend; null = shipped flow untouched): payNow()/payNowOre() first POST {backend}/v1/pay/quote {tier, currency} — no wallet, the client has none — and use the returned HAUNT:<request_id> memo + server amount in the Solana Pay link and the stored pending (pending gains backend:true + req). Any quote failure falls back silently to the local memo+amount flow. EXCEPTION: a sold_out anomaly quote shows the honest sold-out state (all 100 rolling-24h slots taken + next-free time) and does NOT fall back — a local payment would bypass the slot cap.
- (ii) SIGNATURE-FORWARDING VERIFY: the old memo-POST pre-check in verifyPay() is removed. Now, when pend.backend is set, each signature the getSignaturesForAddress scan discovers is FIRST POSTed to {backend}/v1/pay/verify {signature}; on {verified:true} the client accepts through the SAME local success path (paySave with the response tier, allowlist-checked) and stops. Any other outcome continues with the on-device delta verification for that signature, unchanged.
- (iii) PRICE REPOINT (all configurations — the sealed MAJOR): solPrice()/orePrice() now fetch https://lite-api.jup.ag/price/v3?ids=<mint> and parse j[mint].usdPrice (v3 shape). Failure semantics unchanged: ORE -> 0 -> '—' rows + unavailable note; SOL -> labeled $180 estimate fallback. Live smoke with the patched parse: SOL $108.03, ORE $118.34.
- BACKEND AMENDMENT (~/workspace/haunt-backend): quotes are wallet-OPTIONAL (anonymous quotes; quotes.wallet + slot_holds.wallet nullable, with an in-place migration for pre-amendment DBs). verify_payment DERIVES the payer from the tx — SOL: largest non-treasury negative lamport delta >= 99% of expected; ORE: owner whose combined per-owner token delta is negative >= 99% — and grants the entitlement to the DERIVED payer even when a quote claims a different wallet (response: {verified, tier, wallet:<derived>, claimed_wallet:<quote's>, expires_at}). No derivable payer -> 'payer_not_found', nothing granted, signature NOT consumed. Anomaly conversion stamps the derived payer onto the slot hold so one-per-wallet sees anonymous passes. Direct /v1/slots/claim still requires a wallet.
- PROOF: backend suite 26 -> 33 tests, all pass (new: null-wallet SOL/ORE grants to derived payer, split-sender + treasury-only no-payer rejections with quote left pending and re-verifiable, claimed-wallet mismatch, anonymous anomaly conversion + payer stamp, old-schema migration). The one pre-existing fixture gap found en route: ore_tx never modeled the payer's token account (a real SPL transfer shows both sides) — fixture completed, semantics unchanged. Client: stubbed harness executing the REAL extracted payNow/payNowOre/backendQuote/verifyPay passed 18/18 checks (quote flow, sold-out branch, backend:null parity, quote-failure fallback, signature forwarding, backend-decline -> local verify). Rebuild clean; 4/4 script blocks node --check; D md5 ee29390c602ae3ea9105ce7610a22d9c UNCHANGED; replay_engine.js md5 8425744bdebd2ed0676f9efafcd342e5 UNCHANGED; haunt.html 543,508 -> 546,648 bytes. Shipped config still treasury:null + backend:null — nothing can charge.

## 2026-10-12 — ACTIVATION PATCH: code-audit fixes (2 majors + 2 minors)
- MAJOR 1 — SLOT HOLDS ARE NOW COST/IDENTITY-BOUND (backend): SLOT_HOLD_MINUTES 10 -> 2 (long enough to finish an open wallet payment, short as a grief window). Anomaly quotes REQUIRE a wallet ({error:'wallet_required'} — anonymous anomaly quotes are refused, burning zero holds); one live hold per wallet (existing) + ONE live hold per source IP (hold rows record source_ip; server passes the request IP into create_quote and /v1/slots/claim; second live hold from one IP -> 409 ip_hold_limit) + anomaly-quote rate limit 20 attempts/hour/IP (429 rate_limited; attempts table pruned lazily). Verify-time slot re-check: an anomaly quote's expires_at is the HOLD deadline, not a payment deadline — a payment whose blockTime (or verify time) is within QUOTE_TTL of quote creation settles; the slot is re-decided inside the grant transaction (live hold converts; lapsed hold + capacity -> fresh converted slot for the payer; capacity gone -> grant recorded with entitlements.note='refund_due' and response pass_state:'refund_due' — never silently lost). Non-anomaly expiry semantics unchanged.
- MAJOR 1 (client): anomaly quote failures incl. wallet_required fall back to the local flow like any other quote failure (sold_out stays explicit); pay buttons gain an in-flight guard — _payInFlight + _payBusy() disable #payBody buttons during the quote fetch and ignore repeat taps in payNow()/payNowOre(), with re-enable in a finally on EVERY exit path (success, sold-out, fallback, ORE price failure). Proven on the built page (Playwright, quote route delayed 600ms): triple payNow() -> exactly 1 quote request, button disabled mid-flight, guard released after success / sold-out / wallet_required exits, zero page errors.
- MAJOR 2 — PAYER BINDING IS INSTRUCTION-DERIVED (backend): solana.sol_payer/ore_payer (balance-diff heuristics) are REPLACED by instruction_payer_sol/instruction_payer_ore: parse parsed instructions INCLUDING innerInstructions; sum transfers INTO the treasury per source (SOL: system transfer source; ORE: source token account's owner, resolved via the tx's pre+post token balances — a closed source account resolves through pre; destination must resolve to treasury-owned + ORE mint). The payer must be a SIGNER whose own contributions reach >=99% of expected. Treasury balance-delta >=99% remains the amount gate. Swap-routed payments (contributions from a non-signer pool) now fail closed with payer_not_found; a co-signer shuffling larger amounts can no longer steal the binding. One NARROW fallback remains: only when instruction parsing yields no candidate, balance deltas of SIGNER accounts only, binding only when EXACTLY ONE signer qualifies. Fixtures re-expressed in instruction terms (sol_tx/ore_tx now carry real parsed transfer instructions + addressed token accounts).
- MINOR 2 — MULTI-MEMO: verify_payment considers ALL memos in the tx and prefers a pending, unstale quote; a stale or consumed memo earlier in the tx no longer poisons a good one (probe K). Error derivation from the first candidate when none qualifies is unchanged (quote_not_pending / quote_expired).
- MINOR 4 — MIGRATIONS ARE TRANSACTIONAL: db.py runs _migrate_nullable_wallets + the new _ensure_columns (quotes.source_ip, slot_holds.source_ip, entitlements.note) inside one BEGIN IMMEDIATE transaction on open — no half-migrated states.
- NOT FIXED BY DESIGN: Minor 1 (backend refusal + local unlock divergence — recorded design decision) and Minor 3 (theoretical race — no action) are untouched.
- PROOF: backend suite 33 -> 49 tests, ALL PASS (new: probe H bounds x6 incl. TTL + rate limit + cap-still-real, probe F swap-routed fail-closed, probe G co-signer victim binding, probe D per-owner summing across two source accounts, probe E closed source account, fallback lone-signer + ambiguity, probe K x2, refund_due x3 incl. capacity-free re-claim and too-old rejection). Probe H replicated standalone (cap=3): 4 anonymous attempts -> 4x wallet_required, 0 holds; distinct wallets one IP -> ip_hold_limit; cap still sold_out for a 4th distinct wallet+IP. Live HTTP smoke (throwaway treasury): anonymous anomaly quote -> 400 wallet_required x4 with slots/status holds=0; wallet-bound quote OK; second same-IP hold -> 409 ip_hold_limit; anonymous day quote still 200. Client rebuild clean; 4/4 script blocks node --check; D md5 ee29390c602ae3ea9105ce7610a22d9c UNCHANGED; replay_engine.js md5 8425744bdebd2ed0676f9efafcd342e5 UNCHANGED; haunt.html 546,648 -> 547,869 bytes; shipped config still treasury:null + backend:null — nothing can charge.

## 2026-10-12 — Activation patch checker addendum (SHIP)
- Checker independently re-proved both major fixes live + module level (49/49 tests; exhaustion attack dead; swap-routed -> payer_not_found with no grant and signature unconsumed; co-signed shuffle -> victim bound; refund_due recorded end-to-end).
- PRE-ACTIVATION ALIGNMENT ITEMS (tracked, cosmetic, fix when payments activate): (1) tier amounts display at 4dp while the Solana Pay URL carries 6dp — display understates the exact charge by rounding; align display precision with the URL. (2) Anomaly tier-row copy "100 slots/day" -> "100 slots · rolling 24h" at enforcement time (sold-out copy already uses rolling-24h).

## 2026-10-12 — TREASURY SET: payments LIVE (on-device verification)
- Owner's wallet resolved on-chain from raaru.skr (AllDomains ANS): FRShnxX7vi64gtBoA6JPmH9MBuVqtheEQaxA1D17wKdP. PAYMENTS.treasury set to it (single occurrence in the build); PAYMENTS.backend stays null -> the audited on-device verifier is the live path. Server-side verification activates when the backend is hosted and backend is set.
- Known accepted caveat (unchanged): entitlements are browser-local (forgeable by a determined user against their own unlock only; every payment itself is verified against the treasury's on-chain balance delta >=99% before unlock). Deployment remains POSTPONED — the build is live-capable but undistributed; backend hosting rides the deployment decision.
- Render checker SHIP: no not-configured note; Pay buttons live; solana: link constructs to the treasury with 6dp amount + memo; pending stored (sessionStorage haunt_pending, read back by verifyPay); prices verified against live lite-api v3 payloads; D md5 + replay engine unchanged.

## 2026-10-12 — REPLAY tab redesign: Last-4 page + elapsed timer (designer pass)
- Owner ask: "Optimize the replay so it shows the last 4 on one page with full information" + a timer above it saying how long ago it was. The old daypart-tab picker (Last Hour/Morning/Afternoon/Evening/Night chips over compact .rpick cards) is replaced in renderTopPicker() (build_app.py): replays deduped + validated as before, entitlement splice preserved verbatim (no 'allreplays' -> latest 1 only), sorted desc by round, split 4 + rest.
- NEW PAGE (#t-replay / #topPicker): (1) timer strip — "LAST REPLAY · ENDED <elapsed>" computed from the newest replay's LAST timeline timestamp; agoText(): <120 min -> "N min ago", <48 h -> "H h [M min] ago", else "D d [H h] ago". Single 20s text-only interval (tickAgo/_agoInt/_agoEnd), refreshed synchronously on every renderTopPicker() call. Deliberate deviation from a visibility-gated cleanup: the player Back button only calls show('t-replay') without re-rendering (and the boot render happens while t-view is up), so a visibility-killed timer would strand the strip on "—"; cleanup is element-existence-based instead (self-clears if #lastAgo leaves the DOM; start is gated on !_agoInt so it can never stack) — same always-on text-clock pattern as the header banner ticker. Player, openReplay(), engine, nav/show() wiring, boot autoplay: untouched.
- (2) 2x2 grid of full-information cards (fullCard()): round #, visible PLAY pill (WATCHING badge when that round is loaded in ENG, 'on' gold state on the currentRound card — picker parity with pickCard), static 5x5 board from agg.t (lastBoard(): DOM .fp-tile, value-only at card scale, fpEdge() outline colors, .win gold ring on the winner tile — renders ONCE from agg, no canvas/engine per card), winner line (TILE n · SOL on it), stat boxes TOTAL SOL / MINERS / DEPLOYS (agg.d/agg.m/deploys), date + start time (real timeline[0].t via fmtD/fmtT/tzAbbr — no ~ prefix) + duration (durText, first->last timeline event, m:ss). Whole card is the tap target -> openReplay(round), role/tabindex/Enter-Space pattern matching the mini-cards, full aria-label. (3) "Earlier replays" section below: remaining replays in the existing compact pickCard()/.rpick style — nothing orphaned.
- sec-h copy: "Last 4 hauntings" / "Four fresh rounds, rebuilt deploy-by-deploy. Tap to play." Layout notes: grid tracks are minmax(0,1fr) (plain 1fr tracks let aspect-ratio tiles + stat labels force 217px min-content cards in a 187px track — found by render bisect); at <=380px the sec-h eyebrow+subcopy hide, stat boxes flatten to a wrapping inline flex line, when-line drops to 9px (was ellipsizing the duration), winner line 10.5px (TILE 20 clipped by ~2px at 11px).
- PROOF (real-time Playwright, full Chromium, 412x915 + 360x740): boot autoplay lands in player on 431140; Back -> Last-4 page. 412x915: full 2x2 grid visible WITHOUT scrolling (grid bottom 826 vs nav top 838, 12px slack). 360x740: does NOT fully fit — 75px of scroll (grid bottom 738 vs nav top 663); structural: ~207px fixed header+nav chrome on a 740px viewport vs 4 board cards; reported honestly per the brief rather than shrinking boards into illegibility. Every number on all 4 cards DOM-verified against D.replays at both sizes (winner, win SOL 3dp, total SOL, miners, deploys, full date/time/duration string, 25 tiles, winner index, fpEdge border colors via normalized comparison, zero tile/text clipping). Timer: live text matched agoText(end of 431140) = "5 d 11 h ago" at check time; format ladder unit-proven (119 min / 2 h / 2 h 1 min / 2 d / 2 d 1 h / 2 d 5 h); Playwright clock mock proved the interval updates the DOM with no re-render ("2 h ago" -> +61 min -> "3 h 1 min ago" -> +45 h -> "2 d ago"). Taps: all 4 grid cards open the player on the right round, Back returns to the Last-4 page, earlier card (431115) opens correctly, all 5 nav tabs regression-clean. Zero pageerrors; only console errors are the local server's /favicon.ico 404 (environmental).
- SEALS: D md5 ee29390c602ae3ea9105ce7610a22d9c UNCHANGED; replay_engine.js md5 8425744bdebd2ed0676f9efafcd342e5 UNCHANGED; 4/4 script blocks node --check; haunt.html 553,084 bytes.

## 2026-10-12 — Last-4 fix pass (auditor consensus: 4 fixes)
- FIX 1 (design MINOR-1): the .l4-when line ellipsized the DURATION at <=380px under wider font metrics. fullCard() now wraps the weekday in <span class="l4-wd"> (hidden at <=380px) and the base .l4-when drops 10px -> 9.5px. Proof (real-time Playwright, full Chromium, true content widths via shrink-to-fit clone — scrollWidth clamps at clientWidth so it cannot show slack): 360px content 115.0 (default font) / 123.5 (DejaVu-forced) vs 144 client — was 151 under DejaVu pre-fix; rendered line at 360 reads "OCT 7 · 09:09 UTC · ⏱ 1:06" (duration visible on all 4 cards); at 412 the weekday is retained and content is 148.9 / 159.1 vs 165.
- FIX 2 (design MINOR-2): .l4-win hard-clipped the winner line on two-digit tiles ("SOL on i"). Trims: font 12px -> 11px (<=380px: 10.5 -> 9.5px), gap 6 -> 4px (<=380px: 3px), crown 13 -> 11.5px (<=380px: 11px). Proof: TILE 20 card (#431133) content width 144.3 / 159.5 vs 165 at 412 and 125.0 / 138.1 vs 144 at 360 (default / DejaVu) — pre-fix DejaVu needed 178 / 155. Screenshots show the complete "TILE 20 · 0.360 SOL on it" at both sizes; last-child right-edge check passes on every card.
- FIX 3 (design MINOR-3): picker bottom clearance — #topPicker gains padding-bottom:24px and the Earlier-replays label scroll-margin-bottom:96px. At 412 max scroll the film strip's bottom now rests 105px above the nav top (was 81px) and the label 298px clear; grid one-page fit untouched (grid bottom 820 vs nav top 838 at first paint, 18px slack).
- FIX 4 (functionality MINOR-1): renderTopPicker() sorted AFTER reps.splice(1), so the free/locked sample was bundle-order-first 431032 while boot autoplay played the newest 431140 and the timer strip anchored to the wrong round. Order swapped (sort desc, then splice). Proof: ?tier=free picker shows exactly one card, #431140, timer "5 d 11 h ago" identical to the paid view, no Earlier section rendered; paid/full state unchanged (4 grid cards + 4 earlier, same order/content).
- Regression: boot autoplay -> player; card #431133 opens the correct round; Back returns to the picker; all 5 nav tabs verified; zero pageerrors and zero non-favicon console errors across all runs (both viewports, default + hostile fonts, both tiers).
- SEALS: D md5 ee29390c602ae3ea9105ce7610a22d9c UNCHANGED; replay_engine.js md5 8425744bdebd2ed0676f9efafcd342e5 UNCHANGED; 4/4 script blocks node --check; haunt.html md5 68b55a4bc75f5beac4ae331b9d81b3f3.
- Note: an intermediate edit briefly placed the wd/md consts in refreshReplayPills() (multi-match on a shared snippet); caught on source re-read and reverted before rebuild — the player surface carries no change from this pass. Verified by grep: const wd= exists only at its 2 pre-existing sites + fullCard.

## 2026-10-12 — Treasury build guard
- build_app.py now asserts at build time that the finished HTML contains the treasury address (FRShnxX7...wKdP, raaru.skr) exactly once; any other count aborts the build WITHOUT writing haunt.html. Deliberate treasury changes must update EXPECTED_TREASURY in the same commit and be noted here. Proven both ways: normal build byte-identical (md5 68b55a4b...), tampered-treasury build exits 1 leaving haunt.html untouched.

## 2026-10-12 — Per-replay "minutes ago" labels on the REPLAY page
- Owner ask: "Label all tiles being shown by how many minutes ago." Every replay card on the picker now carries its own relative-age label for ITS round's end (that replay's last timeline timestamp), in the header timer's gold/tabular treatment (uppercase micro-label): grid cards get a right-aligned `.l4-ago` line under the card head (placement measured — the head row itself had ~40px free vs a 64–74px label, so the label got its own line); Earlier-replays film cards get a centered `.rpick-ago` row between the meta line and the bar.
- Live ticking: all labels are `.ago-el` elements carrying `data-end`; the existing single 20s interval (tickAgo) now refreshes every `.ago-el` from its own data-end (header #lastAgo included — same element-existence self-clear lifecycle). Text-node updates only; no card re-renders.
- Height budget (412 one-page fit preserved): card padding 10→7px with board width 78%→75% (tiles stay 128.2px vs 128.7px — byte-level size preserved), board margin 7/5→5/4, stats margin-top 6→4, when margin-top 5→4 (≤380px equivalents: padding 8→6, board 76%→73%, stats 5→3, ago font 9px). Net card height +2.9px; grid bottom 825.7 (default) / 826.2 (DejaVu) vs nav top 838 → 12.3/11.8px slack. Side benefit: wider cards inside (165→171px) IMPROVED the fixed lines' worst-case slack — when-line 11.9px, winner-line 11.5px under DejaVu (were 5.9/5.5).
- Verified (real-time Playwright, 360×740 + 412×915, default + forced DejaVu Sans): all 8 labels' text == agoText(round's last timeline ts) computed in-page at the same instant, data-end exact for all 8 (+ header == 431140's end); clock-mock +37 min advanced all 9 labels consistently with board/card node identity AND grid/film innerHTML (label texts masked) byte-identical — and the REAL 20s interval re-verified the same after a 26s wait; exactly 1 live 20s interval after boot, full tab cycle, and open+Back; self-clears when the picker empties; free tier shows exactly #431140 with card label == header label; zero pageerrors (only /favicon.ico 404s from the test server). D md5 + replay_engine md5 unchanged; treasury guard passes (FRShnxX7 ×1). haunt.html now 553,913 bytes, md5 707338c8a877f8609d841416606f61c6.

## 2026-10-12 — addendum: timer strip case harmonized
- Design delta-audit MINOR-1: the header strip's ago value rendered lowercase (raw agoText) while all 8 per-card labels are CSS-uppercased. Strip value now text-transform:uppercase (.l4-timer b) — one-line cosmetic fix, strip was the odd one out. Rebuilt; haunt.html md5 73ec291fd9017e23f8a9e7ad2747a749; all four CLOCK IN staged copies (submission-repo APK asset + app/, repo/, android/) refreshed to this build and md5-verified identical.

## 2026-10-12 — ChatModule fix pass (bug-hunt findings M1–M3, m4–m9, S1–S6 — owner approved full scope)
Scope: build_app.py ChatModule JS + chat CSS + chat section HTML only. Verified by render-level Playwright probes (full Chromium --headless=new, real-time, 360x740 + 412x915 + keyboard-shrink sizes): 77/77 checks PASS on the shipped build (76 in the full suite + S6 targeted re-probe on the same build).
- M1 (stored data bricked the tab): every stored message now passes a validation seam (cleanMsg/cleanList) on load — non-objects dropped, id sanitized ([a-zA-Z0-9_-], entry dropped if unusable), txt coerced (numbers/bools stringified, unpaired surrogates stripped), name falls back to anon.skr only when unusable, avatar must be a string of <=4 code points else the ghost, timestamp must be a finite positive number else no time label, `me` must be one of the three reaction keys. ALL write paths (send/toggleRx/del) operate on the cleaned list and write it back, so poison is purged on first write. render/renderList are try/catch-wrapped with a "floor glitched" fallback; the composer can never be left stuck-hidden by bad data. PROOF: seed {"floor":[null]} -> empty state renders, composer visible, zero pageerrors, send works, reload persists; junk seed [42,"text",true,{valid odd msg}] -> exactly 1 message renders (ghost avatar, no time label), no "[object Object]", no "Invalid Date", zero pageerrors; corrupt top-level stores ('not-json', '[]', '"string"') all degrade to a working empty chat.
- M2 (stored-XSS via reaction counts): counts are whitelisted to the three RX keys and coerced via Math.floor(Number(v)), kept only when finite and >0 — at the validation seam AND again at render (cnt() in msgHTML). No stored field reaches HTML unescaped (text/name/avatar already esc()'d; counts now numeric-only). PROOF: hunter payload rx {"🔥":"<img src=x onerror=window.__pwned=1>","⛏":"lots","👀":-5} -> window.__pwned undefined, ZERO <img> in the list, "lots"/"-5" never rendered. Post-text XSS battery (<img onerror>, <svg onload>, javascript: link) stays inert incl. after reload.
- M3 (silent quota loss): save() now returns a boolean. On failure send() does NOT clear the composer — the text stays in the box and a persistent error note (#chatErr, app note idiom) reads "Could not save — this device's storage is full. Your message is still in the box: free up space, then send again." Success hides it. PROOF (origin filled to ~300 units of headroom via binary-searched filler key): 480-char send -> input still holds all 480 chars, error visible, store unchanged; filler removed -> the same text sends, input clears, error hides.
- m5 (delete nuked id duplicates): the delete button carries its render index (data-idx); del(id, idx) splices exactly one entry — the tapped index when its id matches, else the first id match. PROOF: two messages sharing id "dup1" -> tapping the second's delete leaves exactly the first, in DOM and store.
- m6 (500 cap split emoji): cap enforced by code POINTS ([...txt].slice(0,500)) on both the stored text and the input path (maxlength attribute removed; send() is the enforcer). PROOF: 499 a's + ghost emoji stores exactly 500 code points ending in the intact emoji, zero lone surrogates, rendered tail is the emoji; a 501st code point is dropped cleanly.
- m7 (radar silent 30/86): radar footer now reads "Latest 30 of 86 signals — tap a signal to open the round. Read-only." with the total read live from AnomalyModule.signals().length. PROOF: rendered footer matches /Latest 30 of 86 signals/ and the total equals signals().length (86) measured in-page.
- m8 (shared draft -> wrong-room posts): drafts are scoped per channel (drafts{} keyed by channel id, stashed on every keystroke; channel chips now call go() which switches without clobbering; render restores the current channel's draft; a successful send clears only that channel's draft). Session-only by design (a draft is keystrokes, not data). PROOF: typed "floor draft text" in #mining-floor -> switched to #strategies (input empty) -> send posted nothing anywhere -> posted "strat real post" to strat only -> switched back: floor draft intact in the box, floor store still empty.
- m9 (whitespace no-op): whitespace-only send now shakes the composer (.chat-shake keyframes) and flashes a dashed hint note ("Nothing to send — whitespace does not count...") that auto-hides after ~2.4s; nothing is stored and the text stays. PROOF: shake class applied, hint visible with .hint styling, store count 0, input value preserved.
- S1 (178px void): .chat-list padding-bottom 170px -> 10px (the body already reserves 158px for the 150px fixed composer+nav stack; the list was double-paying). PROOF: void below the last message at max scroll = 18px at BOTH sizes (was ~178px); usable window at the newest message 644/915px (70%) at 412 and 458/740 (62%) at 360; fully-visible messages at newest: 7 (412) / 4 (360) vs baseline 4 / 2.
- S2 (reaction-row tax): the reaction row renders ONLY when the message has >=1 reaction or its picker is open (rxOpen, session-only); otherwise a compact full-width "+" affordance (.chat-rxadd) sits in the meta row's new .chat-ctl cluster and reveals working chips on tap — no dead UI, reactions addable to every message. PROOF: unreacted message has NO .chat-rx in the DOM and DOES have the + button; tap + -> 3 chips appear; tap fire -> count 1 + .on state persisted to the store; after reload the reacted message shows its row (and no +) with counts intact. Pitch per unreacted message: 80px at 412 (was 114.5) and 101px at 360 (3-line wrap); same-width delta vs a reacted message = 45.5px (40px row + margins) at both sizes.
- S3 (first-paint chrome): the "Local preview" banner is now a compact one-line note (45px at BOTH sizes, was 83.6px) with a dismiss X — dismissal persists in localStorage (haunt_chat_v1_note) and survives reload (proven). The "Posting as..." line moved INTO the chips row as a trailing .chat-who span (the standalone #chatWho block is gone); chips padding trimmed (2px 0 8px); #t-chat H1 compressed to var(--t-xl) with the sub tightened — heading block retained per the brief. PROOF: first paint fully-visible messages: 2 at 360 (baseline 0) and 4 at 412 (baseline 2); list top 343px (412) / 354px (360) vs baselines 469 / 498.
- S4 (keyboard): viewport meta gains interactive-widget=resizes-content; a visualViewport resize listener (watchVV, armed once) lifts the fixed composer above an overlay keyboard (bottom = measured keyboard height when >60px) and re-scrolls to the newest message; on layout-resizing shells the inline override clears and the default bottom tracks. PROOF (simulated 45% shrink): 412x503 -> 3 full messages visible, composer hittable (elementFromPoint), just-sent message fully visible, send persisted; 360x407 -> 1 full message visible, just-sent visible, composer hittable (baseline: 0 visible at both sizes).
- S5 (touch targets): delete hitbox is now 44x44 (min-size + flex centering, glyph unchanged, negative margins keep the row pitch); + affordance 40x40; reaction chips min-height 40px. PROOF (getBoundingClientRect at both sizes): del 44x44, rxadd 40x40, rx-chip height 40.
- S6 (third chip clipped at 360): the chips row is wrapped in .chat-chwrap; a right-edge fade (.more::after gradient) shows whenever the row overflows and the user is not at the end (fadeCheck on render + scroll). PROOF at 360: overflow 227px, fade shown on open, clears at scroll end; the #anomaly-radar chip scrolls fully into the visible box (18..139 within 14..346) and tapping it switches channels.
- REGRESSION (hunters' clean list stays clean): post-text XSS battery inert incl. reload; 60 rapid posts stored once each, order + unique ids exact, 60 in DOM after reload; 200-cap trims oldest on send (205 -> 200, first retained = seed #5, last = extra #9); radar write-guards (programmatic send/toggleRx/del on #anomaly-radar) are no-ops — store byte-identical; reaction persistence across reload; full 5-tab tour works with zero pageerrors.
- SEALS: treasury guard passes (FRShnxX7...wKdP exactly once in output); D md5 ee29390c602ae3ea9105ce7610a22d9c UNCHANGED; replay_engine.js md5 8425744bdebd2ed0676f9efafcd342e5 UNCHANGED; 4/4 script blocks node --check; haunt.html md5 b09adbad5bf353d8d0cfd08c025ff80e. NOTE: CLOCK IN staged copies still hold the previous build (73ec291f...) and must be refreshed to b09adbad... before filing.

## 2026-10-12 — ACTIVATION: live V4 bundle swap (corpus era backed up, reversible)
- BACKUP: bundles/ -> bundles_v3_corpus_backup/ (all 5 files incl. days.json); data/deploys_2026-10-07.jsonl -> data_corpus_backup/. Swap reverses with one copy back.
- SWAP: bundles/{leaders,rounds,replays,meta}.json replaced with the staged live bundle (ore-intel-live, built 2026-10-12T22:20Z from the project's own poller + scanner: rounds 437901-438122, 222 total / 200 recent, 150 leaders, replays 438052-438059, 15,233 events / 15,065 funded). data/ now contains ONLY deploys_2026-10-12.jsonl (old 10-07 file removed after backup; build globs data/deploys_*.jsonl). bundles/days.json remains but is not read by build_app.py.
- COPY TRUTH PASS (build_app.py, data-factual strings only):
  1. Embedded meta note: "All values from ORE V3 chain state. ..." -> "All values from ORE V4 chain state, collected live by this project's own poller + scanner. ..." (layout/DeployEvent/winner-formula clauses unchanged — V4-proven).
  2. INFO stats line: "rounds · ORE V3 program" -> "rounds · ORE V4 program" (count is dynamic).
  3. FAQ "What does ORE HAUNT track?": "...from a fixed observation window." -> "...collected live from ORE V4 chain state by our own poller and scanner." (counts unchanged — still true).
  4. FAQ "Is the data live?": "Not yet. This build uses a frozen observation window. Live data arrives with the paid backend." -> "Yes — this build's rounds, miners and replays were collected live from ORE V4 chain state by our own poller + scanner, then bundled into the app. It's a live-collected snapshot at build time, not a real-time stream; the header shows how old the bundled data is."
  5. INFO "What we estimate": "(~77.5s per round)" -> "(~63s per round)".
  6. CODE (not a string, flagged): rtime() explorer date anchor 431031 @ 2026-10-07T06:57:54Z + 77.5s -> 438059 @ 2026-10-12T21:08:49Z (real replay timestamp) + 63s/round (measured on replay span 438052-438059). Old anchor put today's rounds ~18h off, falsifying the "Close, not exact" claim; spot check: #438122 estimates ~22:14 UTC vs ~22:15 cadence projection.
  - NOT changed: program id oreV3EG1i9BEgiAJ8b177Z2S2rMarzak4NMv1kULvWv in the INFO fineprint — that IS the V4 program's address (proven identical). Generic "observation window" phrasing (GHOST/CSV/fingerprint) kept — still truthful. No hardcoded signal counts existed (all dynamic).
- BUILD: haunt.html 581,371 bytes, md5 fb8b6f6d68954ed51970e2097ed13edd. Treasury guard passed. node --check 4/4 script blocks. New D md5 06136615b90323f4cd673f2c1edeed56 (data changed — expected). replay_engine.js unchanged 8425744bdebd2ed0676f9efafcd342e5.
- VERIFIED (Playwright, full Chromium headless=new, 412x915 + 360x740, two passes, zero pageerrors): boot autoplay plays NEW replay 438059 (260 events; real-time advance at 2x confirmed, seek drove 260/260 to completion, clean return to picker). Last-4 grid 438059/58/57/56 + film 438055-52, ago labels in minutes (91-99 min ago), header "ended 91 min ago". ROUNDS explorer 437923-438122, detail #438122 renders (Tile 21 wins, 10.39 SOL, 166 miners). MINERS 150 leaders, top 8fB1oSDz...C5Kn 119.31 SOL; dossier opens with tile fingerprint + ANOMALY EXPOSURE card. NEW anomaly signals count = 28 (AnomalyModule.signals().length); chat radar footer "Latest 28 of 28 signals" matches. INFO shows "222 rounds · ORE V4 program" + "15,065 funded deploys". GHOST renders; checkout modal renders tiers (SOL fallback prices in sandbox — price fetch blocked by proxy, ERR_TUNNEL_CONNECTION_FAILED, environmental). CHAT post + delete verified via real input/click.

## 2026-10-12 — SKR payment rail added to checkout (third currency, mirrors ORE)
- PAYMENTS gains skrMint SKRbvo6Gf7GondiT3BbTfuRDPqLWei4j2Qy2NPGZhW3 (6 dp; triple-verified by parent: Solana Mobile docs + Jupiter verified registry) + skrDecimals:6. Treasury unchanged; build guard passes.
- skrPrice() mirrors orePrice() exactly (Jupiter Price v3, cached, 0 = unavailable, NO invented fallback). Toggle is now 3-way ◎ SOL / ⛏ ORE / 💎 SKR; renderPayTiers quotes SKR from the fetched price; on SKR fetch failure the modal shows the same honest unavailable note + "SKR price unavailable right now" fineprint and renders no Pay button (SOL's labeled-estimate fallback stays SOL-only).
- payNowSkr() mirrors payNowOre() (backend quote attempt, sold-out handling, local fallback quote): pending {tier, cur:'SKR', amt, memo, t} in sessionStorage haunt_pending; Solana Pay link amount at 6dp with spl-token=<SKR mint>.
- verifyPay generalized: SPL branch keyed to the PENDING currency's mint+decimals (isTok/isSkr) — the treasury's balance delta of the pending's own mint ≥99% unlocks; a delta in any other mint cannot verify it. ORE pendings resolve to the same mint/decimals as before (identical math).
- Copy sweep (owner directive): the app contains NO prose enumeration of payment currencies outside the checkout modal (FAQ has no payment-method entry; INFO/onboarding/GHOST name no currencies; modal helper text is currency-neutral) — the toggle + per-currency fineprints are the only enumeration and now include SKR. Nothing to change; two pre-existing stale "Payments aren't live yet" strings (INFO plans fineprint, onboarding slide 3) are NOT enumerations and were left untouched — flagged to parent.
- Verification (Playwright, full Chromium --headless=new, local server, Jupiter + RPC intercepted): toggle/layout/no-overflow PASS at 412×915 + 360×740; mocked prices SOL $200 / ORE $100 / SKR $0.02 → Monthly displays 0.0250 SOL / 0.0500 ORE / 250.0000 SKR; SKR pending {cur:'SKR', amt:250}; captured link solana:<treasury>?amount=250.000000&spl-token=SKRbvo6Gf7…PGZhW3&memo=haunt%3Amonthly%3A…&label=ORE%20HAUNT; fabricated SKR delta 250.000000 (≥99%) unlocks Monthly at both sizes; fabricated ORE-only delta against an SKR pending is rejected ("No matching payment found") at both sizes; SKR price-fetch abort → unavailable state, no Pay button, SOL/ORE unaffected; SOL + ORE links byte-identical to pre-change construction at both sizes; zero pageerrors throughout.
- Harness caveat: after the aborted solana: navigation, headless Chromium swallows subsequent trusted mouse/keyboard input on the verify button (button proven topmost via elementFromPoint; its onclick is the same pattern as the shipped SOL/ORE rails) — verify-step assertions were driven by invoking verifyPay()/DOM clicks after real mouse clicks built the pending. On-device tap behavior is unaffected (this is the known headless post-navigation artifact, same as the demo recordings hit).
- Seals: haunt.html 584,597 B md5 24b1daa09271ca9d6e61e4d2d8984b3f; D md5 06136615b90323f4cd673f2c1edeed56 UNCHANGED; replay_engine.js 8425744bdebd2ed0676f9efafcd342e5 unchanged; node --check 4/4 script blocks.

## 2026-10-12 (post-sweep) — "Enforced at launch" overclaim retired + historical 86-signal claims labeled
- Anomaly cap wording, app UI (build_app.py): INFO Plans card chips "🔒 100 slots / day" + "Enforced at launch" → "🔒 100 slots · rolling 24h" + "Capped · rolling 24h". ROUNDS → Radar locked card: chip "🔒 100 slots / day" → "🔒 100 slots · rolling 24h"; fineprint "Slots release daily and are enforced at launch." → "Capped at 100 slots per rolling 24 hours — the cap is client-side in this build; server-side enforcement activates when the backend hosts." Truth basis: PAYMENTS.backend is null in this build, so the cap is client-side here; the server-side slot ledger activates when the backend hosts (the fix previously tracked here as "at enforcement time" is now applied as honest capped wording instead of a launch claim).
- Historical labels appended (history not rewritten): PATCH_NOTES entries for the Oct 7 corpus-era anomaly work and the build_app.py exposure-derivation header comment now carry "(V3-corpus build of Oct 7; the live V4 window shows 28)" next to their 86-signal counts.
- Rebuilt: haunt.html 584,767 B, md5 fc168f268f92ea1d225efe3b8d550348; treasury guard passes; node --check 4/4; D md5 06136615b90323f4cd673f2c1edeed56 UNCHANGED; replay_engine.js 8425744bdebd2ed0676f9efafcd342e5 UNCHANGED. Built HTML: "Enforced at launch" x0; "enforced" x1 = internal JS comment on ANOMALY_SLOTS (not rendered copy — left per scope). Render-verified (Playwright, full Chromium --headless=new, 412x915 + 360x740): Radar locked card + INFO anomaly card show the new chips/fineprint, zero pageerrors. All four CLOCK IN staged copies refreshed to this build, md5-verified identical.
- Addendum (same evening): the two tails the sweep pass flagged were closed — the ANOMALY_SLOTS internal comment now reads "slots capped per rolling 24h — server-side enforcement activates when the backend hosts" (built HTML "enforced" count is now 0), and the Oct 7 PATCH_NOTES calibration entry (line 241) carries the same V3-corpus label. Final build after addendum: md5 9b2bfd4d1bf082e9716b9dc242b2f44f; D 06136615 UNCHANGED (balanced-brace extraction); node --check OK.
