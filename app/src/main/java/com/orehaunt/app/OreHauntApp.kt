package com.orehaunt.app

import android.app.Application
import com.orehaunt.app.wallet.WalletManager

/**
 * Application holder for app-scoped singletons.
 * The [WalletManager] lives here so the wallet session survives navigation
 * and configuration changes.
 */
class OreHauntApp : Application() {
    val walletManager: WalletManager by lazy { WalletManager() }
}
