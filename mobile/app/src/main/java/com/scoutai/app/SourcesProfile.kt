package com.scoutai.app

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

// ---------- sources ----------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SourcesScreen(vm: MainViewModel) {
    val ctx = LocalContext.current
    var paste by remember { mutableStateOf("") }
    var rss by remember { mutableStateOf("") }
    var removeCh by remember { mutableStateOf<Channel?>(null) }
    var removeWeb by remember { mutableStateOf<WebSource?>(null) }
    LaunchedEffect(Unit) { vm.loadSources() }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 12.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Sources", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
            FilledTonalButton({ vm.scanNow() }, enabled = !vm.scanning) {
                if (vm.scanning) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp) else Icon(Icons.Filled.Radar, null, Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp)); Text(if (vm.scanning) "Scanning…" else "Scan now")
            }
        }
        PullToRefreshBox(vm.sourcesLoading, { vm.loadSources() }, Modifier.weight(1f)) {
            if (vm.sourcesError != null && vm.channels.isEmpty() && vm.webSources.isEmpty()) {
                EmptyState(Icons.Outlined.CloudOff, "Can't load sources", vm.sourcesError ?: "", "Retry") { vm.loadSources() }
            } else LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp, 8.dp, 16.dp, 32.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                item { SectionTitle("Telegram channels, groups & topics", Icons.Outlined.Send) }
                item {
                    OutlinedTextField(
                        paste, { paste = it }, Modifier.fillMaxWidth(), minLines = 2, maxLines = 5,
                        label = { Text("Paste @names or t.me links") },
                        supportingText = { Text("One per line or separated by spaces/commas") },
                    )
                    Button({ vm.addChannels(paste) { ok -> if (ok) paste = "" } }, Modifier.padding(top = 6.dp), enabled = paste.isNotBlank() && !vm.busyAdd) {
                        if (vm.busyAdd) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp) else Text("Add channels")
                    }
                }
                if (vm.channels.isEmpty() && !vm.sourcesLoading) item { Text("No channels monitored yet.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
                items(vm.channels, key = { "c" + it.key }) { c ->
                    ListItem(
                        headlineContent = { Text(c.label, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                        supportingContent = { Text(c.key, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                        trailingContent = {
                            Row {
                                if (c.url.isNotBlank()) IconButton({ runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(c.url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } }) {
                                    Icon(Icons.AutoMirrored.Filled.OpenInNew, "Open in Telegram")
                                }
                                IconButton({ removeCh = c }) { Icon(Icons.Outlined.Delete, "Remove") }
                            }
                        },
                        colors = ListItemDefaults.colors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
                        modifier = Modifier.padding(0.dp),
                    )
                }

                item { Spacer(Modifier.height(12.dp)); SectionTitle("Opportunity websites", Icons.Outlined.Language) }
                items(vm.webSources, key = { "w" + it.id }) { s ->
                    ListItem(
                        headlineContent = { Text(s.name, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                        supportingContent = { Text(listOf(s.kind, "${s.found} found").filter { it.isNotBlank() }.joinToString(" · ")) },
                        trailingContent = {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                if (s.custom) IconButton({ removeWeb = s }) { Icon(Icons.Outlined.Delete, "Remove") }
                                Switch(s.enabled, { vm.toggleWebSource(s, it) })
                            }
                        },
                        colors = ListItemDefaults.colors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
                    )
                }
                item {
                    OutlinedTextField(
                        rss, { rss = it }, Modifier.fillMaxWidth().padding(top = 8.dp), singleLine = true,
                        label = { Text("Add RSS / Atom feed URL") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    )
                    Button({ vm.addWebSource(rss) { ok -> if (ok) rss = "" } }, Modifier.padding(top = 6.dp), enabled = rss.isNotBlank() && !vm.busyAdd) { Text("Add website") }
                }
            }
        }
    }
    removeCh?.let { c ->
        AlertDialog({ removeCh = null }, title = { Text("Stop monitoring?") }, text = { Text(c.label) },
            confirmButton = { TextButton({ vm.removeChannel(c); removeCh = null }) { Text("Remove") } },
            dismissButton = { TextButton({ removeCh = null }) { Text("Cancel") } })
    }
    removeWeb?.let { s ->
        AlertDialog({ removeWeb = null }, title = { Text("Remove website?") }, text = { Text(s.name) },
            confirmButton = { TextButton({ vm.removeWebSource(s); removeWeb = null }) { Text("Remove") } },
            dismissButton = { TextButton({ removeWeb = null }) { Text("Cancel") } })
    }
}

@Composable
private fun SectionTitle(text: String, icon: androidx.compose.ui.graphics.vector.ImageVector) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
        Icon(icon, null, Modifier.size(20.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.width(8.dp))
        Text(text, style = MaterialTheme.typography.titleMedium)
    }
}

// ---------- profile ----------

@Composable
fun ProfileScreen(vm: MainViewModel) {
    var text by remember { mutableStateOf("") }
    var original by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { vm.loadProfile() }
    LaunchedEffect(vm.profile) {
        val p = vm.profile
        if (p != null && (original == null || text == original)) { text = p; original = p }
    }
    val dirty = original != null && text != original

    Column(Modifier.fillMaxSize().statusBarsPadding().padding(horizontal = 16.dp)) {
        Text("AI profile", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.padding(vertical = 8.dp))
        when {
            vm.profile == null && vm.profileError != null -> EmptyState(Icons.Outlined.CloudOff, "Can't load profile", vm.profileError ?: "", "Retry") { vm.loadProfile() }
            vm.profile == null -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
            else -> {
                Text(
                    "Describe yourself and what you're looking for. The AI scores every opportunity against this.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedTextField(text, { text = it }, Modifier.fillMaxWidth().weight(1f).padding(vertical = 10.dp))
                Button(
                    { saving = true; vm.saveProfile(text) { saving = false; if (it) original = text } },
                    Modifier.fillMaxWidth().height(52.dp).padding(bottom = 0.dp), enabled = dirty && text.isNotBlank() && !saving,
                ) { Text(if (saving) "Saving…" else "Save profile") }
                Spacer(Modifier.height(12.dp))
            }
        }
    }
}
