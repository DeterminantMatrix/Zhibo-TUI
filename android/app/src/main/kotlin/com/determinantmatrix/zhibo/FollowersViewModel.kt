package com.determinantmatrix.zhibo

import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.determinantmatrix.zhibo.core.database.toDomain
import com.determinantmatrix.zhibo.core.database.toEntity
import com.determinantmatrix.zhibo.core.model.AppConfig
import com.determinantmatrix.zhibo.core.model.Follower
import com.determinantmatrix.zhibo.core.model.FollowersCsv
import com.determinantmatrix.zhibo.core.model.SettingsCsv
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * M0 验证用 ViewModel：CSV 导入 → Room，Room → CSV 导出，
 * 与桌面版 followers.csv / settings.csv 无损互通。
 */
class FollowersViewModel : ViewModel() {

    private val dao = ZhiboApp.instance.database.followerDao()
    private val settingsRepo = ZhiboApp.instance.settings

    val followers: StateFlow<List<Follower>> = dao.observeAll()
        .map { entities -> entities.map { it.toDomain() } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val settings: StateFlow<AppConfig> = settingsRepo.config
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), AppConfig())

    private val _message = MutableStateFlow("就绪")
    val message: StateFlow<String> = _message

    fun importFollowersCsv(uri: Uri) {
        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    val text = ZhiboApp.instance.contentResolver.openInputStream(uri)!!
                        .bufferedReader(Charsets.UTF_8).readText()
                    val decoded = FollowersCsv.decode(text)
                    dao.replaceAll(decoded.followers.mapIndexed { i, f -> f.toEntity(i) })
                    decoded
                }
            }
            result.fold(
                onSuccess = { (imported, warnings) ->
                    val warningText = if (warnings.isEmpty()) "" else "；警告：" + warnings.joinToString("，")
                    _message.value = "已导入 ${imported.size} 个关注项$warningText"
                },
                onFailure = { _message.value = "导入失败：${it.message}" },
            )
        }
    }

    fun exportFollowersCsv(uri: Uri) {
        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    val text = FollowersCsv.encode(dao.getAll().map { it.toDomain() })
                    ZhiboApp.instance.contentResolver.openOutputStream(uri, "wt")!!
                        .use { it.write(text.toByteArray(Charsets.UTF_8)) }
                }
            }
            result.fold(
                onSuccess = { _message.value = "已导出 ${followers.value.size} 个关注项" },
                onFailure = { _message.value = "导出失败：${it.message}" },
            )
        }
    }

    fun importSettingsCsv(uri: Uri) {
        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    val text = ZhiboApp.instance.contentResolver.openInputStream(uri)!!
                        .bufferedReader(Charsets.UTF_8).readText()
                    val cfg = SettingsCsv.decode(text)
                    settingsRepo.save(cfg)
                    cfg
                }
            }
            result.fold(
                onSuccess = { _message.value = "已导入设置：轮询 ${it.pollInterval}s，并发 ${it.maxConcurrentChecks}" },
                onFailure = { _message.value = "设置导入失败：${it.message}" },
            )
        }
    }

    fun exportSettingsCsv(uri: Uri) {
        viewModelScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    val text = SettingsCsv.encode(settingsRepo.current())
                    ZhiboApp.instance.contentResolver.openOutputStream(uri, "wt")!!
                        .use { it.write(text.toByteArray(Charsets.UTF_8)) }
                }
            }
            result.fold(
                onSuccess = { _message.value = "设置已导出" },
                onFailure = { _message.value = "设置导出失败：${it.message}" },
            )
        }
    }
}
