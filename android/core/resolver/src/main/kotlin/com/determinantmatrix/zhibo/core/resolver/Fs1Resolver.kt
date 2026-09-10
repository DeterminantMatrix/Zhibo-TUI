package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.model.LiveInfo
import com.determinantmatrix.zhibo.core.network.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject

/** FS1 授权配置 — 字段与桌面 rooms.yaml 的 config 段一致。 */
data class Fs1Auth(
    val siteUrl: String = Fs1Trust.DEFAULT_SITE_URL,
    val apiUrl: String = Fs1Trust.DEFAULT_API_URL,
    val playApiUrl: String = Fs1Trust.DEFAULT_PLAY_API_URL,
    val token: String,
    val apiVersion: String = "8",
    val version: String = "1.8.4",
    val imei: String = "",
    val dunImei: String = "",
    val userAgent: String = DEFAULT_UA,
) {
    fun toJson(): JsonObject = buildJsonObject {
        put("site_url", JsonPrimitive(siteUrl))
        put("api_url", JsonPrimitive(apiUrl))
        put("play_api_url", JsonPrimitive(playApiUrl))
        put("token", JsonPrimitive(token))
        put("api_version", JsonPrimitive(apiVersion))
        put("version", JsonPrimitive(version))
        put("imei", JsonPrimitive(imei))
        put("dun_imei", JsonPrimitive(dunImei))
        put("user_agent", JsonPrimitive(userAgent))
    }

    companion object {
        const val DEFAULT_UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"

        private val json = Json

        fun fromJson(element: JsonObject): Fs1Auth? {
            val token = (element["token"] as? JsonPrimitive)?.content.orEmpty()
            if (token.isEmpty()) return null
            fun str(key: String, fallback: String = ""): String =
                (element[key] as? JsonPrimitive)?.content?.takeIf { it.isNotEmpty() } ?: fallback
            return Fs1Auth(
                siteUrl = str("site_url", Fs1Trust.DEFAULT_SITE_URL),
                apiUrl = str("api_url", Fs1Trust.DEFAULT_API_URL),
                playApiUrl = str("play_api_url", Fs1Trust.DEFAULT_PLAY_API_URL),
                token = token,
                apiVersion = str("api_version", "8"),
                version = str("version", "1.8.4"),
                imei = str("imei"),
                dunImei = str("dun_imei"),
                userAgent = str("user_agent", DEFAULT_UA),
            )
        }

        fun fromJsonText(text: String): Fs1Auth? = runCatching {
            fromJson(json.parseToJsonElement(text).jsonObject)
        }.getOrNull()
    }
}

/** FS1 授权来源 — 由凭据仓库实现（每次请求前读取，授权更新即时生效）。 */
fun interface Fs1AuthProvider {
    fun current(): Fs1Auth?
}

/**
 * FS1（飞速直播）解析器 — 直译桌面 Fs1Plugin：房间 API + 播放签名 API（AES 解密）。
 * URL 列即数字房间号；sport_id 取关注项 extra。
 */
