package com.determinantmatrix.zhibo

import android.content.pm.ActivityInfo
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.BrightnessHigh
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Fullscreen
import androidx.compose.material.icons.filled.FullscreenExit
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PictureInPictureAlt
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.VolumeDown
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.ui.PlayerView
import com.determinantmatrix.zhibo.core.resolver.QualityOptions
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * 播放页：视频满宽无边距 + 自建控件层（播放/暂停、音量、亮度、画中画、全屏）。
 * 全屏 = 横屏铺满整屏；其他播放器在后台继续出声。
 */
@Composable
fun PlayerScreen(manager: PlayerManager) {
    val foreground by manager.foreground.collectAsState()
    val handles by manager.handles.collectAsState()
    val message by manager.message.collectAsState()
    val activity = LocalContext.current as? MainActivity
    val clickScope = rememberCoroutineScope()

    val handle = foreground ?: run {
        Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
            Text("没有正在播放的直播间")
            OutlinedButton(onClick = { manager.showList() }, modifier = Modifier.padding(top = 8.dp)) {
                Text("返回列表")
            }
        }
        return
    }

    var fullscreen by remember { mutableStateOf(false) }
    var controlsVisible by remember { mutableStateOf(true) }
    var playing by remember { mutableStateOf(handle.player.isPlaying) }
    var volume by remember { mutableFloatStateOf(handle.player.volume) }
    var brightness by remember { mutableFloatStateOf(0.5f) }

    LaunchedEffect(handle.player) {
        while (true) {
            playing = handle.player.isPlaying
            delay(500)
        }
    }
    LaunchedEffect(controlsVisible) {
        if (controlsVisible) {
            delay(4000)
            controlsVisible = false
        }
    }

    fun exitFullscreen() {
        fullscreen = false
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
    }

    fun enterFullscreen() {
        fullscreen = true
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
    }

    fun setVolume(value: Float) {
        volume = value.coerceIn(0f, 1f)
        handle.player.volume = volume
    }

    fun setBrightness(value: Float) {
        brightness = value.coerceIn(0.02f, 1f)
        activity?.window?.let { window ->
            window.attributes = window.attributes.apply { screenBrightness = brightness }
        }
    }

    BackHandler(enabled = fullscreen) { exitFullscreen() }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        Column(modifier = Modifier.fillMaxSize()) {
            // 视频区：竖屏满宽 16:9，全屏时铺满整屏
            Box(
                modifier = Modifier
                    .then(
                        if (fullscreen) {
                            Modifier.fillMaxSize()
                        } else {
                            Modifier.fillMaxWidth().aspectRatio(16f / 9f)
                        },
                    )
                    .clickable { controlsVisible = !controlsVisible },
            ) {
                AndroidView(
                    factory = { ctx ->
                        PlayerView(ctx).apply {
                            useController = false
                            setBackgroundColor(android.graphics.Color.BLACK)
                            player = handle.player
                        }
                    },
                    update = { view -> view.player = handle.player },
                    modifier = Modifier.fillMaxSize(),
                )

                if (controlsVisible) {
                    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.25f)))
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .align(Alignment.TopCenter)
                            .windowInsetsPadding(WindowInsets.statusBars)
                            .padding(horizontal = 8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        IconButton(onClick = { manager.showList() }) {
                            Icon(Icons.Filled.Close, "后台播放", tint = Color.White)
                        }
                        Row {
                            IconButton(onClick = { activity?.enterPictureInPictureModeSafely() }) {
                                Icon(Icons.Filled.PictureInPictureAlt, "画中画", tint = Color.White)
                            }
                            IconButton(onClick = {
                                if (fullscreen) exitFullscreen() else enterFullscreen()
                            }) {
                                Icon(
                                    if (fullscreen) Icons.Filled.FullscreenExit else Icons.Filled.Fullscreen,
                                    "全屏",
                                    tint = Color.White,
                                )
                            }
                        }
                    }
                    IconButton(
                        onClick = { if (playing) handle.player.pause() else handle.player.play() },
                        modifier = Modifier
                            .align(Alignment.Center)
                            .size(72.dp),
                    ) {
                        Icon(
                            if (playing) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                            "播放/暂停",
                            tint = Color.White,
                            modifier = Modifier.size(64.dp),
                        )
                    }
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .align(Alignment.BottomCenter)
                            .windowInsetsPadding(WindowInsets.navigationBars)
                            .padding(horizontal = 16.dp, vertical = 8.dp),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            IconButton(onClick = { setVolume(volume - 0.1f) }) {
                                Icon(Icons.Filled.VolumeDown, "音量减", tint = Color.White)
                            }
                            Slider(
                                value = volume,
                                onValueChange = ::setVolume,
                                modifier = Modifier.weight(1f),
                            )
                            IconButton(onClick = { setVolume(volume + 0.1f) }) {
                                Icon(Icons.Filled.VolumeUp, "音量加", tint = Color.White)
                            }
                            Text(
                                "${(volume * 100).toInt()}%",
                                color = Color.White,
                                style = MaterialTheme.typography.labelSmall,
                            )
                        }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            IconButton(onClick = { setBrightness(brightness - 0.1f) }) {
                                Icon(Icons.Filled.BrightnessHigh, "亮度减", tint = Color.White)
                            }
                            Slider(
                                value = brightness,
                                onValueChange = ::setBrightness,
                                modifier = Modifier.weight(1f),
                            )
                            IconButton(onClick = { setBrightness(brightness + 0.1f) }) {
                                Icon(Icons.Filled.BrightnessHigh, "亮度加", tint = Color.White)
                            }
                            Text(
                                "${(brightness * 100).toInt()}%",
                                color = Color.White,
                                style = MaterialTheme.typography.labelSmall,
                            )
                        }
                    }
                }
            }

            // 竖屏时的信息与操作区
            if (!fullscreen) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(MaterialTheme.colorScheme.background)
                        .verticalScroll(rememberScrollState())
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
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
                                OutlinedButton(onClick = {
                                    clickScope.launch { manager.switchQuality(handle, option.value) }
                                }) { Text(option.label) }
                            }
                        }
                    }

                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { manager.stop(handle) }) { Text("停止播放") }
                        OutlinedButton(onClick = { manager.showList() }) { Text("后台播放") }
                        OutlinedButton(onClick = {
                            ZhiboApp.instance.browserRequest.value =
                                BrowserRequest.Sniff(handle.followerUrl, handle.name)
                        }) { Text("网页嗅探兜底") }
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
        }
    }
}
