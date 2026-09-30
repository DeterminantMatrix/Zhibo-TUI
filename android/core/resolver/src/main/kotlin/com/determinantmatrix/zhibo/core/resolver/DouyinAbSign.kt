package com.determinantmatrix.zhibo.core.resolver

/**
 * 抖音 web a_bogus 签名 — 逐行移植 streamget ab_sign.py：
 * SM3 国密哈希 + RC4 + 魔改 base64。测试向量由该库本身生成（见 DouyinTest）。
 */
object DouyinAbSign {

    private val SIGN_TABLES = mapOf(
        "s0" to "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=",
        "s1" to "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
        "s2" to "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe=",
        "s3" to "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe",
        "s4" to "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe",
    )

    internal fun rc4(input: List<Int>, key: List<Int>): List<Int> {
        val s = (0 until 256).toMutableList()
        var j = 0
        for (i in 0 until 256) {
            j = (j + s[i] + key[i % key.size]) % 256
            val t = s[i]; s[i] = s[j]; s[j] = t
        }
        var i = 0
        j = 0
        val result = mutableListOf<Int>()
        for (char in input) {
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            val t = s[i]; s[i] = s[j]; s[j] = t
            result.add(s[(s[i] + s[j]) % 256] xor char)
        }
        return result
    }

    private fun rotateLeft(x: Int, n: Int): Int {
        val shift = n % 32
        return (x shl shift) or (x ushr (32 - shift))
    }

    private fun tj(j: Int): Int = if (j in 0..15) 2043430169 else 2055708042

    private fun ff(j: Int, x: Int, y: Int, z: Int): Int =
        if (j in 0..15) x xor y xor z else (x and y) or (x and z) or (y and z)

    private fun gg(j: Int, x: Int, y: Int, z: Int): Int =
        if (j in 0..15) x xor y xor z else (x and (z.inv())) or (y and z)

    /** SM3 状态机。 */
    internal class Sm3 {

        private val digest = org.bouncycastle.crypto.digests.SM3Digest()

        private fun digestBytes(data: List<Int>): ByteArray {
            val bytes = ByteArray(data.size)
            data.forEachIndexed { i, v -> bytes[i] = v.toByte() }
            digest.reset()
            digest.update(bytes, 0, bytes.size)
            val out = ByteArray(digest.digestSize)
            digest.doFinal(out, 0)
            return out
        }

        internal var diagCompress = false
        var reg = IntArray(8)
        var chunk = mutableListOf<Int>()
        var size = 0

        init {
            reset()
        }

        fun reset() {
            reg = intArrayOf(
                1937774191, 1226093241, 388252375, -628488704, -1452330820, 372324522, -477237683, -1325724082,
            )
            chunk = mutableListOf()
            size = 0
        }

        fun sum(data: List<Int>): List<Int> {
            val bytes = digestBytes(data)
            return bytes.map { it.toInt() and 0xFF }
        }
    }

    /** 魔改 base64：输入为 0-255 "字符码"列表。 */
    @JvmStatic
    fun debugRotl(x: Int, n: Int): Int = rotateLeft(x, n)

        internal fun resultEncrypt(longStr: List<Int>, num: String): String {
        val table = SIGN_TABLES.getValue(num)
        val masks = intArrayOf(16515072, 258048, 4032, 63)
        val shifts = intArrayOf(18, 12, 6, 0)
        val result = StringBuilder((longStr.size + 2) / 3 * 4)
        var round = 0
        fun longInt(r: Int): Int {
            val base = r * 3
            val c1 = longStr.getOrElse(base) { 0 }
            val c2 = longStr.getOrElse(base + 1) { 0 }
            val c3 = longStr.getOrElse(base + 2) { 0 }
            return (c1 shl 16) or (c2 shl 8) or c3
        }
        var current = longInt(round)
        // 对齐 Python math.ceil(len / 3 * 4)：len%3==2 时 (len+2)/3*4 会多一个字符
        val totalChars = (longStr.size * 4 + 2) / 3
        for (i in 0 until totalChars) {
            if (i / 4 != round) {
                round++
                current = longInt(round)
            }
            val index = i % 4
            result.append(table[(current and masks[index]) ushr shifts[index]])
        }
        return result.toString()
    }

    private fun generRandom(randomNum: Int, option: List<Int>): List<Int> {
        val byte1 = randomNum and 255
        val byte2 = (randomNum ushr 8) and 255
        return listOf(
            (byte1 and 170) or (option[0] and 85),
            (byte1 and 85) or (option[0] and 170),
            (byte2 and 170) or (option[1] and 85),
            (byte2 and 85) or (option[1] and 170),
        )
    }

    /** 与 JS/Python 版一致的固定"随机"字节（保证签名确定性可测）。 */
    private fun generateRandomStr(): List<Int> =
        generRandom(1234, listOf(3, 45)) +
            generRandom(9876, listOf(1, 0)) +
            generRandom(5555, listOf(1, 5))

