package com.scoutai.app

import android.content.Intent
import android.net.Uri
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

// ---------- helpers ----------

enum class Level { RED, AMBER, GREEN, BLUE, NONE }
class Badge(val text: String, val level: Level)

fun deadlineBadge(o: Opp, today: LocalDate = LocalDate.now()): Badge {
    val d = o.deadlineDate
    if (d == null) return if (o.deadline.isNotBlank()) Badge(o.deadline.take(24), Level.NONE) else Badge("No deadline", Level.NONE)
    val n = ChronoUnit.DAYS.between(today, d)
    return when {
        n < 0 -> Badge("Overdue ${-n}d", Level.RED)
        n == 0L -> Badge("Due today", Level.RED)
        n == 1L -> Badge("Tomorrow", Level.AMBER)
        n <= 3 -> Badge("$n days left", Level.AMBER)
        n <= 14 -> Badge("$n days left", Level.BLUE)
        else -> Badge("${n / 7} weeks left", Level.GREEN)
    }
}

private fun levelColor(l: Level, neutral: Color) = when (l) {
    Level.RED -> Red; Level.AMBER -> Amber; Level.GREEN -> Green; Level.BLUE -> Blue; Level.NONE -> neutral
}

private fun scoreColor(s: Int, neutral: Color) = when {
    s >= 8 -> Green; s >= 5 -> Amber; else -> neutral
}

fun sortOpps(list: List<Opp>, mode: SortMode, status: String): List<Opp> {
    val byNewest = compareByDescending<Opp> { it.createdAt }
    return when {
        mode == SortMode.SCORE -> list.sortedWith(compareByDescending<Opp> { it.score }.then(byNewest))
        mode == SortMode.NEWEST || status == "FINISHED" || status == "DISMISSED" -> list.sortedWith(byNewest)
        else -> list.sortedWith(
            compareBy<Opp> { it.deadlineDate == null }.thenBy { it.deadlineDate }.then(compareByDescending { it.score }),
        )
    }
}

private fun open(ctx: android.content.Context, url: String) {
    if (url.isBlank()) return
    runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}

/** (button label, target status) for the main action, and swipe-left target. */
private fun primaryAction(status: String): Pair<String, String> = when (status) {
    "UNTRACKED" -> "Plan" to "PLANNED"
    "PLANNED" -> "Start" to "WORKING"
    "WORKING" -> "Finish" to "FINISHED"
    "FINISHED" -> "Reopen" to "WORKING"
    else -> "Restore" to "UNTRACKED"
}

