package com.determinantmatrix.zhibo

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/**
 * 代理设置：按平台填 http://host:port，direct = 强制直连，留空 = 默认直连。
 * 与桌面 settings.csv 的 platform_proxy.* 键互通。
 */
@Composable
fun ProxyDialog(
    current: Map<String, String>,
    onSave: (Map<String, String>) -> Unit,
    onDismiss: () -> Unit,
) {
    val platforms = listOf("twitch", "youtube", "kick", "chzzk", "tiktok", "twitcasting")
    val values = remember(current) {
        platforms.associateWith { current[it].orEmpty() }.toMutableMap()
    }
    val scope = rememberCoroutineScope()
    val repo = ZhiboApp.instance.settings

    fun save() {
        val filtered = values.filterValues { it.isNotBlank() }
        scope.launch {
            repo.savePlatformProxies(filtered)
            ZhiboApp.instance.refreshResolvers(filtered)
            onDismiss()
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("平台代理设置") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    "格式 http://host:port；direct=强制直连；留空=默认直连。" +
                        "设置后立即对对应平台的检测与取流生效。",
                    style = MaterialTheme.typography.bodySmall,
                )
                platforms.forEach { platform ->
                    Row(
                        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(
                            platform,
                            style = MaterialTheme.typography.labelMedium,
                            modifier = Modifier.padding(end = 8.dp),
                        )
                        OutlinedTextField(
                            value = values[platform].orEmpty(),
                            onValueChange = { values[platform] = it },
                            placeholder = { Text("direct", style = MaterialTheme.typography.labelSmall) },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { save() }) { Text("保存") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}
