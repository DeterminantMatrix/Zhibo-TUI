package com.determinantmatrix.zhibo

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.ui.PlayerView
import com.determinantmatrix.zhibo.core.resolver.QualityOptions
import kotlinx.coroutines.launch

/**
 * 播放页：PlayerView 全宽 16:9 + 换画质 + 画中画 + 停止。
 * 多实例：其他房间的播放器继续在后台出声。
 */
@Composable
fun PlayerScreen(manager: PlayerManager) {
    val foreground by manager.foreground.collectAsState()
    val handles by manager.handles.collectAsState()
    val message by manager.message.collectAsState()
    val context = LocalContext.current
    val activity = context as? MainActivity
    val clickScope = androidx.compose.runtime.rememberCoroutineScope()

    val handle = foreground ?: run {
        Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
            Text("没有正在播放的直播间")
            OutlinedButton(onClick = { manager.showList() }, modifier = Modifier.padding(top = 8.dp)) {
                Text("返回列表")
            }
        }
        return
    }

    BackHandler { manager.showList() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        AndroidView(
            factory = { ctx ->
                PlayerView(ctx).apply {
                    useController = true
                    player = handle.player
                }
            },
            update = { view -> view.player = handle.player },
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(16f / 9f),
        )
        DisposableEffect(handle.player) {
            onDispose { }
        }

        Text(handle.name, style = MaterialTheme.typography.titleMedium)
        Text(
            text = "画质 ${handle.quality} · ${handles.size}/${PlayerManager.MAX_PLAYERS} 路播放",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Text("画质", style = MaterialTheme.typography.labelLarge)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            QualityOptions.forPlatform(handle.platform).forEach { option ->
                val selected = option.value == handle.quality
                if (selected) {
                    Button(onClick = { }) { Text(option.label) }
                } else {
                    OutlinedButton(onClick = { clickScope.launch { manager.switchQuality(handle, option.value) } }) {
                        Text(option.label)
                    }
                }
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { activity?.enterPictureInPictureModeSafely() }) { Text("画中画") }
            OutlinedButton(onClick = { manager.stop(handle) }) { Text("停止播放") }
            OutlinedButton(onClick = { manager.showList() }) { Text("后台播放") }
        }

        if (message.isNotEmpty()) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = message,
                    modifier = Modifier.padding(10.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        if (handles.size > 1) {
            Text("其他播放中的直播间", style = MaterialTheme.typography.labelLarge)
            handles.filter { it != handle }.forEach { other ->
                OutlinedButton(onClick = { manager.show(other) }, modifier = Modifier.fillMaxWidth()) {
                    Text("${other.name} · ${other.quality}")
                }
            }
        }
    }
}
