package com.determinantmatrix.zhibo

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.network.Http
import com.determinantmatrix.zhibo.core.resolver.ResolverRegistry
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext

/**
 * 播放管理器 — 对齐桌面"最多 3 个直播间同时播放"。
 * 每个 PlayerHandle 一个 ExoPlayer 实例；前台只显示一个，其余继续出声。
 */
class PlayerManager(
    private val context: Context,
    private val registry: ResolverRegistry,
) {

    class PlayerHandle(
        val followerUrl: String,
        val name: String,
        val platform: String,
        var quality: String,
        val streamUrl: String,
        val player: ExoPlayer,
    )

    private val _handles = MutableStateFlow<List<PlayerHandle>>(emptyList())
    val handles: StateFlow<List<PlayerHandle>> = _handles

    private val _foreground = MutableStateFlow<PlayerHandle?>(null)
    val foreground: StateFlow<PlayerHandle?> = _foreground

    private val _message = MutableStateFlow("")
    val message: StateFlow<String> = _message

    private val http = Http()

    /** 前台切换：不新建播放器，只换显示焦点。 */
    fun show(handle: PlayerHandle) {
        _foreground.value = handle
    }

    /** 回列表页；其余播放器继续在后台播放。 */
    fun showList() {
        _foreground.value = null
    }

    suspend fun play(follower: Follower, quality: String = follower.quality) {
        val existing = _handles.value.firstOrNull { it.followerUrl == follower.url }
        if (existing != null) {
            _foreground.value = existing
            return
        }
        if (_handles.value.size >= MAX_PLAYERS) {
            _message.value = "最多 $MAX_PLAYERS 个直播间同时播放"
            return
        }
        _message.value = "正在获取播放地址…"
        val resolved = runCatching {
            val resolver = registry.get("streamget")
                ?: throw IllegalStateException("streamget 插件未注册")
            resolver.getStreamUrl(follower.url, quality)
        }
        val streamUrl = resolved.getOrElse { failure ->
            _message.value = "播放失败：${failure.message?.take(120)}"
            return
        }
        val handle = withContext(Dispatchers.Main) {
            val player = buildPlayer(streamUrl)
            PlayerHandle(
                followerUrl = follower.url,
                name = follower.name,
                platform = follower.platform,
                quality = quality,
                streamUrl = streamUrl,
                player = player,
            )
        }
        _handles.value = _handles.value + handle
        _foreground.value = handle
        _message.value = "${follower.name} 播放中（$quality）"
    }

    suspend fun switchQuality(handle: PlayerHandle, newQuality: String) {
        if (newQuality == handle.quality) return
        _message.value = "切换画质到 $newQuality…"
        val resolved = runCatching {
            registry.get("streamget")!!.getStreamUrl(handle.followerUrl, newQuality)
        }
        val newUrl = resolved.getOrElse { failure ->
            _message.value = "切换失败：${failure.message?.take(120)}"
            return
        }
        withContext(Dispatchers.Main) {
            handle.quality = newQuality
            handle.player.setMediaItem(buildMediaItem(newUrl))
            handle.player.prepare()
            handle.player.playWhenReady = true
        }
        _message.value = "${handle.name} 已切换到 $newQuality"
    }

    fun stop(handle: PlayerHandle) {
        withContextMain {
            handle.player.stop()
            handle.player.release()
        }
        _handles.value = _handles.value - handle
        if (_foreground.value == handle) _foreground.value = _handles.value.lastOrNull()
    }

    fun stopAll() {
        _handles.value.forEach { handle ->
            withContextMain {
                handle.player.stop()
                handle.player.release()
            }
        }
        _handles.value = emptyList()
        _foreground.value = null
    }

    private fun buildPlayer(streamUrl: String): ExoPlayer {
        val player = ExoPlayer.Builder(context)
            .setMediaSourceFactory(
                DefaultMediaSourceFactory(
                    DefaultDataSource.Factory(
                        context,
                        OkHttpDataSource.Factory(http.callFactory()),
                    ),
                ),
            )
            .build()
        player.setMediaItem(buildMediaItem(streamUrl))
        player.prepare()
        player.playWhenReady = true
        return player
    }

    private fun buildMediaItem(url: String): MediaItem {
        val path = url.substringBefore('?').lowercase()
        val mime = when {
            path.endsWith(".m3u8") -> MimeTypes.APPLICATION_M3U8
            // FLV 无专用常量：交给 DefaultExtractorsFactory 按内容嗅探（含 FlvExtractor）
            else -> null
        }
        val builder = MediaItem.Builder().setUri(url)
        mime?.let { builder.setMimeType(it) }
        return builder.build()
    }

    private fun withContextMain(block: () -> Unit) {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) block()
        else android.os.Handler(android.os.Looper.getMainLooper()).post(block)
    }

    companion object {
        const val MAX_PLAYERS = 3
    }
}
