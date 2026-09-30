package com.determinantmatrix.zhibo

import android.content.pm.ActivityInfo
import android.net.wifi.WifiManager
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
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
import androidx.compose.material.icons.filled.Cast
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Fullscreen
import androidx.compose.material.icons.filled.FullscreenExit
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PictureInPictureAlt
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.VolumeOff
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import com.determinantmatrix.zhibo.core.network.DlnaCast
import com.determinantmatrix.zhibo.core.network.Http
import com.determinantmatrix.zhibo.core.resolver.QualityOptions
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.abs

/**
 * 播放页 — 经典直播播放器交互：
 * 左半屏上下滑调音量、右半屏上下滑调亮度；直播无进度条。
 * 底部按钮：码率信息 / 小窗 / 投屏(DLNA) / 音量(静音切换) / 暂停。
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
    var muted by remember { mutableStateOf(false) }
    var volumeBeforeMute by remember { mutableFloatStateOf(1f) }
    var brightness by remember { mutableFloatStateOf(0.5f) }
    var showStats by remember { mutableStateOf(false) }
    var showCast by remember { mutableStateOf(false) }
    var touchDownX by remember { mutableFloatStateOf(0f) }
    var touchDownY by remember { mutableFloatStateOf(0f) }
    var touchLastY by remember { mutableFloatStateOf(0f) }
    var touchDragging by remember { mutableStateOf(false) }
    var gestureType by remember { mutableStateOf<String?>(null) } // "volume" | "brightness"
    var gestureValue by remember { mutableFloatStateOf(0f) }
    var statsLine by remember { mutableStateOf("统计中…") }

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
    LaunchedEffect(showStats) {
        while (showStats) {
            val fmt = handle.player.videoFormat
            val res = if (fmt != null && fmt.width > 0) "${fmt.width}x${fmt.height}" else "未知"
            val codec = fmt?.codecs?.takeIf { it.isNotEmpty() } ?: "未知"
            val fps = fmt?.frameRate?.takeIf { it > 0 }?.toInt()?.toString() ?: "-"
            val totalMb = (handle.stats.totalBytes / 1024 / 1024).toInt()
            statsLine = "码率 ${handle.stats.kbps} kbps · 已加载 $totalMb MB · $res ${fps}fps · $codec"
            delay(600)
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
        muted = volume <= 0.001f
        handle.player.volume = volume
    }

    fun toggleMute() {
        if (muted) {
            muted = false
            volume = if (volumeBeforeMute <= 0.001f) 0.6f else volumeBeforeMute
        } else {
            volumeBeforeMute = if (volume > 0.001f) volume else 0.6f
            muted = true
            volume = 0f
        }
        handle.player.volume = volume
    }

    fun setBrightness(value: Float) {
        brightness = value.coerceIn(0.02f, 1f)
        activity?.window?.let { window ->
            window.attributes = window.attributes.apply { screenBrightness = brightness }
        }
    }

    fun togglePlay() {
        if (playing) handle.player.pause() else handle.player.play()
    }

    BackHandler(enabled = fullscreen) { exitFullscreen() }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        Column(modifier = Modifier.fillMaxSize()) {
            // 视频区：竖屏满宽 16:9，全屏铺满整屏
            Box(
                modifier = Modifier
                    .then(
                        if (fullscreen) {
                            Modifier.fillMaxSize()
                        } else {
                            Modifier.fillMaxWidth().aspectRatio(16f / 9f)
                        },
                    )
            ) {
                AndroidView(
                    factory = { ctx ->
                        PlayerView(ctx).apply {
                            useController = false
                            setBackgroundColor(android.graphics.Color.BLACK)
                            player = handle.player
                        }
                    },
                    update = { view ->
                        view.player = handle.player
                        // 经典交互状态机：左/右半屏上下滑调音量/亮度，轻点切控件层。
                        // 直接挂在 PlayerView 上 —— 覆写过的 onTouchEvent 会吞掉
                        // Compose 覆盖层的 pointerInput 事件。
                        val slop = android.view.ViewConfiguration.get(view.context).scaledTouchSlop
                        view.setOnTouchListener { view, event ->
                            when (event.actionMasked) {
                                android.view.MotionEvent.ACTION_DOWN -> {
                                    touchDownX = event.x
                                    touchLastY = event.y
                                    touchDragging = false
                                }
                                android.view.MotionEvent.ACTION_MOVE -> {
                                    val totalDy = event.y - touchDownY
                                    if (!touchDragging && abs(totalDy) > slop) {
                                        touchDragging = true
                                        gestureType = if (touchDownX < view.width / 2f) "volume" else "brightness"
                                    }
                                    if (touchDragging) {
                                        val dy = event.y - touchLastY
                                        val delta = -dy / view.height
                                        if (gestureType == "volume") setVolume(volume + delta) else setBrightness(brightness + delta)
                                        gestureValue = if (gestureType == "volume") volume else brightness
                                    }
                                    touchLastY = event.y
                                }
                                android.view.MotionEvent.ACTION_UP -> {
                                    if (!touchDragging) controlsVisible = !controlsVisible
                                    gestureType = null
                                }
                                android.view.MotionEvent.ACTION_CANCEL -> gestureType = null
                            }
                            true
                        }
                    },
                    modifier = Modifier.fillMaxSize(),
                )


                // 手势指示器
                gestureType?.let { type ->
                    Box(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .background(Color.Black.copy(alpha = 0.6f))
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                if (type == "volume") Icons.Filled.VolumeUp else Icons.Filled.BrightnessHigh,
                                contentDescription = null,
                                tint = Color.White,
                            )
                            Spacer(Modifier.size(8.dp))
                            Text(
                                "${(gestureValue * 100).toInt()}%",
                                color = Color.White,
                                style = MaterialTheme.typography.titleMedium,
                            )
                        }
                    }
                }

                // 控件层
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

                    // 码率信息面板
                    if (showStats) {
                        Card(
                            modifier = Modifier
                                .align(Alignment.TopStart)
                                .windowInsetsPadding(WindowInsets.statusBars)
                                .padding(start = 56.dp, top = 8.dp),
                        ) {
                            Text(
                                statsLine,
                                modifier = Modifier.padding(8.dp),
                                style = MaterialTheme.typography.labelSmall,
                                color = Color.White,
                            )
                        }
                    }

                    // 底部按钮：码率 / 小窗 / 投屏 / 音量 / 暂停
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .align(Alignment.BottomCenter)
                            .windowInsetsPadding(WindowInsets.navigationBars)
                            .padding(horizontal = 8.dp, vertical = 4.dp),
                        horizontalArrangement = Arrangement.SpaceEvenly,
                    ) {
                        PlayerBottomButton(
                            icon = { Icon(Icons.Filled.Info, "码率信息", tint = Color.White) },
                            label = "码率",
                            selected = showStats,
                            onClick = { showStats = !showStats },
                        )
                        PlayerBottomButton(
                            icon = { Icon(Icons.Filled.PictureInPictureAlt, "小窗播放", tint = Color.White) },
                            label = "小窗",
                            onClick = { activity?.enterPictureInPictureModeSafely() },
                        )
                        PlayerBottomButton(
                            icon = { Icon(Icons.Filled.Cast, "投屏", tint = Color.White) },
                            label = "投屏",
                            onClick = { showCast = true },
                        )
                        PlayerBottomButton(
                            icon = {
                                Icon(
                                    if (muted) Icons.Filled.VolumeOff else Icons.Filled.VolumeUp,
                                    "音量",
                                    tint = Color.White,
                                )
                            },
                            label = if (muted) "已静音" else "音量",
                            onClick = { toggleMute() },
                        )
                        PlayerBottomButton(
                            icon = {
                                Icon(
                                    if (playing) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                                    "暂停/播放",
                                    tint = Color.White,
                                )
                            },
                            label = if (playing) "暂停" else "播放",
                            onClick = { togglePlay() },
                        )
                    }
                }

                // 投屏对话框
                if (showCast) {
                    CastDialog(
                        streamUrl = handle.streamUrl,
                        title = handle.name,
                        onDismiss = { showCast = false },
                    )
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

@Composable
private fun PlayerBottomButton(
    icon: @Composable () -> Unit,
    label: String,
    selected: Boolean = false,
    onClick: () -> Unit,
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .clickable(onClick = onClick)
            .padding(horizontal = 6.dp, vertical = 2.dp),
    ) {
        icon()
        Text(
            label,
            color = if (selected) MaterialTheme.colorScheme.primary else Color.White,
            style = MaterialTheme.typography.labelSmall,
        )
    }
}

/** DLNA 投屏对话框：SSDP 扫描 + 手动 IP（模拟器组播不可用时的兜底）。 */
@Composable
private fun CastDialog(
    streamUrl: String,
    title: String,
    onDismiss: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var scanning by remember { mutableStateOf(false) }
    var devices by remember { mutableStateOf<List<DlnaCast.Renderer>>(emptyList()) }
    var scanHint by remember { mutableStateOf("正在扫描局域网设备…") }
    var manual by remember { mutableStateOf("") }
    var casting by remember { mutableStateOf<String?>(null) }

    fun scan() {
        scanning = true
        devices = emptyList()
        scanHint = "正在扫描局域网设备…"
        scope.launch {
            val lock = (context.getSystemService(WifiManager::class.java))?.createMulticastLock("zhibo_cast")
            lock?.setReferenceCounted(false)
            lock?.acquire()
            try {
                val found = DlnaCast.discover(Http())
                devices = found
                scanHint = if (found.isEmpty()) {
                    "未发现设备（模拟器不支持组播；真机可用，或在下方手动输入电视/盒子 IP:端口）"
                } else {
                    "发现 ${found.size} 台设备"
                }
            } catch (t: Throwable) {
                scanHint = "扫描失败：${t.message?.take(60)}"
            } finally {
                lock?.release()
                scanning = false
            }
        }
    }

    fun castTo(renderer: DlnaCast.Renderer) {
        casting = renderer.friendlyName
        scope.launch {
            try {
                kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                    DlnaCast.cast(Http(), renderer, streamUrl, title)
                }
                casting = null
                ZhiboApp.instance.playerManager.setMessage("已投屏到「${renderer.friendlyName}」")
                onDismiss()
            } catch (t: Throwable) {
                casting = null
                scanHint = "投屏失败：${t.message?.take(80)}"
            }
        }
    }

    fun castToManual(hostPort: String) {
        casting = hostPort
        scope.launch {
            try {
                val renderer = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                    // 优先解析设备描述拿真实 controlURL；失败则按常见路径约定兜底
                    DlnaCast.describe("http://$hostPort/description.xml", Http())
                        ?: DlnaCast.describe("http://$hostPort/", Http())
                        ?: DlnaCast.Renderer(
                            friendlyName = hostPort,
                            controlUrl = "http://$hostPort/upnp/control/AVTransport",
                            location = "http://$hostPort/",
                        )
                }
                kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                    DlnaCast.cast(Http(), renderer, streamUrl, title)
                }
                casting = null
                ZhiboApp.instance.playerManager.setMessage("已投屏到「${renderer.friendlyName}」")
                onDismiss()
            } catch (t: Throwable) {
                casting = null
                scanHint = "投屏失败：${t.message?.take(80)}"
            }
        }
    }

    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("投屏到局域网设备") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(scanHint, style = MaterialTheme.typography.bodySmall)
                devices.forEach { device ->
                    OutlinedButton(onClick = { castTo(device) }, modifier = Modifier.fillMaxWidth()) {
                        Text(device.friendlyName)
                    }
                }
                OutlinedTextField(
                    value = manual,
                    onValueChange = { manual = it },
                    label = { Text("手动输入 IP:端口") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedButton(
                    onClick = { castToManual(manual.trim()) },
                    enabled = manual.contains(':') && casting == null,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(if (casting == null) "投屏到该地址" else "正在投屏…")
                }
                OutlinedButton(onClick = { if (!scanning) scan() }, enabled = !scanning) {
                    Text(if (scanning) "扫描中…" else "重新扫描")
                }
            }
        },
        confirmButton = {
            OutlinedButton(onClick = onDismiss) { Text("关闭") }
        },
    )
}
