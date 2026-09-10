package com.determinantmatrix.zhibo.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.determinantmatrix.zhibo.ZhiboApp
import com.determinantmatrix.zhibo.startMonitoring
import com.determinantmatrix.zhibo.stopMonitoring

/**
 * 功能页：监控服务开关、播放管理（多路切换/停止）、数据导入导出。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FunctionsScreen() {
    val controller = ZhiboApp.instance.engineController
    val running by controller.running.collectAsState()
    val handles by ZhiboApp.instance.playerManager.handles.collectAsState()
    val foreground by ZhiboApp.instance.playerManager.foreground.collectAsState()
    val clickScope = androidx.compose.runtime.rememberCoroutineScope()

    Scaffold(topBar = { TopAppBar(title = { Text("功能") }) }) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .padding(padding)
                .padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item {
                Text("解析包", style = MaterialTheme.typography.titleMedium)
                Text(
                    "视频地址由 App 内置解析器直接获取，无需安装外部工具；" +
                        "解析逻辑随 App 更新升级。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(
                        modifier = Modifier.padding(10.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        ResolverRow("B站 / 斗鱼 / 虎牙", "内置 · 直连可用")
                        ResolverRow("抖音", "内置 · a_bogus 签名 · 直连可用")
                        ResolverRow("FS1（飞速直播）", "内置 · 需授权（设置 → FS1 配置导入/授权采集）")
                        ResolverRow("Twitch", "内置 · 需代理（设置 → 平台代理）")
                        ResolverRow("YouTube", "内置嗅探兜底 · 建议浏览器路径")
                    }
                }
            }
            item {
                Text("监控服务", style = MaterialTheme.typography.titleMedium)
            }
            item {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    if (running) {
                        Button(onClick = { stopMonitoring() }) { Text("停止监控") }
                    } else {
                        Button(onClick = { startMonitoring() }) { Text("开始监控") }
                    }
                    Text(
                        if (running) "运行中 · 常驻通知保持后台轮询" else "已停止",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            item {
                Text("播放管理", style = MaterialTheme.typography.titleMedium)
            }
            if (handles.isEmpty()) {
                item {
                    Text(
                        "没有正在播放的直播间。回到首页点开在线直播间即可播放（最多 3 路）。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                items(handles, key = { it.followerUrl }) { handle ->
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(handle.name, style = MaterialTheme.typography.bodyLarge)
                                Text(
                                    "${handle.platform} · ${handle.quality}" +
                                        if (foreground == handle) " · 前台中" else "",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            if (foreground != handle) {
                                Button(onClick = { ZhiboApp.instance.playerManager.show(handle) }) {
                                    Text("切到前台")
                                }
                            }
                            OutlinedButton(onClick = {
                                ZhiboApp.instance.playerManager.stop(handle)
                            }) { Text("停止") }
                        }
                    }
                }
            }
            item {
                Text("数据", style = MaterialTheme.typography.titleMedium)
                Text(
                    "关注与设置的导入导出已移至「设置」页。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun ResolverRow(name: String, statusText: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(name, style = MaterialTheme.typography.bodyMedium)
        Text(
            statusText,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
