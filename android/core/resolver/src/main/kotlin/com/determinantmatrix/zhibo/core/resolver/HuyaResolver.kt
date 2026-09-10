package com.determinantmatrix.zhibo.core.resolver

import com.determinantmatrix.zhibo.core.model.LiveInfo
import com.determinantmatrix.zhibo.core.network.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import java.security.MessageDigest
import java.util.Base64
import kotlin.random.Random

/**
 * 虎牙直播 — 直译 streamget HuyaLiveStream：
 * mp.huya.com profileRoom 检测 + 反码重建（md5 签名）取流；lol 分类回退网页内嵌 stream JSON。
 */
class HuyaResolver(private val http: Http) : LiveResolver {

    override val name = "huya"

    private val pcHeaders = mapOf(
        "user-agent" to PC_UA,
        "accept-language" to "zh-CN,zh;q=0.9",
    )
    private val mobileHeaders = mapOf(
        "user-agent" to "ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))",
        "referer" to "https://servicewechat.com/wx74767bf0b684f7d3/301/page-frame.html",
        "accept-language" to "zh-CN,zh;q=0.9",
    )

    private fun roomIdFromUrl(url: String): String = url.substringBefore("?").trimEnd('/').substringAfterLast('/')

    private suspend fun resolveRoomId(url: String): String {
        val id = roomIdFromUrl(url)
        if (id.any { it.isLetter() }) {
            val html = http.get(url, mobileHeaders)
            Regex("\"ProfileRoom\":(\\d+)").find(html)?.let { return it.groupValues[1] }
            throw IllegalArgumentException("虎牙别名房间解析失败，请使用数字房间链接：$url")
        }
        return id
    }

    override suspend fun checkLive(url: String, quality: String): LiveInfo = withContext(Dispatchers.IO) {
        val rid = resolveRoomId(url)
        val api = "https://mp.huya.com/cache.php?m=Live&do=profileRoom&roomid=$rid&showSecret=1"
        val parsed = Jsonx.parse(http.get(api, pcHeaders))
        val data = Jsonx.obj(Jsonx.at(parsed, "data")) ?: throw IllegalStateException("虎牙房间返回异常")
        val anchor = Jsonx.str(Jsonx.at(data, "profileInfo", "nick"))
        val liveStatus = Jsonx.str(data["realLiveStatus"])

        if (liveStatus != "ON") {
            return@withContext LiveInfo(
                isLive = false,
                anchorName = anchor,
                platform = "huya",
                extra = roomIdExtra(rid),
            )
        }

        val introduction = Jsonx.str(Jsonx.at(data, "liveData", "introduction"))
        val gameHostName = Jsonx.str(Jsonx.at(data, "liveData", "gameHostName"))
        if (gameHostName == "lol") {
            // 桌面端对该分类回退网页抓取
            return@withContext scrapeWebStream(url, rid, anchor, introduction)
        }

        val streams = Jsonx.array(Jsonx.at(data, "stream", "baseSteamInfoList"))
            .mapNotNull { Jsonx.obj(it) }
        val (flv, m3u8) = pickStream(streams)
        LiveInfo(
            isLive = true,
            anchorName = anchor,
            title = introduction,
            m3u8Url = m3u8,
            flvUrl = flv,
            streamUrl = flv.ifEmpty { m3u8 },
            platform = "huya",
            extra = roomIdExtra(rid),
        )
    }

    override suspend fun getStreamUrl(url: String, quality: String): String {
        val info = checkLive(url, quality)
        if (!info.isLive) throw IllegalStateException("未开播")
        // 画质档位在 M2 播放页完善；当前返回检测到的最佳流
        return info.streamUrl
    }

    private data class StreamUrls(val flv: String, val m3u8: String)

    private fun pickStream(streams: List<JsonObject>): StreamUrls {
        val entries = streams.map { info ->
            val cdnType = Jsonx.str(info["sCdnType"])
            val streamName = Jsonx.str(info["sStreamName"])
            Triple(
                cdnType,
                "${Jsonx.str(info["sFlvUrl"])}/$streamName.flv?${Jsonx.str(info["sFlvAntiCode"])}",
                "${Jsonx.str(info["sHlsUrl"])}/$streamName.m3u8?${Jsonx.str(info["sHlsAntiCode"])}",
            )
        }
        val selected = entries.lastOrNull { it.first == "TX" } ?: entries.firstOrNull()
            ?: return StreamUrls("", "")
        var flv = selected.second
        var m3u8 = selected.third
        if (selected.first == "TX" || selected.first == "HW") {
            flv = flv.replace("&ctype=tars_mp", "&ctype=huya_webh5").replace("&fs=bhct", "&fs=bgct")
            m3u8 = m3u8.replace("&ctype=tars_mp", "&ctype=huya_webh5").replace("&fs=bhct", "&fs=bgct")
        }
        return StreamUrls(flv, m3u8)
    }