class Fs1Resolver(
    private val http: Http,
    private val authProvider: Fs1AuthProvider,
) : LiveResolver {

    override val name = "fs1"

    private fun headers(auth: Fs1Auth): Map<String, String> = mapOf(
        "Accept" to "application/json, text/plain, */*",
        "Accept-Language" to "zh-CN,zh;q=0.9",
        "Origin" to auth.siteUrl,
        "Referer" to auth.siteUrl + "/",
        "User-Agent" to auth.userAgent,
        "api-version" to auth.apiVersion,
        "version" to auth.version,
        "authorization" to auth.token,
        "device" to "3",
        "imei" to auth.imei,
        "dun-imei" to auth.dunImei,
    )

    private fun playHeaders(auth: Fs1Auth): Map<String, String> =
        headers(auth) + mapOf(
            "device2" to "3",
            "platform" to "fszb",
            "Content-Type" to "application/x-www-form-urlencoded;charset=UTF-8",
        )

    override suspend fun checkLive(url: String, quality: String, extra: JsonObject): LiveInfo = withContext(Dispatchers.IO) {
        val auth = authProvider.current()
            ?: throw IllegalStateException("尚未配置 FS1 授权，请先在“FS1 授权采集”中登录并抓取")
        if (!Fs1Trust.isTrustedApi(auth.apiUrl)) throw IllegalStateException("FS1 API 地址未通过信任校验")
        val roomId = url.trim()
        val sportId = (extra["sport_id"] as? JsonPrimitive)?.content?.takeIf { it.isNotEmpty() } ?: "1"

        val resp = http.get("${auth.apiUrl}?room_id=$roomId&sport_id=$sportId", headers(auth))
        val body = Jsonx.parse(resp)
        if (Jsonx.int(Jsonx.at(body, "code")) != 200) {
            throw IllegalStateException(Jsonx.str(Jsonx.at(body, "message")).ifEmpty { "FS1 API 错误" })
        }
        val data = Jsonx.obj(Jsonx.at(body, "data")) ?: throw IllegalStateException("FS1 返回缺 data")
        val isLive = Jsonx.str(data["room_status"]) == "2"

        // 画质选择：指定档位优先，其次默认优先级
        val priority = if (quality != "best") listOf(quality) + QUALITY_PRIORITY else QUALITY_PRIORITY
        val playFlow = Jsonx.array(data["play_flow"]).mapNotNull { Jsonx.obj(it) }
        var selectedUrl = ""
        var selectedName = ""
        var selectedCodeId = ""
        for (codeId in priority) {
            val q = playFlow.firstOrNull { Jsonx.str(it["code_id"]) == codeId }
            if (q != null) {
                selectedUrl = Jsonx.str(q["play_url"])
                selectedName = Jsonx.str(q["name"])
                selectedCodeId = codeId
                break
            }
        }
        if (selectedUrl.isEmpty()) {
            selectedUrl = Jsonx.str(data["pull_url"]).ifEmpty { Jsonx.str(data["pull_flv_url"]) }
            if (selectedName.isEmpty()) selectedName = "原画"
        }

        if (isLive && selectedUrl.isEmpty() && selectedCodeId.isNotEmpty()) {
            selectedUrl = fetchPlayUrl(auth, roomId, selectedCodeId, data, sportId)
        }

        LiveInfo(
            isLive = isLive,
            anchorName = Jsonx.str(Jsonx.at(data, "anchor_info", "nickname")),
            title = Jsonx.str(data["room_title"]),
            streamUrl = selectedUrl,
            m3u8Url = Jsonx.str(data["pull_url"]),
            flvUrl = Jsonx.str(data["pull_flv_url"]),
            qualityName = selectedName,
            platform = "fs1",
            extra = buildJsonObject {
                put("home", JsonPrimitive(Jsonx.str(Jsonx.at(data, "match_info", "home_name"))))
                put("away", JsonPrimitive(Jsonx.str(Jsonx.at(data, "match_info", "away_name"))))
                put("room_id", JsonPrimitive(Jsonx.str(data["room_id"]).ifEmpty { roomId }))
                put("sport_id", JsonPrimitive(sportId))
            },
        )
    }

    override suspend fun getStreamUrl(url: String, quality: String): String {
        val info = checkLive(url, quality)
        if (!info.isLive) throw IllegalStateException("主播未开播")
        return info.streamUrl.ifEmpty { throw IllegalStateException("无法获取流地址") }
    }

    private fun fetchPlayUrl(auth: Fs1Auth, roomId: String, codeId: String, data: JsonObject, sportId: String): String {
        if (!Fs1Trust.isTrustedPlayApi(auth.playApiUrl)) {
            throw IllegalStateException("FS1 播放 API 地址未通过信任校验")
        }
        val params = linkedMapOf(
            "room_id" to roomId,
            "code_id" to codeId,
            "time" to (System.currentTimeMillis() / 1000).toString(),
        )
        if (roomId == "888888888") {
            Jsonx.str(Jsonx.at(data, "match_info", "match_id")).takeIf { it.isNotEmpty() }?.let {
                params["match_id"] = it
            }
            params["sport_id"] = sportId
        }
        params["signature"] = Fs1Crypto.signParams(params)
        val resp = http.postForm(auth.playApiUrl, playHeaders(auth), params)
        val decrypted = Jsonx.parse(Fs1Crypto.decryptPlayResponse(resp.trim().trim('"')))
        if (Jsonx.int(Jsonx.at(decrypted, "code")) != 200) {
            throw IllegalStateException(Jsonx.str(Jsonx.at(decrypted, "message")).ifEmpty { "播放 API 错误" })
        }
        return Jsonx.str(Jsonx.at(decrypted, "data", "play_url"))
    }

    companion object {
        val QUALITY_PRIORITY = listOf("lgzm", "gqzm", "bqzm")
    }
}
