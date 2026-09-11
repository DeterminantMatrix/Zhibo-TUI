package com.determinantmatrix.zhibo.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.determinantmatrix.zhibo.BrowserRequest
import com.determinantmatrix.zhibo.FollowersViewModel
import com.determinantmatrix.zhibo.ProxyDialog
import com.determinantmatrix.zhibo.ZhiboApp
import kotlinx.coroutines.launch

/**
 * 设置页：凭据、平台代理、通知开关、轮询间隔、数据导入导出、主题、关于。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(vm: FollowersViewModel = androidx.lifecycle.viewmodel.compose.viewModel()) {
    val settings by vm.settings.collectAsState()
    val themeMode by ZhiboApp.instance.settings.themeMode.collectAsState(initial = "system")
    val scope = rememberCoroutineScope()

    var showProxyDialog by remember { mutableStateOf(false) }
    var pollInput by remember(settings.pollInterval) { mutableStateOf(settings.pollInterval.toString()) }

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

    fun savePollInterval() {
        val v = pollInput.trim().toIntOrNull() ?: return
        val clamped = v.coerceIn(5, 3600)
        val cfg = settings.copy(pollInterval = clamped)
        scope.launch {
            ZhiboApp.instance.settings.save(cfg)
            vm.notify("轮询间隔已保存为 ${clamped}s")
        }
    }

    fun toggleNotifications() {
        val cfg = settings.copy(notificationsEnabled = !settings.notificationsEnabled)
        scope.launch {
            ZhiboApp.instance.settings.save(cfg)
            vm.notify(if (cfg.notificationsEnabled) "开播通知已开启" else "开播通知已关闭")
        }
    }

    Scaffold(topBar = { TopAppBar(title = { Text("设置") }) }) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item { Text("凭据", style = MaterialTheme.typography.titleMedium) }
            item {
                val credStore = ZhiboApp.instance.credentials
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = {
                            ZhiboApp.instance.browserRequest.value = BrowserRequest.BilibiliLogin
                        }) { Text("B站登录采集${if (credStore.hasBilibiliCookie()) " ✓" else ""}") }
                        OutlinedButton(onClick = {
                            ZhiboApp.instance.browserRequest.value = BrowserRequest.Fs1Auth
                        }) { Text("FS1授权采集${if (credStore.hasFs1Auth()) " ✓" else ""}") }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { pickFs1Yaml.launch(arrayOf("*/*")) }) {
                            Text("FS1配置导入")
                        }
                    }
                }
            }

            item { HorizontalDivider() }
            item { Text("网络", style = MaterialTheme.typography.titleMedium) }
            item {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("平台代理", style = MaterialTheme.typography.bodyLarge)
                        Text(
                            "Twitch/YouTube 等需代理的平台，按平台设置",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    OutlinedButton(onClick = { showProxyDialog = true }) { Text("设置") }
                }
            }

            item { HorizontalDivider() }
            item { Text("监控", style = MaterialTheme.typography.titleMedium) }
            item {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("开播通知", style = MaterialTheme.typography.bodyLarge)
                        Text(
                            "检测到开播时弹出系统通知",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = settings.notificationsEnabled,
                        onCheckedChange = { toggleNotifications() },
                    )
                }
            }
            item {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("轮询间隔（秒）", style = MaterialTheme.typography.bodyLarge)
                        Text("安全范围 5–3600", style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    OutlinedTextField(
                        value = pollInput,
                        onValueChange = { pollInput = it.filter { c -> c.isDigit() }.take(5) },
                        singleLine = true,
                        modifier = Modifier.width(110.dp),
                    )
                    Button(onClick = { savePollInterval() }) { Text("保存") }
                }
            }

            item { HorizontalDivider() }
            item { Text("数据", style = MaterialTheme.typography.titleMedium) }
            item {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = {
                            pickFollowersCsv.launch(
                                arrayOf("text/*", "text/csv", "application/csv", "text/comma-separated-values"),
                            )
                        }) { Text("导入关注") }
                        OutlinedButton(onClick = { saveFollowersCsv.launch("followers.csv") }) {
                            Text("导出关注")
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = {
                            pickSettingsCsv.launch(
                                arrayOf("text/*", "text/csv", "application/csv", "text/comma-separated-values"),
                            )
                        }) { Text("导入设置") }
                        OutlinedButton(onClick = { saveSettingsCsv.launch("settings.csv") }) {
                            Text("导出设置")
                        }
                    }
                }
            }

            item { HorizontalDivider() }
            item { Text("主题", style = MaterialTheme.typography.titleMedium) }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(
                        "system" to "跟随系统",
                        "light" to "浅色",
                        "dark" to "深色",
                    ).forEach { (value, label) ->
                        FilterChip(
                            selected = themeMode == value,
                            onClick = {
                                scope.launch { ZhiboApp.instance.settings.setThemeMode(value) }
                            },
                            label = { Text(label) },
                        )
                    }
                }
            }

            item { HorizontalDivider() }
            item {
                Text(
                    "直播监控 Android · v0.1.0-m4 · 与桌面版 CSV 互通",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
