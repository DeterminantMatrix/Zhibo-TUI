package com.determinantmatrix.zhibo

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme {
                FollowersScreen()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FollowersScreen(vm: FollowersViewModel = viewModel()) {
    val followers by vm.followers.collectAsState()
    val settings by vm.settings.collectAsState()
    val message by vm.message.collectAsState()

    val pickFollowersCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let(vm::importFollowersCsv) }

    val saveFollowersCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("text/csv"),
    ) { uri -> uri?.let(vm::exportFollowersCsv) }

    val pickSettingsCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> uri?.let(vm::importSettingsCsv) }

    val saveSettingsCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("text/csv"),
    ) { uri -> uri?.let(vm::exportSettingsCsv) }

    Scaffold(
        topBar = { TopAppBar(title = { Text("直播监控 · M0") }) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = "数据互通验证：导入桌面版 followers.csv，导出后应与桌面序列化逐字节一致。",
                style = MaterialTheme.typography.bodySmall,
            )

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { pickFollowersCsv.launch(arrayOf("text/*", "text/csv", "application/csv", "text/comma-separated-values")) }) {
                    Text("导入关注 CSV")
                }
                Button(onClick = { saveFollowersCsv.launch("followers.csv") }) {
                    Text("导出关注 CSV")
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { pickSettingsCsv.launch(arrayOf("text/*", "text/csv", "application/csv", "text/comma-separated-values")) }) {
                    Text("导入设置 CSV")
                }
                Button(onClick = { saveSettingsCsv.launch("settings.csv") }) {
                    Text("导出设置 CSV")
                }
            }

            Text(
                text = "状态：$message",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                text = "设置：轮询 ${settings.pollInterval}s · 并发 ${settings.maxConcurrentChecks} · 通知 ${if (settings.notificationsEnabled) "开" else "关"}",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text("关注项（${followers.size}）", style = MaterialTheme.typography.titleMedium)

            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(followers) { f ->
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Row(
                            modifier = Modifier.padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(10.dp)
                                    .background(
                                        if (f.enabled) Color(0xFF2E9E44) else Color(0xFFB0B0B0),
                                        CircleShape,
                                    ),
                            )
                            Column {
                                Text(f.name, style = MaterialTheme.typography.bodyLarge)
                                Text(
                                    text = "${f.platform} · ${f.plugin} · ${f.quality} · ${f.tags.joinToString("/")}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
