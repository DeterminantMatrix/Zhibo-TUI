package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.model.LiveInfo
import com.determinantmatrix.zhibo.core.network.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * 抖音 — 直译 streamget DouyinLiveStream 的 web 路径：
 * webcast/room/web/enter + a_bogus 签名（DouyinAbSign）。
 * 风控较强：失败时引擎会标异常，可退浏览器嗅探兜底。
 */
class DouyinResolver(private val http: Http) : LiveResolver {

    override val name = "douyin"

    private val headers = mapOf(
        "referer" to "https://live.douyin.com/335354047186",
        "user-agent" to "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/123.0.0.0 Safari/537.36",
        "cookie" to TTWID_COOKIE,
    )

    private fun webRid(url: String): String = url.substringBefore("?").trimEnd('/').substringAfterLast('/')

    override suspend fun checkLive(url: String, quality: String, extra: JsonObject): LiveInfo =
        withContext(Dispatchers.IO) {
            val rid = webRid(url)
            val params = linkedMapOf(
                "aid" to "6383",
                "app_name" to "douyin_web",
                "live_id" to "1",
                "device_platform" to "web",
                "language" to "zh-CN",
                "browser_language" to "zh-CN",
                "browser_platform" to "Win32",
                "browser_name" to "Chrome",
                "browser_version" to "116.0.0.0",
                "web_rid" to rid,
                "is_need_double_stream" to "false",
                "msToken" to "",
            )
            val query = params.entries.joinToString("&") { (key, value) ->
                java.net.URLEncoder.encode(key, "UTF-8") + "=" + java.net.URLEncoder.encode(value, "UTF-8")
            }
            val bogus = DouyinAbSign.abSign(query, UA_123)
            val body = http.get("https://live.douyin.com/webcast/room/web/enter/?$query&a_bogus=$bogus", headers)
            val parsed = Jsonx.parse(body)
            val data = Jsonx.obj(Jsonx.at(parsed, "data")) ?: throw IllegalStateException("抖音返回异常")

            val roomList = Jsonx.array(data["data"])
            if (roomList.isEmpty()) {
                val prompts = Jsonx.str(data["prompts"])
                throw IllegalStateException(prompts.ifEmpty { "抖音房间数据为空" })
            }
            val room = Jsonx.obj(roomList[0]) ?: throw IllegalStateException("抖音房间数据异常")
            val anchor = Jsonx.str(Jsonx.at(data, "user", "nickname"))
            val status = Jsonx.int(room["status"])

            if (status != 2) {
                return@withContext LiveInfo(
                    isLive = false,
                    anchorName = anchor,
                    platform = "douyin",
                    extra = JsonObject(mapOf("web_rid" to JsonPrimitive(rid))),
                )
            }

            // origin 流：live_core_sdk_data.pull_data.stream_data → data.origin.main
            val streamUrl = runCatching {
                val streamData = Jsonx.str(
                    Jsonx.at(room, "stream_url", "live_core_sdk_data", "pull_data", "stream_data"),
                )
                val sd = Jsonx.obj(Jsonx.parse(streamData))
                val main = Jsonx.obj(Jsonx.at(sd, "data", "origin", "main"))!!
                val codec = Jsonx.str(Jsonx.obj(Jsonx.parse(Jsonx.str(main["sdk_params"])))?.get("VCodec"))
                val hls = Jsonx.str(main["hls"])
                val flv = Jsonx.str(main["flv"])
                (if (hls.isNotEmpty()) "$hls&codec=$codec" else "") to (if (flv.isNotEmpty()) "$flv&codec=$codec" else "")
            }.getOrElse { "" to "" }
            val hls = streamUrl.first
            val flv = streamUrl.second

            LiveInfo(
                isLive = true,
                anchorName = anchor,
                title = Jsonx.str(room["title"]),
                m3u8Url = hls,
                flvUrl = flv,
                streamUrl = hls.ifEmpty { flv },
                qualityName = "原画",
                platform = "douyin",
                extra = JsonObject(mapOf("web_rid" to JsonPrimitive(rid))),
            )
        }

    override suspend fun getStreamUrl(url: String, quality: String): String {
        val info = checkLive(url, quality)
        if (!info.isLive) throw IllegalStateException("主播未开播")
        return info.streamUrl.ifEmpty { throw IllegalStateException("无法获取流地址") }
    }

    companion object {
        private const val UA_123 = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

        /** streamget 内置的公开 ttwid（匿名可用的房间进入凭据）。 */
        private const val TTWID_COOKIE = "ttwid=1%7CmDcInbJ7AJ-2PGtsgrG4xj7SOiNMzePqQBF1LMO2Qkg" +
            "%7C1761107324%7Cbbf97c2cd9f8eae8e8c36db4ef50c323deaa4b161179170aaf659590867c162d"
    }
}
