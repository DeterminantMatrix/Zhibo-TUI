package com.determinantmatrix.zhibo.core.datastore

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.determinantmatrix.zhibo.core.model.AppConfig
import com.determinantmatrix.zhibo.core.model.SettingsCsv
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.zhiboDataStore by preferencesDataStore(name = "zhibo_settings")

/**
 * 全局设置仓库 — DataStore 键名与桌面 settings.csv 键一致，
 * settings.csv 可无损导入（一次性覆盖）与导出。
 */
class SettingsRepository(private val context: Context) {

    private object Keys {
        val pollInterval = intPreferencesKey(SettingsCsv.KEY_POLL_INTERVAL)
        val maxConcurrentChecks = intPreferencesKey(SettingsCsv.KEY_MAX_CONCURRENT_CHECKS)
        val failureBackoffAfter = intPreferencesKey(SettingsCsv.KEY_FAILURE_BACKOFF_AFTER)
        val failureBackoffPolls = intPreferencesKey(SettingsCsv.KEY_FAILURE_BACKOFF_POLLS)
        val notificationsEnabled = booleanPreferencesKey(SettingsCsv.KEY_NOTIFICATIONS_ENABLED)
        fun platformProxy(platform: String) =
            stringPreferencesKey(SettingsCsv.KEY_PLATFORM_PROXY_PREFIX + platform)
    }

    val config: Flow<AppConfig> = context.zhiboDataStore.data.map { prefs ->
        AppConfig(
            pollInterval = prefs[Keys.pollInterval] ?: 60,
            maxConcurrentChecks = prefs[Keys.maxConcurrentChecks] ?: 8,
            failureBackoffAfter = prefs[Keys.failureBackoffAfter] ?: 3,
            failureBackoffPolls = prefs[Keys.failureBackoffPolls] ?: 2,
            notificationsEnabled = prefs[Keys.notificationsEnabled] ?: true,
            platformProxies = prefs.asMap().entries
                .filter { it.key.name.startsWith(SettingsCsv.KEY_PLATFORM_PROXY_PREFIX) }
                .mapNotNull { entry ->
                    val platform = entry.key.name.removePrefix(SettingsCsv.KEY_PLATFORM_PROXY_PREFIX)
                    val proxy = (entry.value as? String)?.takeIf { it.isNotBlank() } ?: return@mapNotNull null
                    platform to proxy
                }
                .toMap(),
        )
    }

    suspend fun current(): AppConfig = config.first()

    /** 只更新平台代理（设置界面用），其余设置保持不变。 */
    suspend fun savePlatformProxies(proxies: Map<String, String>) {
        context.zhiboDataStore.edit { prefs ->
            prefs.asMap().keys
                .filter { it.name.startsWith(SettingsCsv.KEY_PLATFORM_PROXY_PREFIX) }
                .forEach { prefs.remove(it) }
            proxies.forEach { (platform, proxy) ->
                if (platform.isNotBlank() && proxy.isNotBlank()) {
                    prefs[platformProxyKey(platform)] = proxy
                }
            }
        }
    }

    private fun platformProxyKey(platform: String) =
        stringPreferencesKey(SettingsCsv.KEY_PLATFORM_PROXY_PREFIX + platform.lowercase().trim())

    suspend fun save(cfg: AppConfig) {
        context.zhiboDataStore.edit { prefs ->
            prefs[Keys.pollInterval] = cfg.pollInterval
            prefs[Keys.maxConcurrentChecks] = cfg.maxConcurrentChecks
            prefs[Keys.failureBackoffAfter] = cfg.failureBackoffAfter
            prefs[Keys.failureBackoffPolls] = cfg.failureBackoffPolls
            prefs[Keys.notificationsEnabled] = cfg.notificationsEnabled
            // 先清掉旧的平台代理键再写入，避免残留
            val stale = prefs.asMap().keys
                .filter { it.name.startsWith(SettingsCsv.KEY_PLATFORM_PROXY_PREFIX) }
            stale.forEach { prefs.remove(it) }
            cfg.platformProxies.forEach { (platform, proxy) ->
                prefs[Keys.platformProxy(platform)] = proxy
            }
        }
    }
}