    /** 网页内嵌 stream JSON 兜底（对齐 fetch_web_stream_data）。 */
    private suspend fun scrapeWebStream(url: String, rid: String, anchor: String, fallbackTitle: String): LiveInfo {
        val html = http.get(url, pcHeaders)
        val match = Regex("stream: (\\{\"data\".*?),\"iWebDefaultBitRate\"").find(html)
            ?: throw IllegalStateException("虎牙页面解析失败（无 stream JSON）")
        val parsed = Jsonx.parse(match.groupValues[1] + "}")
        val node = Jsonx.at(parsed, "data", "0")
        val gameLiveInfo = Jsonx.obj(Jsonx.at(node, "gameLiveInfo"))
        val streamList = Jsonx.array(Jsonx.at(node, "gameStreamInfoList"))
            .mapNotNull { Jsonx.obj(it) }
        var title = fallbackTitle
        if (gameLiveInfo != null) {
            Jsonx.str(gameLiveInfo["introduction"]).takeIf { it.isNotEmpty() }?.let { title = it }
        }
        val flv = streamList.firstOrNull()?.let { first ->
            val streamName = Jsonx.str(first["sStreamName"])
            val anti = rebuildAntiCode(Jsonx.str(first["sFlvAntiCode"]), streamName)
            val suffix = Jsonx.str(first["sFlvUrlSuffix"]).ifEmpty { "flv" }
            "${Jsonx.str(first["sFlvUrl"])}/$streamName.$suffix?$anti&ratio="
        } ?: ""
        val m3u8 = streamList.firstOrNull()?.let { first ->
            val streamName = Jsonx.str(first["sStreamName"])
            val anti = rebuildAntiCode(Jsonx.str(first["sHlsAntiCode"], ), streamName)
            val suffix = Jsonx.str(first["sHlsUrlSuffix"]).ifEmpty { "m3u8" }
            "${Jsonx.str(first["sHlsUrl"])}/$streamName.$suffix?$anti&ratio="
        } ?: ""
        return LiveInfo(
            isLive = streamList.isNotEmpty(),
            anchorName = if (anchor.isEmpty()) Jsonx.str(gameLiveInfo?.get("nick")) else anchor,
            title = title,
            flvUrl = flv,
            m3u8Url = m3u8,
            streamUrl = flv.ifEmpty { m3u8 },
            platform = "huya",
            extra = roomIdExtra(rid),
        )
    }

    /** 反码重建 — 直译 get_anti_code（md5 签名，codec=264 保 ExoPlayer 兼容）。 */
    internal fun rebuildAntiCode(oldAntiCode: String, streamName: String): String {
        val query = parseQuery(oldAntiCode)
        val t13 = System.currentTimeMillis()
        val sdkSid = t13
        val initUuid = ((t13 % 10_000_000_000L) * 1000L + Random.nextLong(1000)) % 4_294_967_295L
        val uid = Random.nextLong(1_400_000_000_000L, 1_400_001_000_000L)
        val seqId = uid + sdkSid
        val targetUnix = (t13 + 110624) / 1000
        val wsTime = java.lang.Long.toHexString(targetUnix)

        val fmDecoded = String(Base64.getDecoder().decode(java.net.URLDecoder.decode(query["fm"] ?: "", "UTF-8")))
        val wsSecretPf = fmDecoded.split("_").first()
        val wsSecretHash = md5("$seqId|${query["ctype"] ?: ""}|100")
        val wsSecret = "${wsSecretPf}_${uid}_${streamName}_${wsSecretHash}_$wsTime"
        val wsSecretMd5 = md5(wsSecret)

        return "wsSecret=$wsSecretMd5&wsTime=$wsTime&seqid=$seqId&ctype=${query["ctype"] ?: ""}&ver=1" +
            "&fs=${query["fs"] ?: ""}&uuid=$initUuid&u=$uid&t=100&sv=2403051612&sdk_sid=$sdkSid&codec=264"
    }

    private fun parseQuery(query: String): Map<String, String> =
        query.split("&").mapNotNull {
            val idx = it.indexOf('=')
            if (idx <= 0) null else it.substring(0, idx) to it.substring(idx + 1)
        }.toMap()

    private fun md5(text: String): String =
        MessageDigest.getInstance("MD5").digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

    private fun roomIdExtra(rid: String) = kotlinx.serialization.json.JsonObject(
        mapOf("room_id" to kotlinx.serialization.json.JsonPrimitive(rid)),
    )

    companion object {
        private const val PC_UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
    }
}
