package com.scoutai.app

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.launch

class UiEvent(val message: String, val actionLabel: String? = null, val onAction: (() -> Unit)? = null)

enum class SortMode(val label: String) { DEADLINE("Deadline"), SCORE("Score"), NEWEST("Newest") }

class MainViewModel(app: Application) : AndroidViewModel(app) {
    val store = Store(app)
    val api = Api(store)
    val events = MutableSharedFlow<UiEvent>(extraBufferCapacity = 16)

    var signedIn by mutableStateOf(store.loggedIn)
        private set

    // ----- board -----
    var opps by mutableStateOf(runCatching { parseOpps(store.cachedOpps) }.getOrDefault(emptyList()))
        private set
    var refreshing by mutableStateOf(false)
        private set
    var loadedOnce by mutableStateOf(opps.isNotEmpty())
        private set
    var boardError by mutableStateOf<String?>(null)
        private set
    var tab by mutableStateOf("UNTRACKED")
    var query by mutableStateOf("")
    var category by mutableStateOf<String?>(null)
    var minScore by mutableStateOf(0)
    var sort by mutableStateOf(SortMode.DEADLINE)

    // ----- sources -----
    var channels by mutableStateOf<List<Channel>>(emptyList())
        private set
    var webSources by mutableStateOf<List<WebSource>>(emptyList())
        private set
    var sourcesError by mutableStateOf<String?>(null)
        private set
    var sourcesLoading by mutableStateOf(false)
        private set
    var scanning by mutableStateOf(false)
        private set
    var busyAdd by mutableStateOf(false)
        private set

    // ----- profile -----
    var profile by mutableStateOf<String?>(null)
        private set
    var profileError by mutableStateOf<String?>(null)
        private set

    private fun toast(msg: String, label: String? = null, action: (() -> Unit)? = null) {
        events.tryEmit(UiEvent(msg, label, action))
    }

    private fun fail(e: Throwable): String {
        if (e is AuthException) { signedIn = false }
        return e.message ?: "Something went wrong"
    }

    // ---------- auth ----------

    fun login(server: String, password: String, onDone: (String?) -> Unit) {
        val url = normalizeServer(server)
        if (url.isBlank() || password.isBlank()) { onDone("Enter the server address and password."); return }
        viewModelScope.launch {
            try {
                val token = api.login(url, password)
                store.server = url; store.token = token; store.password = password
                signedIn = true
                onDone(null)
                refresh()
            } catch (e: Exception) { onDone(e.message ?: "Login failed") }
        }
    }

    fun logout() {
        store.logout()
        PollWorker.cancel(getApplication())
        opps = emptyList(); channels = emptyList(); webSources = emptyList(); profile = null
        loadedOnce = false; boardError = null
        signedIn = false
    }

    fun testConnection(onDone: (String) -> Unit) = viewModelScope.launch {
        onDone(try { api.ping(); "Connected to ${store.server}" } catch (e: Exception) { fail(e) })
    }

    // ---------- board ----------

    fun refresh(silent: Boolean = false) {
        if (refreshing || !signedIn) return
        viewModelScope.launch {
            refreshing = !silent || !loadedOnce
            try {
                val (list, raw) = api.opportunities()
                opps = list; store.cachedOpps = raw; loadedOnce = true; boardError = null
            } catch (e: Exception) {
                boardError = fail(e)
            } finally { refreshing = false }
        }
    }

    private fun replace(id: Int, f: (Opp) -> Opp) { opps = opps.map { if (it.id == id) f(it) else it } }

    fun setStatus(o: Opp, status: String, undo: Boolean = true) {
        if (o.status == status) return
        val before = o.status
        replace(o.id) { it.copy(status = status) }
        viewModelScope.launch {
            try {
                api.setStatus(o.id, status)
                if (undo) toast("Moved to ${STATUS_LABEL[status]}", "Undo") { setStatus(o.copy(status = status), before, undo = false) }
            } catch (e: Exception) {
                replace(o.id) { it.copy(status = before) }
                toast(fail(e))
            }
        }
    }

    fun setDeadline(o: Opp, text: String) {
        viewModelScope.launch {
            try {
                val upd = api.setDeadline(o.id, text.trim())
                replace(o.id) { upd.copy(sourceLabel = it.sourceLabel, sourceUrl = it.sourceUrl) }
                toast(if (text.isBlank()) "Deadline cleared" else "Deadline saved")
            } catch (e: Exception) { toast(fail(e)) }
        }
    }

