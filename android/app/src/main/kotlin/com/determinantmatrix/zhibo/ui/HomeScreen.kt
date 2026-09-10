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
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Scaffold
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import kotlin.math.abs
import kotlin.math.roundToInt
import com.determinantmatrix.zhibo.FollowersViewModel
import com.determinantmatrix.zhibo.ZhiboApp
import com.determinantmatrix.zhibo.core.monitor.CheckState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** 平台品牌色（头像底色）。 */
fun platformColor(platform: String): Color = when (platform.lowercase()) {
    "bilibili" -> Color(0xFF00A1D6)
    "douyu" -> Color(0xFFFF5D23)
    "huya" -> Color(0xFFFF7C00)
    "douyin" -> Color(0xFF161823)
    "twitch" -> Color(0xFF9146FF)
    "youtube" -> Color(0xFFE62117)
    "fs1" -> Color(0xFF7C4DFF)
    else -> Color(0xFF607D8B)
}

fun platformBadge(platform: String): String = when (platform.lowercase()) {
    "bilibili" -> "B"
    "douyu" -> "斗"
    "huya" -> "虎"
    "douyin" -> "抖"
    "twitch" -> "T"
    "youtube" -> "Y"
    "fs1" -> "F"
    else -> "直"
}

internal fun relativeTime(checkedAtMillis: Long, nowMillis: Long = System.currentTimeMillis()): String {
    val delta = ((nowMillis - checkedAtMillis) / 1000).coerceAtLeast(0)
    return when {
        delta < 60 -> "刚刚"
        delta < 3600 -> "${delta / 60}分钟前"
        delta < 86400 -> "${delta / 3600}小时前"
        else -> "${delta / 86400}天前"
    }
}

private val playScope = CoroutineScope(Dispatchers.Main + SupervisorJob())

/**
 * 首页：顶部标签 chips（按关注标签分组）+ 直播间卡片流 + "添加关注"。
 * 卡片交互：轻点=播放（在线）；右滑到底=删除；左滑到底=编辑。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(vm: FollowersViewModel = androidx.lifecycle.viewmodel.compose.viewModel()) {
    val rows by vm.followers.collectAsState()
    val statuses by ZhiboApp.instance.engineController.engine.statuses.collectAsState()
    val message by vm.message.collectAsState()

    var selectedTag by remember { mutableStateOf("全部") }
    var editTarget by remember { mutableStateOf<FollowersViewModel.FollowerRow?>(null) }
    val now = remember(rows) { System.currentTimeMillis() }

    val tags = remember(rows) {
        val set = linkedSetOf<String>()
        rows.forEach { r -> r.follower.tags.forEach { set.add(it) } }
        listOf("全部") + set.toList()
    }
    val visible = if (selectedTag == "全部") {
        rows
    } else {
        rows.filter { r -> selectedTag in r.follower.tags }
    }

    val clickScope = rememberCoroutineScope()
    val pickFollowersCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let(vm::importFollowersCsv) }

    Scaffold(
        topBar = {
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
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(rows, key = { it.id }) { row ->
                    val status = statuses[row.follower.url]
                    LiveSwipeCard(
                        row = row,
                        status = status,
                        now = now,
                        onPlay = { clickScope.launch { ZhiboApp.instance.playerManager.play(row.follower) } },
                        onEdit = { editTarget = row },
                        onDelete = { vm.deleteFollower(row.id) },
                        onClick = {
                            if (status?.state == CheckState.LIVE) {
                                clickScope.launch { ZhiboApp.instance.playerManager.play(row.follower) }
                            } else {
                                vm.notify(
                                    row.follower.name + if (row.follower.enabled) " 未开播或未检测到在线" else " 已停用",
                                )
                            }
                        },
                    )
                }
            }
        }
    }

    editTarget?.let { row ->
        EditFollowerDialog(
            initial = row.follower,
            onDismiss = { editTarget = null },
            onSave = { edited ->
                vm.updateFollower(row.id, edited)
                editTarget = null
            },
        )
    }
}

/**
 * 可滑动卡片：右滑过半松手=删除；左滑过半松手=编辑（弹回并打开编辑框）。
 * 自研拖动（detectHorizontalDragGestures），不依赖 material3 SwipeToDismissBox。
 */
@Composable
private fun LiveSwipeCard(
    row: FollowersViewModel.FollowerRow,
    status: com.determinantmatrix.zhibo.core.monitor.FollowerStatus?,
    now: Long,
    onPlay: () -> Unit,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    onClick: () -> Unit,
) {
    val f = row.follower
    var offsetX by remember { mutableFloatStateOf(0f) }

    Box(modifier = Modifier.fillMaxSize()) {
        // 滑动背景：右滑红色删除、左滑紫色编辑
        if (abs(offsetX) > 8f) {
            Box(
                modifier = Modifier
                    .matchParentSize()
                    .background(if (offsetX > 0) Color(0xFFD64541) else Color(0xFF5B5BD6))
                    .padding(horizontal = 28.dp),
                contentAlignment = if (offsetX > 0) Alignment.CenterStart else Alignment.CenterEnd,
            ) {
                Text(
                    if (offsetX > 0) "松手删除" else "松手编辑",
                    color = Color.White,
                    style = MaterialTheme.typography.titleMedium,
                )
            }
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .offset { IntOffset(offsetX.roundToInt(), 0) }
                .pointerInput(row.id) {
                    detectHorizontalDragGestures(
                        onHorizontalDrag = { change, dragAmount ->
                            change.consume()
                            offsetX = (offsetX + dragAmount).coerceIn(-720f, 720f)
                        },
                        onDragEnd = {
                            if (offsetX >= 560f) onDelete() else if (offsetX <= -560f) onEdit()
                            offsetX = 0f
                        },
                    )
                }
                .background(MaterialTheme.colorScheme.surface)
                .clickable(onClick = onClick)
                .padding(horizontal = 14.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            LiveCard(
                name = f.name,
                platform = f.platform,
                title = status?.title.orEmpty(),
                state = status?.state,
                quality = f.quality,
                enabled = f.enabled,
                checkedText = status?.checkedAtMillis?.takeIf { it > 0 }
                    ?.let { relativeTime(it, now) } ?: "",
                onClick = onClick,
                onPlay = onPlay,
            )
        }
    }
}

/** 单张直播间卡片 — 布局对齐 nodyssey 的帖子行，右侧显式播放按钮。 */
@Composable
private fun LiveCard(
    name: String,
    platform: String,
    title: String,
    state: CheckState?,
    quality: String,
    enabled: Boolean,
    checkedText: String,
    onClick: () -> Unit,
    onPlay: () -> Unit,
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
        verticalAlignment = Alignment.CenterVertically,
    ) {
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
        if (state == CheckState.LIVE) {
            IconButton(onClick = onPlay) {
                Icon(
                    Icons.Filled.PlayArrow,
                    contentDescription = "播放",
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(32.dp),
                )
            }
        }
        if (!enabled) {
            Text(
                "已停用",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
