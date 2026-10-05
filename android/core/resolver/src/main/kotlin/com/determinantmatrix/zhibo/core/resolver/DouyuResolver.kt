package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.model.LiveInfo
import com.determinantmatrix.zhibo.core.network.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.security.MessageDigest

/** 斗鱼直播 — 直译 streamget DouyuLiveStream（betard 检测 + 白名单密钥 getH5PlayV1 取流）。 */
class DouyuResolver(private val http: Http) : LiveResolver {

    override val name = "douyu"

    private val baseHeaders = mapOf(
        "user-agent" to USER_AGENT,
        "referer" to "https://www.douyu.com/",
    )

    private fun roomId(url: String): String {
        Regex("douyu.com/(\\d+)").find(url)?.let { return it.groupValues[1] }
        Regex("rid=(\\d+)").find(url)?.let { return it.groupValues[1] }
        // 慢路径：自定义直播间别名 → 抓移动端页面找 rid（仅数字直链用不到）
        throw IllegalArgumentException("无法从 URL 解析斗鱼房间号：$url")
    }

    override suspend fun checkLive(url: String, quality: String, extra: kotlinx.serialization.json.JsonObject): LiveInfo = withContext(Dispatchers.IO) {
        val rid = roomId(url)
        val body = http.get("https://www.douyu.com/betard/$rid", baseHeaders)
        val room = Jsonx.obj(Jsonx.at(Jsonx.parse(body), "room"))
            ?: throw IllegalStateException("斗鱼房间返回异常")
        val rawTitle = Jsonx.str(room["room_name"]).replace("&nbsp;", " ").trim()
        val hasContent = Jsonx.int(room["show_status"]) == 1
        val isLoop = Jsonx.int(room["videoLoop"]) == 1
        LiveInfo(
            isLive = hasContent,
            anchorName = Jsonx.str(room["nickname"]),
            title = if (isLoop) "【轮播】$rawTitle" else rawTitle,
            platform = "douyu",
            extra = kotlinx.serialization.json.JsonObject(
                mapOf("room_id" to kotlinx.serialization.json.JsonPrimitive(Jsonx.str(room["room_id"]))),
            ),
        )
    }

    override suspend fun getStreamUrl(url: String, quality: String): String = withContext(Dispatchers.IO) {
        val rid = roomId(url)
        val white = whiteKey()
        val ts = System.currentTimeMillis() / 1000
        val salt = if (!white.isSpecial) "$rid$ts" else ""
        var secret = white.randStr
        repeat(white.encTime) {
            secret = md5(secret + white.key)
        }
        val auth = md5(secret + white.key + salt)

        val form = linkedMapOf(
            "rate" to Quality.douyuRate(quality),
            "ver" to "219032101",
            "iar" to "0",
            "ive" to "0",
            "rid" to rid,
            "hevc" to "0",
            "fa" to "0",
            "sov" to "0",
            "enc_data" to white.encData,
            "tt" to ts.toString(),
            "did" to DEFAULT_DID,
            "auth" to auth,
        )
        val headers = baseHeaders + mapOf(
            "origin" to "https://www.douyu.com",
            "content-type" to "application/x-www-form-urlencoded",
        )
        val resp = http.postForm("https://playweb.douyucdn.cn/lapi/live/getH5PlayV1/$rid", headers, form)
        val parsed = Jsonx.parse(resp)
        if (Jsonx.int(parsed.let { Jsonx.at(it, "error") }) != 0) {
            throw IllegalStateException("斗鱼取流失败 error=${Jsonx.str(parsed.let { Jsonx.at(it, "error") })}")
        }
        val data = Jsonx.obj(Jsonx.at(parsed, "data")) ?: throw IllegalStateException("斗鱼取流失败：无 data")
        val rtmpUrl = Jsonx.str(data["rtmp_url"])
        val rtmpLive = Jsonx.str(data["rtmp_live"])
        if (rtmpUrl.isEmpty() || rtmpLive.isEmpty()) throw IllegalStateException("斗鱼取流失败：地址为空")
        "$rtmpUrl/$rtmpLive"
    }

    private class WhiteKey(
        val randStr: String,
        val key: String,
        val encTime: Int,
        val isSpecial: Boolean,
        val encData: String,
    )

    private fun whiteKey(): WhiteKey {
        val body = http.get(
            "https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption?did=$DEFAULT_DID",
            mapOf("user-agent" to USER_AGENT),
        )
        val parsed = Jsonx.parse(body)
        if (Jsonx.int(parsed.let { Jsonx.at(it, "error") }) != 0) {
            throw IllegalStateException("获取斗鱼白名单密钥失败")
        }
        val data = Jsonx.obj(Jsonx.at(parsed, "data")) ?: throw IllegalStateException("获取斗鱼白名单密钥失败")
        return WhiteKey(
            randStr = Jsonx.str(data["rand_str"]),
            key = Jsonx.str(data["key"]),
            encTime = Jsonx.int(data["enc_time"]),
            isSpecial = Jsonx.bool(data["is_special"]),
            encData = Jsonx.str(data["enc_data"]),
        )
    }

    private fun md5(text: String): String =
        MessageDigest.getInstance("MD5").digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

    companion object {
        private const val DEFAULT_DID = "10000000000000000000000000001501"
        private const val USER_AGENT =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }
}
