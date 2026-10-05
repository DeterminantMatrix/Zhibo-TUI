package com.determinantmatrix.zhibo.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.resolver.QualityOptions

/**
 * 编辑关注项：名称 / URL / 主插件 / 画质 / 标签 / 启用（FS1 另有 sport_id）。
 * 保存写回 Room 并即时生效（监控下一轮按新值检测）。
 */
@Composable
fun EditFollowerDialog(
    initial: Follower,
    onDismiss: () -> Unit,
    onSave: (Follower) -> Unit,
) {
    var name by remember { mutableStateOf(initial.name) }
    var url by remember { mutableStateOf(initial.url) }
    var plugin by remember { mutableStateOf(initial.plugin) }
    var quality by remember { mutableStateOf(initial.quality) }
    var tagsText by remember { mutableStateOf(initial.tags.joinToString("|")) }
    var sportId by remember { mutableStateOf(initial.sportId) }
    var enabled by remember { mutableStateOf(initial.enabled) }
    var error by remember { mutableStateOf<String?>(null) }

    val pluginOptions = listOf("streamget", "fs1", "streamlink", "yt_dlp")
    val qualityOptions = QualityOptions.forPlatform(
        if (plugin == "fs1") "fs1" else initial.platform,
    )

    fun save() {
        if (name.isBlank()) { error = "名称不能为空"; return }
        if (url.isBlank()) { error = "地址不能为空"; return }
        val tags = tagsText.split("|", "，", ",").map { it.trim() }.filter { it.isNotEmpty() }
        val extra = LinkedHashMap(initial.extra)
        if (sportId.isNotBlank()) {
            extra[Follower.EXTRA_SPORT_ID] = kotlinx.serialization.json.JsonPrimitive(sportId.trim())
        }
        onSave(
            initial.copy(
                name = name.trim(),
                url = url.trim(),
                quality = quality,
                tags = tags.ifEmpty { listOf(Follower.DEFAULT_TAG) },
                extra = kotlinx.serialization.json.JsonObject(extra),
                enabled = enabled,
            ),
        )
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("编辑关注项") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("名称") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidthDp(),
                )
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it },
                    label = { Text(if (plugin == "fs1") "房间号（数字）" else "直播间地址") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidthDp(),
                )
                Text("主插件", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    pluginOptions.forEach { option ->
                        FilterChip(
                            selected = plugin == option,
                            onClick = { plugin = option },
                            label = { Text(option) },
                        )
                    }
                }
                Text("画质", style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    qualityOptions.forEach { option ->
                        FilterChip(
                            selected = quality.equals(option.value, ignoreCase = true),
                            onClick = { quality = option.value },
                            label = { Text(option.label) },
                        )
                    }
                }
                OutlinedTextField(
                    value = tagsText,
                    onValueChange = { tagsText = it },
                    label = { Text("标签（| 分隔）") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidthDp(),
                )
                if (plugin == "fs1") {
                    OutlinedTextField(
                        value = sportId,
                        onValueChange = { sportId = it.filter { c -> c.isDigit() }.take(8) },
                        label = { Text("sport_id") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidthDp(),
                    )
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text("启用", style = MaterialTheme.typography.bodyLarge)
                    Switch(checked = enabled, onCheckedChange = { enabled = it })
                    Text(
                        if (enabled) "参与轮询" else "不参与轮询",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                error?.let {
                    Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = { Button(onClick = { save() }) { Text("保存") } },
        dismissButton = {
            androidx.compose.material3.OutlinedButton(onClick = onDismiss) { Text("取消") }
        },
    )
}

private fun Modifier.fillMaxWidthDp(): Modifier = this.then(Modifier.fillMaxWidth())
