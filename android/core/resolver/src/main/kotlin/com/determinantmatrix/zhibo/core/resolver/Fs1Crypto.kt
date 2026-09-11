package com.determinantmatrix.zhibo.core.resolver

import java.net.URI
import java.security.MessageDigest
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

/** FS1 域名/API 信任校验 — 直译桌面 fs1_plugin.py 的白名单。 */
object Fs1Trust {

    private val FS_DOMAIN = Regex("(?:[a-z0-9-]+\\.)*(?:fszb|fs)\\d+\\.com", RegexOption.IGNORE_CASE)
    private val PLAY_HOST = Regex("openim-php-api\\.[a-z0-9]{8,64}\\.cc", RegexOption.IGNORE_CASE)

    const val API_HOST = "apc.xzood6veuybwkr.com"
    const val API_PATH = "/v1/room"
    const val PLAY_PATH = "/v230/play/url"
    const val DEFAULT_SITE_URL = "https://www.fszb130.com"
    const val DEFAULT_API_URL = "https://apc.xzood6veuybwkr.com/v1/room"
    const val DEFAULT_PLAY_API_URL = "https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url"

    private fun parse(url: String): URI? = runCatching {
        val uri = URI(url.trim())
        val ok = uri.scheme?.lowercase() == "https" &&
            !uri.host.isNullOrBlank() &&
            uri.userInfo == null &&
            (uri.port == -1 || uri.port == 443)
        if (ok) uri else null
    }.getOrNull()

    private fun isTrusted(
        url: String,
        hostOk: (String) -> Boolean,
        path: String,
    ): Boolean {
        val uri = parse(url) ?: return false
        return hostOk(uri.host!!.lowercase()) &&
            uri.rawPath == path &&
            uri.rawQuery == null &&
            uri.rawFragment == null
    }

    fun isTrustedApi(url: String): Boolean =
        isTrusted(url, { it == API_HOST }, API_PATH)

    fun isTrustedPlayApi(url: String): Boolean =
        isTrusted(url, { PLAY_HOST.matches(it) }, PLAY_PATH)

    fun isTrustedSite(url: String): Boolean {
        val uri = parse(url) ?: return false
        return FS_DOMAIN.matches(uri.host!!.lowercase())
    }
}

/** FS1 加解密与签名 — 密钥经掩码表还原，与桌面实现字节一致。 */
object Fs1Crypto {

    private val SIGN_SECRET_MASK = intArrayOf(
        35, 17, 24, 55, 106, 42, 17, 22, 62, 12, 57, 29, 56, 52, 47, 110,
        2, 29, 53, 52, 107, 105, 14, 41, 35, 24, 62, 31, 41, 48, 48, 105,
        13, 12, 27, 32, 41, 32, 42, 53, 43, 48, 52, 105, 24, 20, 55, 53,
        44, 22, 61, 32, 44, 57, 8, 14, 34, 30, 107, 13, 63, 35, 109, 11,
        11, 107, 106, 49, 57, 53, 44, 106, 56, 98, 63, 99, 53, 24, 51,
        109, 48, 27, 15, 8,
    )
    private val AES_KEY_MASK = intArrayOf(
        48, 105, 11, 42, 43, 105, 24, 13, 41, 108, 43, 15, 25, 57, 46, 55,
        18, 10, 22, 20, 25, 24, 28, 55, 50, 15, 3, 15, 15, 46, 31, 0,
        47, 47, 43, 10, 3, 46, 46, 13, 35, 42, 55, 40, 8, 48, 46, 41,
        45, 10, 49, 61, 43, 29, 24, 51, 111, 11, 27, 59, 50, 17, 22, 22,
    )
    private val AES_IV_MASK = intArrayOf(
        56, 104, 55, 62, 31, 31, 3, 56, 13, 107, 43, 42, 40, 28, 41, 61,
    )

    private fun unmask(values: IntArray): String =
        String(values.map { (90 xor it).toChar() }.toCharArray())

    fun md5(text: String): String =
        MessageDigest.getInstance("MD5").digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

    /** 播放参数签名：按 key 排序拼接后 md5(secret)。 */
    fun signParams(params: Map<String, String>): String {
        val secret = unmask(SIGN_SECRET_MASK)
        val payload = params.keys.sorted().joinToString("") { key -> key + params.getValue(key) } + secret
        return md5(payload)
    }

    /** AES-128-CBC 解密 base64 响应体，手工去 PKCS7 填充（对齐桌面实现）。 */
    fun decryptPlayResponse(bodyBase64: String): String {
        val key = unmask(AES_KEY_MASK).substring(0, 16).toByteArray(Charsets.ISO_8859_1)
        val iv = unmask(AES_IV_MASK).toByteArray(Charsets.ISO_8859_1)
        val cipher = Cipher.getInstance("AES/CBC/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(iv))
        val decrypted = cipher.doFinal(Base64.getDecoder().decode(bodyBase64))
        val pad = decrypted.last().toInt()
        if (pad < 1 || pad > 16) throw IllegalArgumentException("invalid play-url padding")
        return String(decrypted.copyOfRange(0, decrypted.size - pad), Charsets.UTF_8)
    }
}
