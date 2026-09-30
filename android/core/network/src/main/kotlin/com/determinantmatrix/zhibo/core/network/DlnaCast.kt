package com.determinantmatrix.zhibo.core.network

import java.io.ByteArrayInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.MulticastSocket
import java.net.SocketTimeoutException
import java.net.URI
import javax.xml.parsers.DocumentBuilderFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * DLNA/UPnP 投屏（推流到同一 WiFi 下的 MediaRenderer：电视/盒子）。
 * SSDP 发现 + AVTransport SetAVTransportURI/Play。纯 JVM，可单测。
 */
object DlnaCast {

    data class Renderer(
        val friendlyName: String,
        /** AVTransport control URL（绝对地址）。 */
        val controlUrl: String,
        /** 设备描述文件地址（发现来源）。 */
        val location: String,
    )

    private const val SSDP_ADDR = "239.255.255.250"
    private const val SSDP_PORT = 1900
    private const val AVTRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"

    fun msearchPacket(seconds: Int = 3): String =
        "M-SEARCH * HTTP/1.1\r\n" +
            "HOST: $SSDP_ADDR:$SSDP_PORT\r\n" +
            "MAN: \"ssdp:discover\"\r\n" +
            "MX: $seconds\r\n" +
            "ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n"

    /**
     * SSDP 扫描局域网内的 DLNA 渲染设备。
     * 注意：Android 模拟器 NAT 不转发组播，模拟器内请用手动 IP；真机正常。
     */
    suspend fun discover(http: Http, timeoutMs: Long = 3500): List<Renderer> =
        withContext(Dispatchers.IO) {
            val found = LinkedHashMap<String, Renderer>()
            val group = InetAddress.getByName(SSDP_ADDR)
            MulticastSocket(SSDP_PORT + (0..2000).random()).apply {
                broadcast = true
                soTimeout = 250
                joinGroup(group)
            }.use { socket ->
                val packet = msearchPacket().toByteArray(Charsets.ISO_8859_1)
                socket.send(DatagramPacket(packet, packet.size, group, SSDP_PORT))
                val deadline = System.currentTimeMillis() + timeoutMs
                val buf = ByteArray(2048)
                while (System.currentTimeMillis() < deadline) {
                    try {
                        val received = DatagramPacket(buf, buf.size)
                        socket.receive(received)
                        val text = String(received.data, 0, received.length)
                        val location = Regex("(?im)^LOCATION:\\s*(\\S+)").find(text)?.groupValues?.get(1)
                            ?: continue
                        if (location in found) continue
                        describe(location, http)?.let { found[location] = it }
                    } catch (_: SocketTimeoutException) {
                    } catch (_: Exception) {
                        // 单个设备描述失败不影响扫描
                    }
                }
            }
            found.values.toList()
        }

    /** 手动指定设备描述地址（模拟器/组播不可用时的兜底）。 */
    fun describe(location: String, http: Http): Renderer? = runCatching {
        val xml = http.get(location)
        parseDescription(xml, location)
    }.getOrNull()

    /** 解析设备描述 XML：friendlyName + AVTransport controlURL（相对路径按 base 解析）。 */
    fun parseDescription(xml: String, baseUrl: String): Renderer? {
        val doc = DocumentBuilderFactory.newInstance().apply {
            isNamespaceAware = false
        }.newDocumentBuilder().parse(ByteArrayInputStream(xml.toByteArray(Charsets.UTF_8)))

        val friendly = doc.getElementsByTagName("friendlyName")
            .let { if (it.length > 0) it.item(0).textContent.trim() else "" }

        var controlUrl: String? = null
        val services = doc.getElementsByTagName("service")
        for (i in 0 until services.length) {
            val service = services.item(i)
            val children = service.childNodes
            var type = ""
            var control = ""
            for (j in 0 until children.length) {
                when (children.item(j).nodeName) {
                    "serviceType" -> type = children.item(j).textContent.trim()
                    "controlURL" -> control = children.item(j).textContent.trim()
                }
            }
            if (type == AVTRANSPORT && control.isNotEmpty()) {
                controlUrl = control
                break
            }
        }
        val resolved = controlUrl?.let {
            URI(baseUrl).resolve(it).toString()
        } ?: return null
        return Renderer(
            friendlyName = friendly.ifEmpty { URI(baseUrl).host ?: "未知设备" },
            controlUrl = resolved,
            location = baseUrl,
        )
    }

    /** 推流：SetAVTransportURI + Play。任一步失败抛异常。 */
    fun cast(http: Http, renderer: Renderer, streamUrl: String, title: String) {
        val metadata = didlMetadata(streamUrl, title)
        val setBody = soapBody(
            "SetAVTransportURI",
            "<InstanceID>0</InstanceID>" +
                "<CurrentURI>${xmlEscape(streamUrl)}</CurrentURI>" +
                "<CurrentURIMetaData>${xmlEscape(metadata)}</CurrentURIMetaData>",
        )
        http.postXml(
            renderer.controlUrl,
            mapOf("SOAPACTION" to "\"$AVTRANSPORT#SetAVTransportURI\""),
            setBody,
        )
        val playBody = soapBody(
            "Play",
            "<InstanceID>0</InstanceID><Speed>1</Speed>",
        )
        http.postXml(
            renderer.controlUrl,
            mapOf("SOAPACTION" to "\"$AVTRANSPORT#Play\""),
            playBody,
        )
    }

    private fun soapBody(action: String, inner: String): String =
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>" +
            "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" " +
            "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body>" +
            "<u:$action xmlns:u=\"$AVTRANSPORT\">$inner</u:$action>" +
            "</s:Body></s:Envelope>"

    /** DIDL-Lite 元数据；MIME 由 URL 猜测（m3u8→HLS，flv→x-flv，默认 mp4）。 */
    fun didlMetadata(streamUrl: String, title: String): String {
        val path = streamUrl.substringBefore('?').lowercase()
        val mime = when {
            path.endsWith(".m3u8") -> "application/vnd.apple.mpegurl"
            path.endsWith(".flv") -> "video/x-flv"
            else -> "video/mp4"
        }
        return "<DIDL-Lite xmlns=\"urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/\" " +
            "xmlns:dc=\"http://purl.org/dc/elements/1.1/\" " +
            "xmlns:upnp=\"urn:schemas-upnp-org:metadata-1-0/upnp/\">" +
            "<item id=\"0\" parentID=\"-1\" restricted=\"0\">" +
            "<dc:title>${xmlEscape(title)}</dc:title>" +
            "<upnp:class>object.item.videoItem</upnp:class>" +
            "<res protocolInfo=\"http-get:*:$mime:*\">${xmlEscape(streamUrl)}</res>" +
            "</item></DIDL-Lite>"
    }

    fun xmlEscape(text: String): String = text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")
        .replace("'", "&apos;")
}