// ---------- screen ----------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BoardScreen(vm: MainViewModel) {
    var searching by remember { mutableStateOf(vm.query.isNotEmpty()) }
    var showFilters by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<Opp?>(null) }
    var deleting by remember { mutableStateOf<Opp?>(null) }

    val counts = remember(vm.opps) { vm.opps.groupingBy { it.status }.eachCount() }
    val categories = remember(vm.opps) {
        vm.opps.filter { it.status != "DISMISSED" }.groupingBy { it.category }.eachCount().entries.sortedByDescending { it.value }.map { it.key }
    }
    val items = remember(vm.opps, vm.tab, vm.query, vm.category, vm.minScore, vm.sort) {
        val q = vm.query.trim().lowercase()
        sortOpps(vm.opps.filter { o ->
            o.status == vm.tab && o.score >= vm.minScore && (vm.category == null || o.category == vm.category) &&
                (q.isEmpty() || listOf(o.title, o.summary, o.category, o.sourceLabel, o.why).any { it.lowercase().contains(q) })
        }, vm.sort, vm.tab)
    }
    val filtersActive = vm.category != null || vm.minScore > 0 || vm.sort != SortMode.DEADLINE

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        if (searching) {
            OutlinedTextField(
                vm.query, { vm.query = it }, Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
                placeholder = { Text("Search title, summary, source") }, singleLine = true,
                leadingIcon = { Icon(Icons.Filled.Search, null) },
                trailingIcon = { IconButton({ vm.query = ""; searching = false }) { Icon(Icons.Filled.Close, "Close search") } },
                shape = RoundedCornerShape(28.dp),
            )
        } else {
            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Board", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
                IconButton({ searching = true }) { Icon(Icons.Filled.Search, "Search") }
                IconButton({ showFilters = true }) {
                    BadgedBox({ if (filtersActive) Badge() }) { Icon(Icons.Outlined.FilterList, "Filter and sort") }
                }
            }
        }
        if (searching) Row(Modifier.padding(horizontal = 12.dp), horizontalArrangement = Arrangement.End) {
            TextButton({ showFilters = true }) { Icon(Icons.Outlined.FilterList, null); Spacer(Modifier.width(6.dp)); Text(if (filtersActive) "Filters on" else "Filters") }
        }

        val tabs = STATUSES
        PrimaryScrollableTabRow(tabs.indexOf(vm.tab), edgePadding = 8.dp, containerColor = MaterialTheme.colorScheme.background) {
            tabs.forEach { s ->
                Tab(vm.tab == s, { vm.tab = s }, text = {
                    Text("${STATUS_LABEL[s]} ${counts[s] ?: 0}", maxLines = 1)
                })
            }
        }

        if (vm.boardError != null && vm.loadedOnce) {
            Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.CloudOff, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onErrorContainer)
                    Spacer(Modifier.width(8.dp))
                    Text("Offline, showing saved data", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onErrorContainer)
                    TextButton({ vm.refresh() }) { Text("Retry") }
                }
            }
        }

        PullToRefreshBox(vm.refreshing, { vm.refresh() }, Modifier.weight(1f)) {
            when {
                !vm.loadedOnce && vm.boardError != null ->
                    EmptyState(Icons.Outlined.CloudOff, "Can't load the board", vm.boardError ?: "", "Retry") { vm.refresh() }
                !vm.loadedOnce && vm.refreshing -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
                items.isEmpty() -> {
                    val filtered = vm.query.isNotBlank() || vm.category != null || vm.minScore > 0
                    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                        Box(Modifier.fillMaxWidth().height(480.dp)) {
                            EmptyState(
                                Icons.Outlined.Inbox,
                                if (filtered) "No matches" else "Nothing in ${STATUS_LABEL[vm.tab]}",
                                if (filtered) "Try clearing the search or filters." else "Pull down to refresh.",
                                if (filtered) "Clear filters" else null,
                                if (filtered) { { vm.query = ""; vm.category = null; vm.minScore = 0 } } else null,
                            )
                        }
                    }
                }
                else -> LazyColumn(
                    Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 8.dp, bottom = 88.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(items, key = { it.id }) { o ->
                        OppCard(o, vm, onEditDeadline = { editing = it }, onDelete = { deleting = it })
                    }
                }
            }
        }
    }

    if (showFilters) FilterSheet(vm, categories) { showFilters = false }
    editing?.let { o -> DeadlineSheet(o, { editing = null }) { vm.setDeadline(o, it); editing = null } }
    deleting?.let { o ->
        AlertDialog(
            onDismissRequest = { deleting = null }, title = { Text("Delete permanently?") },
            text = { Text(o.title) },
            confirmButton = { TextButton({ vm.delete(o); deleting = null }) { Text("Delete") } },
            dismissButton = { TextButton({ deleting = null }) { Text("Cancel") } },
        )
    }
}

