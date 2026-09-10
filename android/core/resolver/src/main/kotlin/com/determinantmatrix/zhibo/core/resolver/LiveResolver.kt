package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.model.LiveInfo
import kotlinx.serialization.json.JsonObject

/** 直播流解析器 — 对应桌面 LiveStreamPlugin。实现必须线程安全。 */
interface LiveResolver {
    val name: String

    /** 检测直播间状态；网络/解析失败应抛异常，由引擎统一归类。extra 为关注项扩展字段。 */
    suspend fun checkLive(url: String, quality: String = "best", extra: JsonObject = JsonObject(emptyMap())): LiveInfo

    /** 获取播放地址；未开播或失败抛异常。 */
    suspend fun getStreamUrl(url: String, quality: String = "best"): String
}

/** 未知插件 / 未支持平台的占位实现。 */
class UnsupportedResolver(override val name: String, private val reason: String) : LiveResolver {
    override suspend fun checkLive(url: String, quality: String, extra: JsonObject): LiveInfo =
        throw UnsupportedOperationException(reason)

    override suspend fun getStreamUrl(url: String, quality: String): String =
        throw UnsupportedOperationException(reason)
}

/** 插件注册表 — 对应桌面 discover_plugins/get_plugin。 */
class ResolverRegistry(resolvers: List<LiveResolver>) {

    private val map: Map<String, LiveResolver> =
        resolvers.associateBy { it.name }

    fun get(name: String): LiveResolver? = map[name.lowercase().trim()]

    fun names(): List<String> = map.keys.toList()
}

object FallbackChain {

    /** 平台默认回退 — 对齐桌面 _platform_fallback_plugins。 */
    fun platformDefaults(platform: String): List<String> = when (platform.trim().lowercase()) {
        "twitch" -> listOf("streamget", "streamlink")
        "youtube" -> listOf("yt_dlp", "streamget", "streamlink")
        else -> emptyList()
    }

    /** 主插件 + 关注项回退 + 平台默认，去重保序。 */
    fun forFollower(plugin: String, fallbackPlugins: List<String>, platform: String): List<String> {
        val chain = mutableListOf(plugin.lowercase().trim())
        fallbackPlugins.forEach { p ->
            val key = p.lowercase().trim()
            if (key.isNotEmpty() && key !in chain) chain.add(key)
        }
        platformDefaults(platform).forEach { p ->
            if (p !in chain) chain.add(p)
        }
        return chain
    }
}
