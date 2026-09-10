package com.determinantmatrix.zhibo

import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import com.determinantmatrix.zhibo.designsys.ThemeMode
import com.determinantmatrix.zhibo.designsys.ZhiboTheme
import com.determinantmatrix.zhibo.ui.FunctionsScreen
import com.determinantmatrix.zhibo.ui.HomeScreen
import com.determinantmatrix.zhibo.ui.SettingsScreen

class MainActivity : ComponentActivity() {

    override fun onResume() {
        super.onResume()
        // 监控自恢复：已授予通知权限则随 App 启动自动恢复轮询（直播监控的核心预期）
        if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            startMonitoring()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // 首次启动先请求通知权限（Android 13+），保证开播通知可达
        setContent {
            val themeMode by ZhiboApp.instance.settings.themeMode.collectAsState(initial = "system")
            ZhiboTheme(
                themeMode = when (themeMode) {
                    "light" -> ThemeMode.LIGHT
                    "dark" -> ThemeMode.DARK
                    else -> ThemeMode.SYSTEM
                },
            ) {
                ZhiboRoot()
            }
        }
    }

    /** 进入画中画；条件不满足时静默失败（如已在 PiP 中）。 */
    fun enterPictureInPictureModeSafely() {
        if (Build.VERSION.SDK_INT < 26) return
        runCatching {
            enterPictureInPictureMode(
                android.app.PictureInPictureParams.Builder()
                    .setAspectRatio(android.util.Rational(16, 9))
                    .build(),
            )
        }
    }
}

/** 根导航：首页（直播间）/ 功能 / 设置。 */
@Composable
fun ZhiboRoot() {
    var tab by androidx.compose.runtime.remember {
        androidx.compose.runtime.mutableIntStateOf(0)
    }
    val manager = ZhiboApp.instance.playerManager
    val foreground by manager.foreground.collectAsState()
    val browser by ZhiboApp.instance.browserRequest.collectAsState()

    androidx.activity.compose.BackHandler(enabled = foreground != null && browser == null) {
        manager.showList()
    }

    Scaffold(
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = tab == 0,
                    onClick = { tab = 0 },
                    icon = {
                        Icon(
                            if (tab == 0) Icons.Filled.Home else Icons.Outlined.Home,
                            contentDescription = "首页",
                        )
                    },
                    label = { Text("首页") },
                )
                NavigationBarItem(
                    selected = tab == 1,
                    onClick = { tab = 1 },
                    icon = {
                        Icon(
                            if (tab == 1) Icons.Filled.Tune else Icons.Outlined.Tune,
                            contentDescription = "功能",
                        )
                    },
                    label = { Text("功能") },
                )
                NavigationBarItem(
                    selected = tab == 2,
                    onClick = { tab = 2 },
                    icon = {
                        Icon(
                            if (tab == 2) Icons.Filled.Settings else Icons.Outlined.Settings,
                            contentDescription = "设置",
                        )
                    },
                    label = { Text("设置") },
                )
            }
        },
    ) { padding ->
        androidx.compose.foundation.layout.Box(
            modifier = Modifier.padding(padding),
        ) {
            when (tab) {
                0 -> HomeScreen()
                1 -> FunctionsScreen()
                else -> SettingsScreen()
            }
        }
    }
}

fun startMonitoring() {
    val context = ZhiboApp.instance
    ContextCompat.startForegroundService(context, Intent(context, MonitorService::class.java))
}

fun stopMonitoring() {
    val context = ZhiboApp.instance
    context.startService(
        Intent(context, MonitorService::class.java).setAction(MonitorService.ACTION_STOP),
    )
}