// ---------- card ----------

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun OppCard(o: Opp, vm: MainViewModel, onEditDeadline: (Opp) -> Unit, onDelete: (Opp) -> Unit) {
    val ctx = LocalContext.current
    var expanded by rememberSaveable(o.id) { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    val badge = deadlineBadge(o)
    val neutral = MaterialTheme.colorScheme.onSurfaceVariant
    val (pLabel, pTarget) = primaryAction(o.status)
    val canSwipeRight = true
    val canSwipeLeft = o.status != "DISMISSED"

    val swipe = rememberSwipeToDismissBoxState(confirmValueChange = { v ->
        when (v) {
            SwipeToDismissBoxValue.StartToEnd -> vm.setStatus(o, pTarget)
            SwipeToDismissBoxValue.EndToStart -> vm.setStatus(o, "DISMISSED")
            else -> {}
        }
        false
    })

    SwipeToDismissBox(
        state = swipe, modifier = Modifier.fillMaxWidth(),
        enableDismissFromStartToEnd = canSwipeRight, enableDismissFromEndToStart = canSwipeLeft,
        backgroundContent = {
            val right = swipe.dismissDirection == SwipeToDismissBoxValue.StartToEnd
            val active = swipe.dismissDirection != SwipeToDismissBoxValue.Settled
            Box(
                Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp))
                    .background(if (!active) Color.Transparent else if (right) Green.copy(alpha = .25f) else Red.copy(alpha = .25f))
                    .padding(horizontal = 20.dp),
                contentAlignment = if (right) Alignment.CenterStart else Alignment.CenterEnd,
            ) {
                if (active) Text(if (right) pLabel else "Dismiss", fontWeight = FontWeight.SemiBold, color = if (right) Green else Red)
            }
        },
    ) {
        ElevatedCard(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)) {
            Column(Modifier.padding(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    val c = levelColor(badge.level, neutral)
                    Surface(color = c.copy(alpha = .16f), shape = RoundedCornerShape(8.dp)) {
                        Row(Modifier.padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.Schedule, null, Modifier.size(14.dp), tint = c)
                            Spacer(Modifier.width(4.dp))
                            Text(badge.text, style = MaterialTheme.typography.labelMedium, color = c, maxLines = 1)
                        }
                    }
                    Spacer(Modifier.weight(1f))
                    val sc = scoreColor(o.score, neutral)
                    Surface(color = sc.copy(alpha = .16f), shape = RoundedCornerShape(8.dp)) {
                        Row(Modifier.padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Filled.Star, null, Modifier.size(14.dp), tint = sc)
                            Spacer(Modifier.width(3.dp))
                            Text("${o.score}/10", style = MaterialTheme.typography.labelMedium, color = sc)
                        }
                    }
                }
                Text(o.title, style = MaterialTheme.typography.titleMedium, maxLines = if (expanded) 10 else 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 10.dp))
                FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    AssistChip({}, label = { Text(o.category, maxLines = 1) }, enabled = true)
                    if (o.sourceLabel.isNotBlank()) AssistChip(
                        { open(ctx, o.sourceUrl) }, label = { Text(o.sourceLabel, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 180.dp)) },
                        leadingIcon = { Icon(Icons.Outlined.Tag, null, Modifier.size(16.dp)) },
                    )
                }
                if (o.summary.isNotBlank()) Text(
                    o.summary, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = if (expanded) Int.MAX_VALUE else 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 8.dp),
                )
                if (o.why.isNotBlank()) {
                    Row(
                        Modifier.padding(top = 6.dp).clip(RoundedCornerShape(8.dp)).clickable { expanded = !expanded }.padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.width(6.dp))
                        Text("Why it matches", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
                        Icon(if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                    }
                    AnimatedVisibility(expanded) {
                        Surface(color = MaterialTheme.colorScheme.primaryContainer.copy(alpha = .35f), shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth().padding(top = 2.dp)) {
                            Text(o.why, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(10.dp))
                        }
                    }
                }
                if (expanded && o.deadline.isNotBlank()) Text("Deadline: ${o.deadline}", style = MaterialTheme.typography.bodySmall, color = neutral, modifier = Modifier.padding(top = 8.dp))

                Row(Modifier.padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FilledTonalButton({ vm.setStatus(o, pTarget) }, contentPadding = PaddingValues(horizontal = 16.dp)) { Text(pLabel) }
                    if (o.status == "UNTRACKED") OutlinedButton({ vm.setStatus(o, "DISMISSED") }, contentPadding = PaddingValues(horizontal = 14.dp)) { Text("Dismiss") }
                    Spacer(Modifier.weight(1f))
                    if (o.postLink.isNotBlank()) IconButton({ open(ctx, o.postLink) }) { Icon(Icons.AutoMirrored.Filled.OpenInNew, "Open original post") }
                    IconButton({ onEditDeadline(o) }) { Icon(Icons.Outlined.EditCalendar, "Edit deadline") }
                    Box {
                        IconButton({ menu = true }) { Icon(Icons.Filled.MoreVert, "More") }
                        DropdownMenu(menu, { menu = false }) {
                            STATUSES.filter { it != o.status }.forEach { s ->
                                DropdownMenuItem({ Text("Move to ${STATUS_LABEL[s]}") }, { menu = false; vm.setStatus(o, s) })
                            }
                            HorizontalDivider()
                            DropdownMenuItem({ Text("Delete") }, { menu = false; onDelete(o) }, leadingIcon = { Icon(Icons.Outlined.Delete, null) })
                        }
                    }
                }
            }
        }
    }
}

// ---------- sheets ----------

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun FilterSheet(vm: MainViewModel, categories: List<String>, onClose: () -> Unit) {
    ModalBottomSheet(onClose) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp).verticalScroll(rememberScrollState())) {
            Text("Sort by", style = MaterialTheme.typography.titleSmall)
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                SortMode.entries.forEachIndexed { i, m ->
                    SegmentedButton(vm.sort == m, { vm.sort = m }, SegmentedButtonDefaults.itemShape(i, SortMode.entries.size)) { Text(m.label) }
                }
            }
            Text(if (vm.minScore == 0) "Minimum score: any" else "Minimum score: ${vm.minScore}", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 12.dp))
            Slider(vm.minScore.toFloat(), { vm.minScore = it.toInt() }, valueRange = 0f..10f, steps = 9)
            Text("Category", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp, bottom = 4.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(vm.category == null, { vm.category = null }, label = { Text("All") })
                categories.forEach { c -> FilterChip(vm.category == c, { vm.category = if (vm.category == c) null else c }, label = { Text(c) }) }
            }
            Row(Modifier.fillMaxWidth().padding(top = 16.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                TextButton({ vm.category = null; vm.minScore = 0; vm.sort = SortMode.DEADLINE }) { Text("Reset") }
                Button(onClose) { Text("Done") }
            }
        }
    }
}