    /**
     * 生成 a_bogus 参数。nowMillis 注入以便测试；生产传当前时间。
     */
    fun abSign(urlSearchParams: String, userAgent: String, nowMillis: Long = System.currentTimeMillis()): String {
        val windowEnv = "1920|1080|1920|1040|0|30|0|0|1872|92|1920|1040|1857|92|1|24|Win32"
        val sm3 = Sm3()
        val urlList = sm3.sum(sm3.sum((urlSearchParams + "cus").map { it.code }))
        val cus = sm3.sum(sm3.sum("cus".map { it.code }))
        val uaKey = listOf(0, 1, 14)
        val ua = sm3.sum(resultEncrypt(rc4(userAgent.toCodePoints(), uaKey), "s3").map { it.code })

        val arguments = listOf(0, 1, 14)

        fun splitToBytes(num: Long): List<Int> = listOf(
            ((num ushr 24) and 255).toInt(), ((num ushr 16) and 255).toInt(),
            ((num ushr 8) and 255).toInt(), (num and 255).toInt(),
        )

        val b = HashMap<Int, Int>()
        b[8] = 3
        b[18] = 44
        // 时间字节全程用 Long 计算（1715300000123 超出 Int 范围）
        val stb = splitToBytes(nowMillis)
        b[20] = stb[0]; b[21] = stb[1]; b[22] = stb[2]; b[23] = stb[3]
        b[24] = ((nowMillis / 256 / 256 / 256 / 256) % 256).toInt()
        b[25] = ((nowMillis / 256 / 256 / 256 / 256 / 256) % 256).toInt()
        val a0 = splitToBytes(arguments[0].toLong())
        b[26] = a0[0]; b[27] = a0[1]; b[28] = a0[2]; b[29] = a0[3]
        b[30] = (arguments[1] / 256) and 255
        b[31] = (arguments[1] % 256) and 255
        val a1 = splitToBytes(arguments[1].toLong())
        b[32] = a1[0]; b[33] = a1[1]
        val a2 = splitToBytes(arguments[2].toLong())
        b[34] = a2[0]; b[35] = a2[1]; b[36] = a2[2]; b[37] = a2[3]
        b[38] = urlList[21]; b[39] = urlList[22]
        b[40] = cus[21]; b[41] = cus[22]
        b[42] = ua[23]; b[43] = ua[24]
        val etb = splitToBytes(nowMillis + 100)
        b[44] = etb[0]; b[45] = etb[1]; b[46] = etb[2]; b[47] = etb[3]
        b[48] = b[8]!!
        b[49] = ((nowMillis + 100) / 256 / 256 / 256 / 256 % 256).toInt()
        b[50] = ((nowMillis + 100) / 256 / 256 / 256 / 256 / 256 % 256).toInt()
        val pageId = 110624
        b[51] = pageId
        val pid = splitToBytes(pageId.toLong())
        b[52] = pid[0]; b[53] = pid[1]; b[54] = pid[2]; b[55] = pid[3]
        val aid = 6383
        b[56] = aid
        b[57] = aid and 255
        b[58] = (aid ushr 8) and 255
        b[59] = (aid ushr 16) and 255
        b[60] = (aid ushr 24) and 255
        val windowEnvList = windowEnv.map { it.code }
        b[64] = windowEnvList.size
        b[65] = b[64]!! and 255
        b[66] = (b[64]!! ushr 8) and 255
        b[69] = 0; b[70] = 0; b[71] = 0
        b[72] = (b[18]!! xor b[20]!! xor b[26]!! xor b[30]!! xor b[38]!! xor b[40]!! xor b[42]!! xor
            b[21]!! xor b[27]!! xor b[31]!! xor b[35]!! xor b[39]!! xor b[41]!! xor b[43]!! xor
            b[22]!! xor b[28]!! xor b[32]!! xor b[36]!! xor b[23]!! xor b[29]!! xor b[33]!! xor
            b[37]!! xor b[44]!! xor b[45]!! xor b[46]!! xor b[47]!! xor b[48]!! xor b[49]!! xor
            b[50]!! xor b[24]!! xor b[25]!! xor b[52]!! xor b[53]!! xor b[54]!! xor b[55]!! xor
            b[57]!! xor b[58]!! xor b[59]!! xor b[60]!! xor b[65]!! xor b[66]!! xor b[70]!! xor
            b[71]!!)
        val bb = mutableListOf(
            b[18]!!, b[20]!!, b[52]!!, b[26]!!, b[30]!!, b[34]!!, b[58]!!, b[38]!!, b[40]!!, b[53]!!,
            b[42]!!, b[21]!!, b[27]!!, b[54]!!, b[55]!!, b[31]!!, b[35]!!, b[57]!!, b[39]!!, b[41]!!,
            b[43]!!, b[22]!!, b[28]!!, b[32]!!, b[60]!!, b[36]!!, b[23]!!, b[29]!!, b[33]!!, b[37]!!,
            b[44]!!, b[45]!!, b[59]!!, b[46]!!, b[47]!!, b[48]!!, b[49]!!, b[50]!!, b[24]!!, b[25]!!,
            b[65]!!, b[66]!!, b[70]!!, b[71]!!,
        )
        bb.addAll(windowEnvList)
        bb.add(b[72]!!)
        val rc = rc4(bb, listOf(121))
        return resultEncrypt(generateRandomStr() + rc, "s4") + "="
    }
}

/** String 的字符码列表（对齐 Python 的 ord 遍历，仅用于签名算法）。 */
private fun String.toCodePoints(): List<Int> = map { it.code }
