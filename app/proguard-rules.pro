# ORE HAUNT release ProGuard rules.
# Keep the MWA clientlib and the JS bridge (called by name from the WebView).
-keep class com.solana.mobilewalletadapter.clientlib.** { *; }
-keepclassmembers class com.orehaunt.app.wallet.OreHauntWalletBridge {
    public *;
}
