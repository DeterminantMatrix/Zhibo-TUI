package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.model.LiveInfo
import com.determinantmatrix.zhibo.core.network.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Twitch — 直译 streamget TwitchLiveStream：
 * GQL（ComscoreStreamingQuery 检测 + PlaybackAccessToken 取流）+ usher m3u8 清单。
 * 中国网络需代理（由 registry 构建时注入带代理的 Http）。
 */
class TwitchResolver(private val http: Http) : LiveResolver {

    override val name = "twitch"

    private val headers = mapOf(
        "user-agent" to "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
        "accept-language" to "en-US",
        "referer" to "https://www.twitch.tv/",
        "client-id" to "kimne78kx3ncx6brgo4mv6wki5h1ko",
        "device-id" to (0 until 16).map { "0123456789abcdefghijklmnopqrstuvwxyz"[(it * 7) % 36] }.joinToString(""),
    )

    private fun login(url: String): String = url.substringBefore("?").trimEnd('/').substringAfterLast('/')

    override suspend fun checkLive(url: String, quality: String, extra: kotlinx.serialization.json.JsonObject): LiveInfo =
        withContext(Dispatchers.IO) {
            val login = login(url)
            val gql = buildJsonArray {
                add(buildJsonObject {
                    put("operationName", "ComscoreStreamingQuery")
                    put("variables", buildJsonObject {
                        put("channel", login.lowercase())
                        put("clipSlug", "")
                        put("isClip", false)
                        put("isLive", true)
                        put("isVodOrCollection", false)
                        put("vodID", "")
                    })
                    put("extensions", buildJsonObject {
                        put("persistedQuery", buildJsonObject {
                            put("version", 1)
                            put("sha256Hash", COMSCORE_HASH)
                        })
                    })
                })
            }
            val body = http.postJson(
                "$GQL",
                headers + mapOf("content-type" to "text/plain;charset=UTF-8"),
                gql.toString(),
            )
            val parsed = Jsonx.parse(body)
            val user = Jsonx.obj(Jsonx.at(parsed, "0", "data", "user"))
                ?: throw IllegalStateException("Twitch 频道不存在")
            val isLive = !Jsonx.isNull(user["stream"])
            val title = Jsonx.str(Jsonx.at(user, "broadcastSettings", "title"))
            LiveInfo(
                isLive = isLive,
                anchorName = Jsonx.str(user["displayName"]) + "-" + login,
                title = title,
                platform = "twitch",
            )
        }

    override suspend fun getStreamUrl(url: String, quality: String): String = withContext(Dispatchers.IO) {
        val login = login(url)
        val tokenQuery = buildJsonObject {
            put("operationName", "PlaybackAccessToken_Template")
            put(
                "query",
                "query PlaybackAccessToken_Template(\$login: String!, \$isLive: Boolean!, \$vodID: ID!, " +
                    "\$isVod: Boolean!, \$playerType: String!) {  streamPlaybackAccessToken(channelName: \$login, " +
                    "params: {platform: \"web\", playerBackend: \"mediaplayer\", playerType: \$playerType}) " +
                    "@include(if: \$isLive) {    value    signature   authorization { isForbidden forbiddenReasonCode }" +
                    "   __typename  }  videoPlaybackAccessToken(id: \$vodID, params: {platform: \"web\", " +
                    "playerBackend: \"mediaplayer\", playerType: \$playerType}) @include(if: \$isVod) {    value   " +
                    " signature   __typename  }}",
            )
            put("variables", buildJsonObject {
                put("isLive", true)
                put("login", login)
                put("isVod", false)
                put("vodID", "")
                put("playerType", "site")
            })
        }
        val body = http.postJson(GQL, headers, tokenQuery.toString())
        val parsed = Jsonx.parse(body)
        val token = Jsonx.str(Jsonx.at(parsed, "data", "streamPlaybackAccessToken", "value"))
        val sign = Jsonx.str(Jsonx.at(parsed, "data", "streamPlaybackAccessToken", "signature"))
        if (token.isEmpty()) throw IllegalStateException("Twitch 取流失败：无 token")

        val params = linkedMapOf(
            "acmb" to "e30=",
            "allow_audio_only" to "true",
            "allow_source" to "true",
            "browser_family" to "firefox",
            "browser_version" to "124.0",
            "cdm" to "wv",
            "fast_bread" to "true",
            "os_name" to "Windows",
            "os_version" to "NT%2010.0",
            "p" to "3553732",
            "platform" to "web",
            "play_session_id" to "bdd22331a986c7f1073628f2fc5b19da",
            "player_backend" to "mediaplayer",
            "player_version" to "1.28.0-rc.1",
            "playlist_include_framerate" to "true",
            "reassignments_supported" to "true",
            "sig" to sign,
            "token" to token,
            "transcode_mode" to "cbr_v1",
        )
        val query = params.entries.joinToString("&") {
            java.net.URLEncoder.encode(it.key, "UTF-8") + "=" + java.net.URLEncoder.encode(it.value, "UTF-8")
        }
        val m3u8 = "https://usher.ttvnw.net/api/channel/hls/$login.m3u8?$query"
        val playlist = http.get(m3u8, headers)
        pickHighest(playlist) ?: throw IllegalStateException("Twitch 播放清单为空")
    }

    /** 主清单解析：取带宽最高的子播放列表 URL。 */
    internal fun pickHighest(playlist: String): String {
        var bandwidth = 0
        var best: String? = null
        var pending = false
        for (raw in playlist.lineSequence()) {
            val line = raw.trim()
            when {
                line.startsWith("#EXT-X-STREAM-INF:") -> {
                    pending = true
                    bandwidth = Regex("BANDWIDTH=(\\d+)").find(line)?.groupValues?.get(1)?.toIntOrNull() ?: 0
                }
                line.startsWith("https://") && pending -> {
                    if (best == null && !line.contains("_audio_only")) best = line
                    pending = false
                }
            }
        }
        return best ?: throw IllegalStateException("Twitch 清单无可用流")
    }

    companion object {
        private const val GQL = "https://gql.twitch.tv/gql"
        private const val COMSCORE_HASH = "e1edae8122517d013405f237ffcc124515dc6ded82480a88daef69c83b53ac01"
    }
}
