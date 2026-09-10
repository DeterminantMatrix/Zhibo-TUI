package com.determinantmatrix.zhibo

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.determinantmatrix.zhibo.core.monitor.CheckState
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme {
                val manager = ZhiboApp.instance.playerManager
                val foreground by manager.foreground.collectAsState()
                val browser by ZhiboApp.instance.browserRequest.collectAsState()
                when {
                    browser != null -> BrowserScreen(browser!!) {
                        ZhiboApp.instance.browserRequest.value = null
                    }
                    foreground != null -> PlayerScreen(manager)
                    else -> FollowersScreen()
                }
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FollowersScreen(vm: FollowersViewModel = viewModel()) {
    val followers by vm.followers.collectAsState()
    val settings by vm.settings.collectAsState()
    val message by vm.message.collectAsState()
    val controller = ZhiboApp.instance.engineController
    val statuses by controller.engine.statuses.collectAsState()
    val monitoring by controller.running.collectAsState()

    val pickFollowersCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let(vm::importFollowersCsv) }
    val saveFollowersCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("text/csv"),
    ) { uri -> uri?.let(vm::exportFollowersCsv) }
    val pickSettingsCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let(vm::importSettingsCsv) }
    val saveSettingsCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("text/csv"),
    ) { uri -> uri?.let(vm::exportSettingsCsv) }

    val pickFs1Yaml = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let(vm::importFs1Yaml) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { _ -> startMonitoring() }

    val clickScope = androidx.compose.runtime.rememberCoroutineScope()
    var showProxyDialog by androidx.compose.runtime.remember {
        androidx.compose.runtime.mutableStateOf(false)
    }

    val context = androidx.compose.ui.platform.LocalContext.current

    fun toggleMonitoring() {
        if (monitoring) {
            stopMonitoring()
        } else if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            startMonitoring()
        }
    }

    Scaffold(
        topBar = { TopAppBar(title = { Text("直播监控 · M1") }) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = ::toggleMonitoring) {
                    Text(if (monitoring) "停止监控" else "开始监控")
                }
                OutlinedButton(onClick = { controller.engine.refreshNow() }) {
                    Text("手动刷新")
                }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { pickFollowersCsv.launch(arrayOf("text/*", "text/csv", "application/csv", "text/comma-separated-values")) }) {
                    Text("导入关注 CSV")
                }
                OutlinedButton(onClick = { saveFollowersCsv.launch("followers.csv") }) {
                    Text("导出关注 CSV")
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { pickSettingsCsv.launch(arrayOf("text/*", "text/csv", "application/csv", "text/comma-separated-values")) }) {
                    Text("导入设置 CSV")
                }
                OutlinedButton(onClick = { saveSettingsCsv.launch("settings.csv") }) {
                    Text("导出设置 CSV")
                }
            }

            Text(
                text = "状态：$message",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
            )

            Text("凭据", style = MaterialTheme.typography.labelLarge)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val credStore = ZhiboApp.instance.credentials
                OutlinedButton(onClick = { ZhiboApp.instance.browserRequest.value = BrowserRequest.BilibiliLogin }) {
                    Text("B站登录采集${if (credStore.hasBilibiliCookie()) " ✓" else ""}")
                }
                OutlinedButton(onClick = { ZhiboApp.instance.browserRequest.value = BrowserRequest.Fs1Auth }) {
                    Text("FS1授权采集${if (credStore.hasFs1Auth()) " ✓" else ""}")
                }
                OutlinedButton(onClick = { pickFs1Yaml.launch(arrayOf("*/*")) }) {
                    Text("FS1配置导入")
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { showProxyDialog = true }) {
                    Text("平台代理")
                }
            }
            if (showProxyDialog) {
                ProxyDialog(
                    current = vm.settings.value.platformProxies,
                    onSave = { proxies ->
                        ZhiboApp.instance.refreshResolvers(proxies)
                        vm.notify("代理设置已保存并生效")
                    },
                    onDismiss = { showProxyDialog = false },
                )
            }
            Text(
                text = "设置：轮询 ${settings.pollInterval}s · 并发 ${settings.maxConcurrentChecks} · 通知 ${if (settings.notificationsEnabled) "开" else "关"}",
                style = MaterialTheme.typography.bodyMedium,
            )
            val liveCount = statuses.values.count { it.state == CheckState.LIVE }
            Text(
                text = "关注项（${followers.size}）· 在线 $liveCount" + if (monitoring) "" else " · 监控未启动",
                style = MaterialTheme.typography.titleMedium,
            )

            LazyColumn(
                modifier = Modifier.fillMaxWidth().weight(1f),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(followers) { f ->
                    val status = statuses[f.url]
                    val (dotColor, stateText) = when (status?.state) {
                        CheckState.LIVE -> Color(0xFF2E9E44) to "在线"
                        CheckState.OFFLINE -> Color(0xFFB0B0B0) to "离线"
                        CheckState.ERROR -> Color(0xFFD64541) to "异常"
                        null -> Color(0xFF9E9E9E) to "未检测"
                    }
                    val playing = ZhiboApp.instance.playerManager.handles.collectAsState().value
                        .any { it.followerUrl == f.url }
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable {
                                if (status?.state == CheckState.LIVE) {
                                    clickScope.launch { ZhiboApp.instance.playerManager.play(f) }
                                } else {
                                    vm.notify(f.name + if (f.enabled) " 未开播或未检测到在线" else " 已停用")
                                }
                            },
                    ) {
                        Row(
                            modifier = Modifier.padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(10.dp)
                                    .background(
                                        if (!f.enabled) Color(0xFFDDDDDD) else dotColor,
                                        CircleShape,
                                    ),
                            )
                            Column {
                                Text(f.name, style = MaterialTheme.typography.bodyLarge)
                                Text(
                                    text = buildString {
                                        append("${f.platform} · ${f.plugin}")
                                        if (f.enabled) append(" · $stateText")
                                        if (playing) append(" · ▶播放中")
                                        status?.title?.takeIf { it.isNotEmpty() }?.let { append(" · $it") }
                                        status?.error?.takeIf { it.isNotEmpty() }?.let { append("（${it.take(60)}）") }
                                    },
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun startMonitoring() {
    val context = ZhiboApp.instance
    ContextCompat.startForegroundService(context, Intent(context, MonitorService::class.java))
}

private fun stopMonitoring() {
    val context = ZhiboApp.instance
    context.startService(
        Intent(context, MonitorService::class.java).setAction(MonitorService.ACTION_STOP),
    )
}
