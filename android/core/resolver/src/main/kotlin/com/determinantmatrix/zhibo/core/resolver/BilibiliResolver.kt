package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.model.LiveInfo
import com.determinantmatrix.zhibo.core.network.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject

/** B 站直播 — 直译 streamget BilibiliLiveStream（免登录检测 + playUrl/getRoomPlayInfo 取流）。 */
class BilibiliResolver(private val http: Http, private val cookies: String = "") : LiveResolver {

    override val name = "bilibili"

    private val pcHeaders = mapOf(
        "user-agent" to "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
        "accept-language" to "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
        "cookie" to cookies.ifEmpty { "__ac_nonce=064caded4009deafd8b89;" },
        "origin" to "https://live.bilibili.com",
        "referer" to "https://live.bilibili.com/26066074",
    )

    private fun roomId(url: String): String = url.substringBefore("?").trimEnd('/').substringAfterLast('/')

    private fun jsonObj(element: kotlinx.serialization.json.JsonElement?): JsonObject? = element as? JsonObject

    override suspend fun checkLive(url: String, quality: String): LiveInfo = withContext(Dispatchers.IO) {
        val rid = roomId(url)
        val roomInit = Jsonx.parse(http.get("$API/room/v1/Room/room_init?id=$rid", pcHeaders))
        val data = Jsonx.obj(Jsonx.at(roomInit, "data"))
            ?: throw IllegalStateException("B站房间不存在或返回异常")
        val liveStatus = Jsonx.int(data["live_status"])
        val uid = Jsonx.str(data["uid"])

        val anchor = if (uid.isNotEmpty()) {
            runCatching {
                val master = Jsonx.parse(http.get("$API/live_user/v1/Master/info?uid=$uid", pcHeaders))
                Jsonx.str(Jsonx.at(master, "data", "info", "uname"))
            }.getOrDefault("")
        } else ""

        val title = runCatching {
            val h5 = Jsonx.parse(http.get("$API/xlive/web-room/v1/index/getH5InfoByRoom?room_id=$rid", pcHeaders))
            Jsonx.str(Jsonx.at(h5, "data", "room_info", "title"))
        }.getOrDefault("")

        LiveInfo(
            isLive = liveStatus == 1,
            anchorName = anchor,
            title = title,
            platform = "bilibili",
            extra = JsonObject(mapOf("room_id" to kotlinx.serialization.json.JsonPrimitive(rid))),
        )
    }

    override suspend fun getStreamUrl(url: String, quality: String): String = withContext(Dispatchers.IO) {
        val rid = roomId(url)
        val qn = Quality.bilibiliQn(quality)

        // 首选：playUrl（FLV durl 列表）
        val playParsed = runCatching {
            Jsonx.parse(http.get("$API/room/v1/Room/playUrl?cid=$rid&qn=$qn&platform=web", pcHeaders))
        }.getOrNull()
        if (playParsed != null && Jsonx.int(Jsonx.at(playParsed, "code")) == 0) {
            val urls = Jsonx.array(Jsonx.at(playParsed, "data", "durl"))
                .mapNotNull { d -> Jsonx.str(Jsonx.obj(d)?.get("url")) }
                .filter { it.isNotEmpty() }
            urls.firstOrNull { "d1--cn-gotcha" in it }?.let { return@withContext it }
            urls.lastOrNull()?.let { return@withContext it }
        }

        // 回退：getRoomPlayInfo（HLS）
        val query = mapOf(
            "room_id" to rid,
            "protocol" to "0,1",
            "format" to "0,1,2",
            "codec" to "0,1,2",
            "qn" to qn.toString(),
            "platform" to "web",
            "ptype" to "8",
            "dolby" to "5",
            "panorama" to "1",
            "hdr_type" to "0,1",
        ).entries.joinToString("&") { "${it.key}=${java.net.URLEncoder.encode(it.value, "UTF-8")}" }
        val info = Jsonx.parse(http.get("$API/xlive/web-room/v2/index/getRoomPlayInfo?$query", pcHeaders))
        if (Jsonx.int(Jsonx.at(info, "data", "live_status")) == 0) throw IllegalStateException("未开播")

        val stream = Jsonx.array(Jsonx.at(info, "data", "playurl_info", "playurl", "stream"))
            .firstOrNull()?.let { Jsonx.obj(it) }
        val format = stream?.get("format")?.let { Jsonx.array(it) }?.firstOrNull()?.let { Jsonx.obj(it) }
        val codecs = format?.get("codec")?.let { Jsonx.array(it) }
            ?.mapNotNull { Jsonx.obj(it) }
            ?.sortedByDescending { Jsonx.int(it["current_qn"]) }
            ?: emptyList()
        if (codecs.isEmpty()) throw IllegalStateException("取流失败：无可用编码")

        val options = listOf(10000, 400, 250, 150, 80)
        val index = options.indexOf(qn).coerceAtLeast(0).coerceAtMost(codecs.size - 1)
        val codec = codecs[index]
        val urlInfo = codec.get("url_info")?.let { Jsonx.array(it) }?.firstOrNull()?.let { Jsonx.obj(it) }
            ?: throw IllegalStateException("取流失败：url_info 缺失")
        Jsonx.str(urlInfo["host"]) + Jsonx.str(codec["base_url"]) + Jsonx.str(urlInfo["extra"])
    }

    companion object {
        private const val API = "https://api.live.bilibili.com"
    }
}