    fun delete(o: Opp) {
        viewModelScope.launch {
            try {
                api.delete(o.id)
                opps = opps.filter { it.id != o.id }
                toast("Deleted")
            } catch (e: Exception) { toast(fail(e)) }
        }
    }

    fun create(title: String, category: String, deadline: String, score: Int, link: String, summary: String, status: String, onDone: (String?) -> Unit) {
        viewModelScope.launch {
            try {
                api.createOpportunity(title.trim(), category.trim(), deadline.trim(), score, link.trim(), summary.trim(), status)
                onDone(null)
                toast("Added")
                refresh(silent = true)
            } catch (e: Exception) { onDone(fail(e)) }
        }
    }

    // ---------- sources ----------

    fun loadSources() {
        viewModelScope.launch {
            sourcesLoading = true
            try {
                channels = api.channels(); webSources = api.webSources(); sourcesError = null
            } catch (e: Exception) { sourcesError = fail(e) } finally { sourcesLoading = false }
        }
    }

    fun addChannels(text: String, onDone: (Boolean) -> Unit) {
        viewModelScope.launch {
            busyAdd = true
            try {
                val r = api.addChannels(text)
                val parts = buildList {
                    if (r.added.isNotEmpty()) add("Added ${r.added.size}")
                    if (r.already.isNotEmpty()) add("${r.already.size} already monitored")
                    if (r.failed.isNotEmpty()) add("Failed: ${r.failed.joinToString("; ")}")
                }
                toast(parts.joinToString(". ").ifBlank { "Nothing to add" })
                channels = api.channels()
                onDone(r.added.isNotEmpty() || r.already.isNotEmpty())
            } catch (e: Exception) { toast(fail(e)); onDone(false) } finally { busyAdd = false }
        }
    }

    fun removeChannel(c: Channel) {
        viewModelScope.launch {
            try { api.removeChannel(c.key); channels = channels.filter { it.key != c.key }; toast("Removed ${c.label}") }
            catch (e: Exception) { toast(fail(e)) }
        }
    }

    fun toggleWebSource(s: WebSource, enabled: Boolean) {
        webSources = webSources.map { if (it.id == s.id) it.copy(enabled = enabled) else it }
        viewModelScope.launch {
            try { api.setWebSourceEnabled(s.id, enabled) } catch (e: Exception) {
                webSources = webSources.map { if (it.id == s.id) it.copy(enabled = !enabled) else it }
                toast(fail(e))
            }
        }
    }

    fun addWebSource(url: String, onDone: (Boolean) -> Unit) {
        viewModelScope.launch {
            busyAdd = true
            try {
                api.addWebSource(url.trim()); webSources = api.webSources(); toast("Website added"); onDone(true)
            } catch (e: Exception) { toast(fail(e)); onDone(false) } finally { busyAdd = false }
        }
    }

    fun removeWebSource(s: WebSource) {
        viewModelScope.launch {
            try { api.removeWebSource(s.id); webSources = webSources.filter { it.id != s.id }; toast("Removed ${s.name}") }
            catch (e: Exception) { toast(fail(e)) }
        }
    }

    fun scanNow() {
        if (scanning) return
        viewModelScope.launch {
            scanning = true
            toast("Scanning… this can take up to a minute")
            try {
                val r = api.scan()
                toast(if (r.running) "Scan is still running on the server. Pull to refresh in a minute."
                else "Scan finished: ${r.evaluated ?: 0} evaluated")
                refresh(silent = true)
            } catch (e: Exception) { toast(fail(e)) } finally { scanning = false }
        }
    }

    // ---------- profile ----------

    fun loadProfile() {
        viewModelScope.launch {
            try { profile = api.profile(); profileError = null } catch (e: Exception) { profileError = fail(e) }
        }
    }

    fun saveProfile(text: String, onDone: (Boolean) -> Unit) {
        viewModelScope.launch {
            try { profile = api.saveProfile(text); toast("Profile saved"); onDone(true) }
            catch (e: Exception) { toast(fail(e)); onDone(false) }
        }
    }
}
