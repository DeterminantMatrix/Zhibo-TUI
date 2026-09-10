package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.model.LiveInfo

/**
 * streamget 门面 — 桌面端一个插件按平台内部分发；安卓同理。
 * 未支持平台抛 UnsupportedOperationException，由引擎按普通失败处理。
 */
class StreamgetResolver(
    private val bilibili: BilibiliResolver,
    private val douyu: DouyuResolver,
    private val huya: HuyaResolver,
    private val douyin: DouyinResolver,
) : LiveResolver {

    override val name = "streamget"

    override suspend fun checkLive(url: String, quality: String, extra: kotlinx.serialization.json.JsonObject): LiveInfo {
        return when (platformOf(url)) {
            "bilibili" -> bilibili.checkLive(url, quality)
            "douyu" -> douyu.checkLive(url, quality)
            "huya" -> huya.checkLive(url, quality)
            "douyin" -> douyin.checkLive(url, quality)
            else -> throw UnsupportedOperationException("安卓端暂不支持该平台的 streamget 检测：$url")
        }
    }

    override suspend fun getStreamUrl(url: String, quality: String): String = when (platformOf(url)) {
        "bilibili" -> bilibili.getStreamUrl(url, quality)
        "douyu" -> douyu.getStreamUrl(url, quality)
        "huya" -> huya.getStreamUrl(url, quality)
        "douyin" -> douyin.getStreamUrl(url, quality)
        else -> throw UnsupportedOperationException("安卓端暂不支持该平台的 streamget 取流：$url")
    }

    companion object {
        /** 平台推断 — 关注项 platform 列为空时兜底。 */
        fun platformOf(url: String): String {
            val host = runCatching { java.net.URI(url).host?.lowercase() ?: "" }.getOrDefault("")
            return when {
                "live.bilibili.com" in host || "b23.tv" in host -> "bilibili"
                host.endsWith("douyu.com") -> "douyu"
                host.endsWith("huya.com") -> "huya"
                host.endsWith("twitch.tv") -> "twitch"
                "youtube.com" in host || "youtu.be" in host -> "youtube"
                "douyin.com" in host -> "douyin"
                else -> ""
            }
        }
    }
}
