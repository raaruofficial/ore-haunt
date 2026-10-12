# ORE HAUNT — CLOCK IN Android scaffold: staging notes

Staged: 2026-10-12 (CLOCK IN deadline today 4:59 PM PDT). Status: **staged, not compiled** — CI builds the APK; no local gradle/Android SDK pass was run.

## Source scaffold

Base copy: **`~/workspace/voidrunner/`** (the VOIDRUNNER CLOCK IN entry) — chosen over `~/workspace/hindsight-android/` because its tree is complete and lean (proguard rules, README, .gitignore, no multi-MB asset pack) while sharing the identical architecture: Kotlin + Compose shell, WebView game screen, Solana MWA clientlib 2.0.8, env-based release signing, GitHub Actions workflow.

One pattern was taken from the HINDSIGHT scaffold instead: its **DECIDED BUNDLED_ASSET** `GameConfig` (WebView loads a bundled local HTML file). VOIDRUNNER's own GameConfig still pointed at a placeholder hosted URL, which is the weakest option under CLOCK IN's "no web wrappers" scoring.

MWA wiring (WalletManager, wallet bridge, GameScreen's `addJavascriptInterface` + `loadUrl`) is the siblings' code verbatim apart from the name swaps below — nothing was re-implemented.

## Adaptations (VOIDRUNNER → ORE HAUNT)

- Package/namespace/applicationId: `com.voidrunner.app` → **`com.orehaunt.app`** (follows the sibling convention `com.<name>.app`; not a placeholder).
- `rootProject.name`: `orehaunt`. App label (`strings.xml`): **ORE HAUNT**. Theme: `Theme.OreHaunt`.
- Classes renamed: `VoidrunnerApp` → `OreHauntApp` (file too), `VoidrunnerWalletBridge` → `OreHauntWalletBridge`. Zero `voidrunner` references remain (grep-verified).
- `GameConfig.kt` rewritten to the bundled-asset decision: `GAME_URL = "file:///android_asset/game/index.html"`, `BRIDGE_NAME = "OreHauntWallet"` (JS seam: `window.OreHauntWallet`).
- **Bundled app**: `app/src/main/assets/game/index.html` = the current `~/workspace/ore-intel/haunt.html` build, byte-identical — md5 `68b55a4bc75f5beac4ae331b9d81b3f3` on both sides (553,397 bytes). The APK runs the real HAUNT build fully offline; live prices/payments use the network when present.
- CI workflow (`.github/workflows/android.yml`): sibling file kept; artifact names → `orehaunt-debug-apk` / `orehaunt-release-apk`; keystore temp file → `/tmp/orehaunt-release.jks`. **Secret names unchanged from the sibling entry**: `KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD` (release signing also reads them as env in `app/build.gradle.kts` — same scheme).
- `README.md` is the sibling's README with names swapped — its "Open decision: game content" section is superseded by the DECIDED bundled-asset note in `GameConfig.kt`. Left in place deliberately (it documents the signing/CI scheme accurately).

## The one non-sibling addition (flagged)

Neither sibling ships launcher-icon resources, yet the manifest references `@mipmap/ic_launcher` — a resource that must exist for the build to link. Added, minimally:

- `res/mipmap-anydpi-v26/ic_launcher.xml` + `ic_launcher_round.xml` (adaptive icon; minSdk is 28, so v26+ resources cover every supported device)
- `res/drawable/ic_launcher_foreground.xml` — a simple gold ghost vector (placeholder art)
- `res/values/ic_launcher_colors.xml` — background `#0B0B14`

All XML parse-verified. If the sibling entries built fine without icons, this addition is harmless; if they didn't, this is the fix. Replace the ghost vector with brand art when convenient — it is explicitly placeholder.

## Uncertainties

1. **Not compiled.** No local APK build was attempted (no SDK pass in the timebox). First CI run is the real test; the risk areas are the same ones the siblings carry (Compose BOM `2026.06.00`, MWA clientlib 2.0.8 resolution) plus the new icon resources above.
2. **The MWA bridge is present but uninvoked by the app.** `haunt.html`'s checkout uses Solana Pay links (which hand off to whatever wallet the OS routes them to); it does not call `window.OreHauntWallet`. Native substance today = the Compose shell, native screens (splash/home/wallet/settings), and the full MWA connect/sign stack in `WalletManager`/bridge — identical posture to the sibling entries at their staging point. Wiring `haunt.html` to prefer the bridge is a follow-up, not a deadline task.
3. **No Gradle wrapper in the tree** — same as both siblings (`gradle/wrapper/` is empty there too). CI provisions Gradle 8.11.1 via `gradle/actions/setup-gradle`, so builds don't need one; local command-line builds would need `gradle wrapper` run once or a system Gradle.
4. HAUNT's shipped build still has `PAYMENTS.backend: null` (on-device verification) with the treasury set — that's the app's own audited state and is unaffected by packaging.

## Owner steps (in order)

1. **Repo**: create the ORE HAUNT CLOCK IN repo (e.g. `raaruofficial/ore-haunt`) — or, if the submission plan folds this into an existing repo, push the contents of this `android/` directory as that repo's root (this directory *is* a repo root: workflow sits at `.github/workflows/` relative to it).
2. **Push** this directory's contents to `main`.
3. **Add the 4 signing secrets** to the repo (Settings → Secrets → Actions), same names as the other entries: `KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`. (Debug APK builds need none of these.)
4. **Get the APK**: Actions → "Android CI" → download `orehaunt-debug-apk` from the push build for immediate testing/sideload. For the signed release: `git tag v0.1.0 && git push origin v0.1.0`, then download `orehaunt-release-apk`.
5. **Install on the Seeker**, smoke-test: app opens on the bundled HAUNT, tabs navigate, checkout buttons render (payments verify on-device; backend still null by design).
6. **Submit via Align** (solanamobile.radiant.nexus) before **4:59 PM PDT today** with the APK + repo link + the demo video/deck from the `clock-in/` folder.

## File tree (28 files, ~668 KB)

Standard sibling layout: root `build.gradle.kts` / `settings.gradle.kts` / `gradle.properties`; `app/` module (`build.gradle.kts`, `proguard-rules.pro`); Kotlin sources under `app/src/main/java/com/orehaunt/app/` (`MainActivity`, `OreHauntApp`, `game/GameConfig`, `ui/` ×6 incl. theme, `wallet/` ×2, `util/Base58`); resources (manifest, strings, themes, launcher icon set); bundled app at `app/src/main/assets/game/index.html`; CI at `.github/workflows/android.yml`.
