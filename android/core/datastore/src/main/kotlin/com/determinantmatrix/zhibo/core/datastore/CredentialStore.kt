package com.determinantmatrix.zhibo.core.datastore

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.determinantmatrix.zhibo.core.resolver.Fs1Auth
import com.determinantmatrix.zhibo.core.resolver.Fs1AuthProvider

/**
 * 凭据仓库 — EncryptedSharedPreferences（AES256-GCM 主密钥在 Android Keystore）。
 * 与桌面一致：凭据永不写入关注项/CSV/日志，只存本机加密存储。
 */
class CredentialStore(private val context: Context) : Fs1AuthProvider {

    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "zhibo_credentials",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    /** B 站 Cookie 头（完整 cookie 串，供 resolver 逐请求读取）。 */
    fun bilibiliCookie(): String = prefs.getString(KEY_BILIBILI_COOKIE, "").orEmpty()

    fun saveBilibiliCookie(cookieHeader: String) {
        prefs.edit().putString(KEY_BILIBILI_COOKIE, cookieHeader).apply()
    }

    fun hasBilibiliCookie(): Boolean = bilibiliCookie().contains("SESSDATA=")

    fun clearBilibiliCookie() {
        prefs.edit().remove(KEY_BILIBILI_COOKIE).apply()
    }

    /** FS1 授权；实现 Fs1AuthProvider，解析器每次请求前读取。 */
    override fun current(): Fs1Auth? =
        prefs.getString(KEY_FS1_AUTH, null)?.let { Fs1Auth.fromJsonText(it) }

    fun saveFs1Auth(auth: Fs1Auth) {
        prefs.edit().putString(KEY_FS1_AUTH, auth.toJson().toString()).apply()
    }

    fun hasFs1Auth(): Boolean = current() != null

    fun clearFs1Auth() {
        prefs.edit().remove(KEY_FS1_AUTH).apply()
    }

    companion object {
        private const val KEY_BILIBILI_COOKIE = "bilibili_cookie"
        private const val KEY_FS1_AUTH = "fs1_auth_json"
    }
}