private val DATE_FMT: DateTimeFormatter = DateTimeFormatter.ofPattern("MMM d, yyyy", Locale.ENGLISH)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DeadlineSheet(o: Opp, onClose: () -> Unit, onSave: (String) -> Unit) {
    var text by remember { mutableStateOf(o.deadline) }
    var picker by remember { mutableStateOf(false) }
    ModalBottomSheet(onClose) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp).imePadding()) {
            Text("Edit deadline", style = MaterialTheme.typography.titleLarge)
            Text(o.title, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedTextField(text, { text = it }, Modifier.fillMaxWidth().padding(top = 12.dp), label = { Text("Deadline (e.g. Nov 15, 2026)") }, singleLine = true)
            Row(Modifier.fillMaxWidth().padding(top = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton({ picker = true }) { Icon(Icons.Outlined.CalendarMonth, null); Spacer(Modifier.width(6.dp)); Text("Pick date") }
                OutlinedButton({ text = "" }) { Text("Clear") }
                Spacer(Modifier.weight(1f))
                Button({ onSave(text) }) { Text("Save") }
            }
        }
    }
    if (picker) {
        val st = rememberDatePickerState(initialSelectedDateMillis = (o.deadlineDate ?: LocalDate.now()).atStartOfDay().toInstant(ZoneOffset.UTC).toEpochMilli())
        DatePickerDialog(
            { picker = false },
            confirmButton = { TextButton({
                st.selectedDateMillis?.let { text = Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate().format(DATE_FMT) }
                picker = false
            }) { Text("OK") } },
            dismissButton = { TextButton({ picker = false }) { Text("Cancel") } },
        ) { DatePicker(st) }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AddOpportunitySheet(vm: MainViewModel, onClose: () -> Unit) {
    var title by remember { mutableStateOf("") }
    var category by remember { mutableStateOf("") }
    var deadline by remember { mutableStateOf("") }
    var score by remember { mutableStateOf(5f) }
    var link by remember { mutableStateOf("") }
    var summary by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("UNTRACKED") }
    var err by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var picker by remember { mutableStateOf(false) }

    ModalBottomSheet(onClose, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp).imePadding().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Add opportunity", style = MaterialTheme.typography.titleLarge)
            OutlinedTextField(title, { title = it }, Modifier.fillMaxWidth(), label = { Text("Title *") })
            OutlinedTextField(category, { category = it }, Modifier.fillMaxWidth(), label = { Text("Category (default Manual)") }, singleLine = true)
            OutlinedTextField(
                deadline, { deadline = it }, Modifier.fillMaxWidth(), label = { Text("Deadline (e.g. Nov 15, 2026)") }, singleLine = true,
                trailingIcon = { IconButton({ picker = true }) { Icon(Icons.Outlined.CalendarMonth, "Pick date") } },
            )
            OutlinedTextField(link, { link = it }, Modifier.fillMaxWidth(), label = { Text("Link (https://…)") }, singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
            OutlinedTextField(summary, { summary = it }, Modifier.fillMaxWidth(), label = { Text("Notes") }, minLines = 2, maxLines = 5)
            Text("Score: ${score.toInt()}", style = MaterialTheme.typography.titleSmall)
            Slider(score, { score = it }, valueRange = 0f..10f, steps = 9)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("UNTRACKED", "PLANNED", "WORKING").forEach { s -> FilterChip(status == s, { status = s }, label = { Text(STATUS_LABEL[s] ?: s) }) }
            }
            err?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Button({
                when {
                    title.isBlank() -> err = "Title is required"
                    link.isNotBlank() && !(link.startsWith("http://") || link.startsWith("https://")) -> err = "Link must start with http:// or https://"
                    else -> {
                        err = null; busy = true
                        vm.create(title, category, deadline, score.toInt(), link, summary, status) { e -> busy = false; if (e == null) onClose() else err = e }
                    }
                }
            }, Modifier.fillMaxWidth().height(52.dp), enabled = !busy) { Text("Add") }
        }
    }
    if (picker) {
        val st = rememberDatePickerState()
        DatePickerDialog(
            { picker = false },
            confirmButton = { TextButton({
                st.selectedDateMillis?.let { deadline = Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate().format(DATE_FMT) }
                picker = false
            }) { Text("OK") } },
            dismissButton = { TextButton({ picker = false }) { Text("Cancel") } },
        ) { DatePicker(st) }
    }
}
