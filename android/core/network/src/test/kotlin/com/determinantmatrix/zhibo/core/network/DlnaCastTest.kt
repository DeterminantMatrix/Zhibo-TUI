package com.determinantmatrix.zhibo.core.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DlnaCastTest {

    private val descriptionXml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <root xmlns="urn:schemas-upnp-org:device-1-0">
          <device>
            <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
            <friendlyName>客厅的小米电视</friendlyName>
            <serviceList>
              <service>
                <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
                <controlURL>/upnp/control/RenderCtrl</controlURL>
              </service>
              <service>
                <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
                <controlURL>/upnp/control/AVT1</controlURL>
              </service>
            </serviceList>
          </device>
        </root>
    """.trimIndent()

    @Test
    fun `parse description resolves relative avtransport control url`() {
        val renderer = DlnaCast.parseDescription(
            descriptionXml,
            "http://192.168.1.8:49152/description.xml",
        )
        assertNotNull(renderer)
        assertEquals("客厅的小米电视", renderer!!.friendlyName)
        assertEquals("http://192.168.1.8:49152/upnp/control/AVT1", renderer.controlUrl)
    }

    @Test
    fun `description without avtransport returns null`() {
        val xml = descriptionXml.replace("/upnp/control/AVT1", "/x")
        // AVTransport serviceType 仍在，controlURL 改为 /x —— 仍应解析成功（不校验路径内容）
        val renderer = DlnaCast.parseDescription(xml, "http://192.168.1.8:49152/desc.xml")
        assertEquals("http://192.168.1.8:49152/x", renderer!!.controlUrl)
        // 没有 AVTransport 服务时返回 null
        val noAv = descriptionXml
            .replace("urn:schemas-upnp-org:service:AVTransport:1", "urn:schemas-upnp-org:service:X:1")
        assertTrue(DlnaCast.parseDescription(noAv, "http://x/desc.xml") == null)
    }

    @Test
    fun `msearch packet has required ssdp lines`() {
        val packet = DlnaCast.msearchPacket()
        assertTrue(packet.startsWith("M-SEARCH * HTTP/1.1\r\n"))
        assertTrue("HOST: 239.255.255.250:1900" in packet)
        assertTrue("ST: urn:schemas-upnp-org:device:MediaRenderer:1" in packet)
        assertTrue(packet.endsWith("\r\n\r\n"))
    }

    @Test
    fun `didl metadata escapes and picks mime by extension`() {
        val didl = DlnaCast.didlMetadata("https://cdn.example/live.m3u8?token=a&b", "老陈 \"说球\" & 赛事")
        assertTrue("application/vnd.apple.mpegurl" in didl)
        assertTrue("https://cdn.example/live.m3u8?token=a&amp;b" in didl)
        assertTrue("老陈 &quot;说球&quot; &amp; 赛事" in didl)
        val flv = DlnaCast.didlMetadata("http://x/y.flv", "t")
        assertTrue("video/x-flv" in flv)
    }
}
