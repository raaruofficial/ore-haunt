package com.orehaunt.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.orehaunt.app.ui.GameScreen
import com.orehaunt.app.ui.HomeScreen
import com.orehaunt.app.ui.SettingsScreen
import com.orehaunt.app.ui.SplashScreen
import com.orehaunt.app.ui.WalletScreen
import com.orehaunt.app.ui.theme.OreHauntTheme

/**
 * Single-activity app. All navigation is native Compose Navigation —
 * the WebView is confined to the game screen and never drives app flow.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            OreHauntTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    val navController = rememberNavController()
                    NavHost(navController = navController, startDestination = "splash") {
                        composable("splash") {
                            SplashScreen(onDone = {
                                navController.navigate("home") {
                                    popUpTo("splash") { inclusive = true }
                                }
                            })
                        }
                        composable("home") { HomeScreen(navController) }
                        composable("game") {
                            GameScreen(
                                navController = navController,
                                onExit = { navController.popBackStack() }
                            )
                        }
                        composable("wallet") { WalletScreen(navController) }
                        composable("settings") { SettingsScreen(navController) }
                    }
                }
            }
        }
    }
}
