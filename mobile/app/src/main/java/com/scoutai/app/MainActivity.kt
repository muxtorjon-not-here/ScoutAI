package com.scoutai.app

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.compose.ui.platform.LocalLifecycleOwner
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { ScoutTheme { Root(vm) } }
    }
}

@Composable
fun Root(vm: MainViewModel) {
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        if (vm.signedIn) MainShell(vm) else LoginScreen(vm)
    }
}

// ---------- login ----------

@Composable
fun LoginScreen(vm: MainViewModel) {
    var server by rememberSaveable { mutableStateOf(vm.store.server.ifBlank { "http://192.168.1." }) }
    var password by rememberSaveable { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val go = {
        busy = true; error = null
        vm.login(server, password) { err -> busy = false; error = err }
    }
    Column(
        Modifier.fillMaxSize().systemBarsPadding().imePadding().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(Icons.Filled.TrackChanges, null, Modifier.size(48.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(12.dp))
        Text("ScoutAI", style = MaterialTheme.typography.headlineLarge)
        Text(
            "Connect to your dashboard. Use the address your PC (or cloud server) shows when ScoutAI starts, e.g. http://192.168.1.20:8000 or a Tailscale address.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp, bottom = 24.dp),
        )
        OutlinedTextField(
            server, { server = it }, Modifier.fillMaxWidth(), label = { Text("Server address") },
            singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            password, { password = it }, Modifier.fillMaxWidth(), label = { Text("Dashboard password") },
            singleLine = true, visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        )
        error?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 12.dp)) }
        Spacer(Modifier.height(20.dp))
        Button(go, Modifier.fillMaxWidth().height(52.dp), enabled = !busy) {
            if (busy) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp) else Text("Connect")
        }
        Text(
            "The password is WEB_PASSWORD from the ScoutAI .env file. It's stored encrypted on this phone.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 16.dp),
        )
    }
}

// ---------- shell ----------

private enum class Dest(val label: String) { Board("Board"), Sources("Sources"), Profile("Profile"), Settings("Settings") }

@Composable
fun MainShell(vm: MainViewModel) {
    var dest by rememberSaveable { mutableStateOf(Dest.Board) }
    var showAdd by rememberSaveable { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current

    LaunchedEffect(Unit) {
        vm.events.collect { e ->
            scope.launch {
                snackbar.currentSnackbarData?.dismiss()
                val r = snackbar.showSnackbar(e.message, e.actionLabel, duration = SnackbarDuration.Long)
                if (r == SnackbarResult.ActionPerformed) e.onAction?.invoke()
            }
        }
    }

    // notification permission (Android 13+), asked once after first login
    val permLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) {
        if (!vm.store.askedNotifPermission) {
            vm.store.askedNotifPermission = true
            if (Build.VERSION.SDK_INT >= 33) permLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (vm.store.notifications) PollWorker.schedule(ctx)
    }

    // initial load + periodic refresh while the app is in the foreground
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                vm.refresh(silent = true)
                delay(60_000)
            }
        }
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            if (dest == Dest.Board) FloatingActionButton({ showAdd = true }) { Icon(Icons.Filled.Add, "Add opportunity") }
        },
        bottomBar = {
            NavigationBar {
                val icons = listOf(Icons.Outlined.Inbox, Icons.Outlined.Hub, Icons.Outlined.Person, Icons.Outlined.Settings)
                Dest.entries.forEachIndexed { i, d ->
                    NavigationBarItem(dest == d, { dest = d }, { Icon(icons[i], d.label) }, label = { Text(d.label) })
                }
            }
        },
    ) { pad ->
        Box(Modifier.fillMaxSize().padding(pad).consumeWindowInsets(pad)) {
            when (dest) {
                Dest.Board -> BoardScreen(vm)
                Dest.Sources -> SourcesScreen(vm)
                Dest.Profile -> ProfileScreen(vm)
                Dest.Settings -> SettingsScreen(vm)
            }
        }
    }
    if (showAdd) AddOpportunitySheet(vm) { showAdd = false }
}

// ---------- settings ----------

@Composable
fun SettingsScreen(vm: MainViewModel) {
    val ctx = LocalContext.current
    var notif by remember { mutableStateOf(vm.store.notifications) }
    var status by remember { mutableStateOf<String?>(null) }
    var confirmLogout by remember { mutableStateOf(false) }
    val perm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}

    Column(Modifier.fillMaxSize().statusBarsPadding().verticalScroll(rememberScrollState()).padding(16.dp)) {
        Text("Settings", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.padding(vertical = 8.dp))
        ElevatedCard(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text("Server", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
                Text(vm.store.server, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(vertical = 4.dp))
                status?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton({ status = "Testing…"; vm.testConnection { status = it } }) { Text("Test connection") }
                }
            }
        }
        Spacer(Modifier.height(12.dp))
        ElevatedCard(Modifier.fillMaxWidth()) {
            Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Notifications", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Checks every ~30 min for new opportunities with score 8+ or a deadline within 2 days.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Switch(notif, {
                    notif = it; vm.store.notifications = it
                    if (it) {
                        PollWorker.schedule(ctx)
                        if (Build.VERSION.SDK_INT >= 33) perm.launch(Manifest.permission.POST_NOTIFICATIONS)
                    } else PollWorker.cancel(ctx)
                })
            }
        }
        Spacer(Modifier.height(12.dp))
        Text(
            "Calendar: moving an item to Planned or Working adds it to Google Calendar on the server, nothing to set up here.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(24.dp))
        Button(
            { confirmLogout = true }, Modifier.fillMaxWidth().height(52.dp),
            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.errorContainer, contentColor = MaterialTheme.colorScheme.onErrorContainer),
        ) { Icon(Icons.AutoMirrored.Filled.Logout, null); Spacer(Modifier.width(8.dp)); Text("Log out") }
        Text("ScoutAI mobile 1.0", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 16.dp))
    }
    if (confirmLogout) AlertDialog(
        onDismissRequest = { confirmLogout = false },
        title = { Text("Log out?") },
        text = { Text("The saved server and password will be removed from this phone.") },
        confirmButton = { TextButton({ confirmLogout = false; vm.logout() }) { Text("Log out") } },
        dismissButton = { TextButton({ confirmLogout = false }) { Text("Cancel") } },
    )
}

// ---------- shared small pieces ----------

@Composable
fun EmptyState(icon: androidx.compose.ui.graphics.vector.ImageVector, title: String, body: String, actionLabel: String? = null, action: (() -> Unit)? = null) {
    Column(
        Modifier.fillMaxSize().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
    ) {
        Icon(icon, null, Modifier.size(56.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.height(12.dp))
        Text(title, style = MaterialTheme.typography.titleMedium)
        Text(body, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp))
        if (actionLabel != null && action != null) {
            Spacer(Modifier.height(16.dp))
            FilledTonalButton(action) { Text(actionLabel) }
        }
    }
}
