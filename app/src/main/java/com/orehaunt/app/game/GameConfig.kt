package com.orehaunt.app.game

/**
 * How the app content reaches the APK.
 *
 * DECIDED: BUNDLED_ASSET (same pattern as the HINDSIGHT entry). The current
 * ORE HAUNT build — a single self-contained HTML file from
 * ~/workspace/ore-intel/build_app.py — is placed at
 * app/src/main/assets/game/index.html. Fully offline-capable: the 150-miner
 * leaderboard, 200-round explorer, replays, anomaly signals and chat preview
 * are all bundled in the file. Live prices/payments use the network when
 * available.
 *
 * The [OreHauntWallet] JS bridge is the seam where the native MWA wallet
 * meets the app (exposed as `window.OreHauntWallet`). The staged shell wires
 * the bridge into the WebView exactly like the sibling entries; the current
 * haunt.html does not call it yet (its checkout uses Solana Pay links), so
 * the bridge is present-but-uninvoked native substance for now — see
 * SCAFFOLD_NOTES.md.
 */
object GameConfig {
    /** Bundled static app. */
    const val GAME_URL: String = "file:///android_asset/game/index.html"

    /** Name the app uses to reach the native wallet: `window.OreHauntWallet`. */
    const val BRIDGE_NAME: String = "OreHauntWallet"
}
