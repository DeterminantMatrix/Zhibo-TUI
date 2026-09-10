package com.determinantmatrix.zhibo.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.determinantmatrix.zhibo.FollowersViewModel
import com.determinantmatrix.zhibo.ZhiboApp
import com.determinantmatrix.zhibo.core.monitor.CheckState
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

/** 平台品牌色（头像底色）。 */
private fun platformColor(platform: String): Color = when (platform.lowercase()) {
    "bilibili" -> Color(0xFF00A1D6)
    "douyu" -> Color(0xFFFF5D23)
    "huya" -> Color(0xFFFF7C00)
    "douyin" -> Color(0xFF161823)
    "twitch" -> Color(0xFF9146FF)
    "youtube" -> Color(0xFFE62117)
    "fs1" -> Color(0xFF7C4DFF)
    else -> Color(0xFF607D8B)
}

private fun platformBadge(platform: String): String = when (platform.lowercase()) {
    "bilibili" -> "B"
    "douyu" -> "斗"
    "huya" -> "虎"
    "douyin" -> "抖"
    "twitch" -> "T"
    "youtube" -> "Y"
    "fs1" -> "F"
    else -> "直"
}

/** 相对时间（对齐 nodyssey 的 "7min ago" 习惯）。 */
internal fun relativeTime(checkedAtMillis: Long, nowMillis: Long = System.currentTimeMillis()): String {
    val delta = ((nowMillis - checkedAtMillis) / 1000).coerceAtLeast(0)
    return when {
        delta < 60 -> "刚刚"
        delta < 3600 -> "${delta / 60}分钟前"
        delta < 86400 -> "${delta / 3600}小时前"
        else -> "${delta / 86400}天前"
    }
}

/**
 * 首页：顶部标签 chips（按关注标签分组）+ 直播间卡片流 + 右下角"添加关注"。
 * 点卡片：在线直接播放；未开播给出提示。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(vm: FollowersViewModel = viewModel()) {
    val followers by vm.followers.collectAsState()
    val statuses by ZhiboApp.instance.engineController.engine.statuses.collectAsState()
    val message by vm.message.collectAsState()

    var selectedTag by remember { mutableStateOf("全部") }
    val now = remember(statuses) { System.currentTimeMillis() }

    val tags = remember(followers) {
        val set = linkedSetOf<String>()
        followers.forEach { f -> f.tags.forEach { set.add(it) } }
        listOf("全部") + set.toList()
    }
    val visible = if (selectedTag == "全部") {
        followers
    } else {
        followers.filter { f -> selectedTag in f.tags }
    }

    val pickFollowersCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let(vm::importFollowersCsv) }

    Scaffold(
        topBar = {
            Column(modifier = Modifier.fillMaxWidth()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    tags.forEach { tag ->
                        FilterChip(
                            selected = tag == selectedTag,
                            onClick = { selectedTag = tag },
                            label = { Text(tag) },
                        )
                    }
                }
            }
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = {
                    pickFollowersCsv.launch(
                        arrayOf("text/*", "text/csv", "application/csv", "text/comma-separated-values"),
                    )
                },
                icon = { Text("+", style = MaterialTheme.typography.titleLarge) },
                text = { Text("添加关注") },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding)) {
            Text(
                text = message,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                items(visible, key = { it.url }) { f ->
                    val status = statuses[f.url]
                    val state = status?.state
                    LiveCard(
                        name = f.name,
                        platform = f.platform,
                        title = status?.title.orEmpty(),
                        state = state,
                        quality = f.quality,
                        checkedText = status?.checkedAtMillis?.takeIf { it > 0 }
                            ?.let { relativeTime(it, now) } ?: "",
                        onClick = {
                            if (state == CheckState.LIVE) {
                                clickScopePlay { ZhiboApp.instance.playerManager.play(f) }
                            } else {
                                vm.notify(f.name + if (f.enabled) " 未开播或未检测到在线" else " 已停用")
                            }
                        },
                    )
                }
            }
        }
    }
}

/** 供 FAB/卡片回调使用的协程作用域持有者。 */
private val playScope = kotlinx.coroutines.CoroutineScope(
    kotlinx.coroutines.Dispatchers.Main + kotlinx.coroutines.SupervisorJob(),
)

internal fun clickScopePlay(block: suspend () -> Unit) {
    playScope.launch { block() }
}

/** 单张直播间卡片 — 布局对齐 nodyssey 的帖子行。 */
@Composable
private fun LiveCard(
    name: String,
    platform: String,
    title: String,
    state: CheckState?,
    quality: String,
    checkedText: String,
    onClick: () -> Unit,
) {
    val (dotColor, stateText) = when (state) {
        CheckState.LIVE -> Color(0xFF2E9E44) to "在线"
        CheckState.OFFLINE -> Color(0xFF9E9E9E) to "离线"
        CheckState.ERROR -> Color(0xFFD64541) to "异常"
        null -> Color(0xFFBDBDBD) to "未检测"
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // 头像：平台色圆角方块 + 首字 + 在线角标
        Box(modifier = Modifier.size(52.dp)) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(platformColor(platform), RoundedCornerShape(10.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    platformBadge(platform),
                    color = Color.White,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                )
            }
            if (state == CheckState.LIVE) {
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .size(12.dp)
                        .background(Color(0xFF2E9E44), CircleShape),
                )
            }
        }
        Column(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            Text(
                name,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (title.isNotEmpty()) {
                Text(
                    title,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                // 状态胶囊（模仿分类标签胶囊）
                Text(
                    stateText,
                    style = MaterialTheme.typography.labelSmall,
                    color = Color.White,
                    modifier = Modifier
                        .background(dotColor, RoundedCornerShape(4.dp))
                        .padding(horizontal = 6.dp, vertical = 1.dp),
                )
                Text(
                    "$platform · $quality",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (checkedText.isNotEmpty()) {
                    Text(
                        checkedText,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        Spacer(Modifier.width(2.dp))
    }
}
