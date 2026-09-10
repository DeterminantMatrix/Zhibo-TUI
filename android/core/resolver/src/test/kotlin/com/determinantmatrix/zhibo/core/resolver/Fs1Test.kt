package com.determinantmatrix.zhibo.core.resolver

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class Fs1Test {

    // 以下向量由桌面 zhibo.plugins.fs1_plugin 真实计算生成
    @Test
    fun `sign matches desktop md5 chain`() {
        val sig = Fs1Crypto.signParams(
            mapOf("room_id" to "888888888", "code_id" to "lgzm", "time" to "1715300000"),
        )
        assertEquals("135bcd25eef36ad772d465d9af057974", sig)
    }

    @Test
    fun `aes cbc decrypt matches desktop`() {
        val json = Fs1Crypto.decryptPlayResponse(
            "FqSIBbxHLiyPZ6wiqBa7X293VzdwGx+bxf1vVQLNuuK9WfSxh0OG6f2Q4XzbfXOzLcScoxtMC8eXrU/O6g3+SVAKllNwUhDhv3PIzOTqEu6+fzyJj6tUfvWfhh68HQT4",
        )
        val parsed = Json.parseToJsonElement(json).jsonObject
        assertEquals(200, Jsonx.int(Jsonx.at(parsed, "code")))
        assertEquals(
            "https://example.com/live.m3u8",
            Jsonx.str(Jsonx.at(parsed, "data", "play_url")),
        )
    }

    @Test
    fun `trusted url validation mirrors desktop whitelist`() {
        assertTrue(Fs1Trust.isTrustedApi("https://apc.xzood6veuybwkr.com/v1/room"))
        assertTrue(!Fs1Trust.isTrustedApi("http://apc.xzood6veuybwkr.com/v1/room"))
        assertTrue(!Fs1Trust.isTrustedApi("https://evil.com/v1/room"))
        assertTrue(!Fs1Trust.isTrustedApi("https://apc.xzood6veuybwkr.com/v1/room?x=1"))
        assertTrue(Fs1Trust.isTrustedPlayApi("https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url"))
        assertTrue(!Fs1Trust.isTrustedPlayApi("https://openim-php-api.abc.cc/other"))
        assertTrue(Fs1Trust.isTrustedSite("https://www.fszb130.com/live/1"))
        assertTrue(Fs1Trust.isTrustedSite("https://fszb148.com"))
        assertTrue(!Fs1Trust.isTrustedSite("https://example.com"))

        // rooms.yaml 里的 api_url 未通过校验时回退默认值（对应桌面逻辑）
        val auth = Fs1Auth.fromJson(
            Fs1Auth(
                token = "t",
                apiUrl = "https://evil.com/v1/room",
            ).toJson(),
        )!!
        assertEquals(Fs1Trust.DEFAULT_API_URL, "https://apc.xzood6veuybwkr.com/v1/room")
        assertFalse(Fs1Trust.isTrustedApi(auth.apiUrl))
    }

    @Test
    fun `fs1 auth json roundtrip`() {
        val auth = Fs1Auth(
            token = "abc",
            imei = "111",
            dunImei = "222",
            apiVersion = "8",
        )
        val parsed = Fs1Auth.fromJsonText(auth.toJson().toString())
        assertEquals(auth, parsed)
        assertNull(Fs1Auth.fromJsonText("""{"foo": 1}"""))
        assertNull(Fs1Auth.fromJsonText("not json"))
    }

    @Test
    fun `fs1 rooms yaml config section parses`() {
        val yaml = """
            config:
              api_url: https://apc.xzood6veuybwkr.com/v1/room
              site_url: 'https://www.fszb130.com'
              api_version: "8"
              token: eyJhbGci.abc_def
              imei: "12345678901234567890123456789012"
              dun_imei: '09876543210987654321098765432109'
              user_agent: Mozilla/5.0 Test
              cookie: a=1; b=2
              version: 1.8.4
              play_api_url: https://openim-php-api.q2n1w3g2y5v0w4l1.cc/v230/play/url

            rooms:
              - room_id: "1"
        """.trimIndent()
        val auth = Fs1Auth.fromYamlText(yaml)!!
        assertEquals("https://apc.xzood6veuybwkr.com/v1/room", auth.apiUrl)
        assertEquals("https://www.fszb130.com", auth.siteUrl)
        assertEquals("8", auth.apiVersion)
        assertEquals("eyJhbGci.abc_def", auth.token)
        assertEquals("12345678901234567890123456789012", auth.imei)
        assertEquals("09876543210987654321098765432109", auth.dunImei)
        assertEquals("Mozilla/5.0 Test", auth.userAgent)
        assertEquals("1.8.4", auth.version)
        // rooms 段不解析、无 token 时拒绝导入
        assertNull(Fs1Auth.fromYamlText("rooms:\n  - room_id: \"1\"\n"))
    }
}
