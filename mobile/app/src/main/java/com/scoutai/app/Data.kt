package com.scoutai.app

import android.content.Context
import android.net.Uri
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.time.LocalDate
import java.util.concurrent.TimeUnit

// ---------- models ----------

val STATUSES = listOf("UNTRACKED", "PLANNED", "WORKING", "FINISHED", "DISMISSED")
val STATUS_LABEL = mapOf(
    "UNTRACKED" to "Inbox", "PLANNED" to "Planned", "WORKING" to "Working",
    "FINISHED" to "Finished", "DISMISSED" to "Dismissed",
)

data class Opp(
    val id: Int, val title: String, val category: String, val channelSource: String,
    val sourceLabel: String, val sourceUrl: String, val deadline: String, val deadlineDate: LocalDate?,
    val score: Int, val urgency: String, val postLink: String, val summary: String,
    val why: String, val status: String, val createdAt: String,
)

data class Channel(val key: String, val label: String, val url: String)
data class WebSource(
    val id: String, val name: String, val kind: String, val urls: List<String>,
    val enabled: Boolean, val custom: Boolean, val found: Int,
)
data class ChannelResult(val added: List<String>, val already: List<String>, val failed: List<String>)
data class ScanResult(val running: Boolean, val evaluated: Int?)

private fun JSONObject.str(key: String): String = if (isNull(key)) "" else optString(key, "")

fun JSONObject.toOpp(): Opp {
    val dd = str("deadline_date").take(10)
    return Opp(
        id = optInt("id"), title = str("title"), category = str("category").ifBlank { "Other" },
        channelSource = str("channel_source"), sourceLabel = str("source_label"), sourceUrl = str("source_url"),
        deadline = str("deadline"), deadlineDate = runCatching { LocalDate.parse(dd) }.getOrNull(),
        score = optInt("score"), urgency = str("urgency"), postLink = str("post_link"),
        summary = str("summary"), why = str("why_it_matches"), status = str("status").ifBlank { "UNTRACKED" },
        createdAt = str("created_at"),
    )
}

fun parseOpps(raw: String): List<Opp> {
    val arr = JSONObject(raw).optJSONArray("opportunities") ?: JSONArray()
    return (0 until arr.length()).map { arr.getJSONObject(it).toOpp() }
}

private fun JSONArray?.strings(): List<String> = this?.let { a -> (0 until a.length()).map { a.optString(it) } } ?: emptyList()

// ---------- secure storage ----------

class Store(ctx: Context) {
    private val app = ctx.applicationContext
    private val plain = app.getSharedPreferences("scout_prefs", Context.MODE_PRIVATE)
    private val secure = openSecure()

    private fun openSecure() = try {
        create()
    } catch (e: Exception) { // corrupted keystore after restore/OS change: start clean, user logs in again
        app.deleteSharedPreferences("scout_secure")
        create()
    }

    private fun create() = EncryptedSharedPreferences.create(
        app, "scout_secure", MasterKey.Builder(app).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var server: String
        get() = secure.getString("server", "") ?: ""
        set(v) = secure.edit().putString("server", v).apply()
    var token: String
        get() = secure.getString("token", "") ?: ""
        set(v) = secure.edit().putString("token", v).apply()
    var password: String
        get() = secure.getString("password", "") ?: ""
        set(v) = secure.edit().putString("password", v).apply()

    var notifications: Boolean
        get() = plain.getBoolean("notifications", true)
        set(v) = plain.edit().putBoolean("notifications", v).apply()
    var askedNotifPermission: Boolean
        get() = plain.getBoolean("asked_notif", false)
        set(v) = plain.edit().putBoolean("asked_notif", v).apply()
    var cachedOpps: String
        get() = plain.getString("cache_opps", "") ?: ""
        set(v) = plain.edit().putString("cache_opps", v).apply()
    var notifiedIds: Set<String>
        get() = plain.getStringSet("notified", emptySet()) ?: emptySet()
        set(v) = plain.edit().putStringSet("notified", v).apply()
    var pollBaselineDone: Boolean
        get() = plain.getBoolean("poll_baseline", false)
        set(v) = plain.edit().putBoolean("poll_baseline", v).apply()

    val loggedIn get() = server.isNotBlank() && token.isNotBlank()

    fun logout() {
        secure.edit().remove("token").remove("password").apply()
        plain.edit().remove("cache_opps").remove("notified").remove("poll_baseline").apply()
    }
}

fun normalizeServer(raw: String): String {
    var s = raw.trim().trimEnd('/')
    if (s.isEmpty()) return s
    if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://$s"
    // bare host or IP without a port: the dashboard's default is 8000
    val authority = s.substringAfter("://").substringBefore('/')
    if (!authority.contains(':') && !s.startsWith("https://")) s = "$s:8000"
    return s
}

// ---------- API ----------

class ApiException(val code: Int, message: String) : Exception(message)
class AuthException : Exception("Signed out. Please log in again.")
class OfflineException(cause: Throwable?) :
    Exception("Can't reach the server. Is your phone on the same Wi-Fi / Tailscale, and is ScoutAI running?", cause)

class Api(private val store: Store) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).writeTimeout(15, TimeUnit.SECONDS).build()
    private val slowClient = client.newBuilder().readTimeout(120, TimeUnit.SECONDS).build()
    private val loginLock = Mutex()
    private val json = "application/json".toMediaType()

    private fun detail(body: String, fallback: String): String =
        runCatching { JSONObject(body).opt("detail") as? String }.getOrNull()?.takeIf { it.isNotBlank() } ?: fallback

    private fun exec(c: OkHttpClient, server: String, method: String, path: String, body: JSONObject?, token: String?): Pair<Int, String> {
        val rb = when {
            method == "GET" || method == "DELETE" -> null
            else -> (body ?: JSONObject()).toString().toRequestBody(json)
        }
        val req = Request.Builder().url(server + path).method(method, rb)
            .apply { if (!token.isNullOrBlank()) header("Authorization", "Bearer $token") }.build()
        return try {
            c.newCall(req).execute().use { it.code to (it.body?.string() ?: "") }
        } catch (e: IllegalArgumentException) {
            throw ApiException(0, "That server address doesn't look valid.")
        } catch (e: IOException) {
            throw OfflineException(e)
        }
    }

    /** POST /api/login. Returns the Bearer token. */
    suspend fun login(server: String, password: String): String = withContext(Dispatchers.IO) {
        val (code, body) = exec(client, server, "POST", "/api/login", JSONObject().put("password", password), null)
        when {
            code == 200 -> JSONObject(body).optString("token").ifBlank { throw ApiException(code, "Server sent no token") }
            code == 401 -> throw ApiException(code, "Wrong password")
            code == 429 -> throw ApiException(code, "Too many attempts. Wait a minute.")
            else -> throw ApiException(code, detail(body, "Server answered HTTP $code. Is that the ScoutAI address?"))
        }
    }

    private suspend fun call(method: String, path: String, body: JSONObject? = null, slow: Boolean = false): String =
        withContext(Dispatchers.IO) {
            val c = if (slow) slowClient else client
            var (code, text) = exec(c, store.server, method, path, body, store.token)
            if (code == 401) { // token expired (server restarted / password changed): re-login silently once
                val stale = store.token
                loginLock.withLock {
                    if (store.token == stale) {
                        val pw = store.password
                        if (pw.isBlank()) throw AuthException()
                        try { store.token = login(store.server, pw) } catch (e: ApiException) {
                            if (e.code == 401) store.token = ""
                            throw AuthException()
                        }
                    }
                }
                val r = exec(c, store.server, method, path, body, store.token)
                code = r.first; text = r.second
                if (code == 401) throw AuthException()
            }
            if (code !in 200..299) throw ApiException(code, detail(text, "Server error (HTTP $code)"))
            text
        }

    private fun seg(key: String) = key.split("/").joinToString("/") { Uri.encode(it) }

    suspend fun ping() { call("GET", "/api/ping") }

    suspend fun opportunities(): Pair<List<Opp>, String> {
        val raw = call("GET", "/api/opportunities")
        return parseOpps(raw) to raw
    }

    suspend fun createOpportunity(title: String, category: String, deadline: String, score: Int, link: String, summary: String, status: String) {
        call("POST", "/api/opportunities", JSONObject().put("title", title).put("category", category.ifBlank { "Manual" })
            .put("deadline", deadline).put("score", score).put("post_link", link).put("summary", summary).put("status", status))
    }

    suspend fun setStatus(id: Int, status: String) { call("PATCH", "/api/opportunities/$id/status", JSONObject().put("status", status)) }
    suspend fun setDeadline(id: Int, deadline: String): Opp =
        JSONObject(call("PATCH", "/api/opportunities/$id/deadline", JSONObject().put("deadline", deadline))).toOpp()
    suspend fun delete(id: Int) { call("DELETE", "/api/opportunities/$id") }

    suspend fun channels(): List<Channel> {
        val arr = JSONObject(call("GET", "/api/channels")).optJSONArray("channels") ?: JSONArray()
        return (0 until arr.length()).map {
            val o = arr.getJSONObject(it)
            Channel(o.str("key"), o.str("label").ifBlank { o.str("key") }, o.str("url"))
        }
    }
    suspend fun addChannels(text: String): ChannelResult {
        val o = JSONObject(call("POST", "/api/channels", JSONObject().put("channels", text), slow = true))
        return ChannelResult(o.optJSONArray("added").strings(), o.optJSONArray("already").strings(), o.optJSONArray("failed").strings())
    }
    suspend fun removeChannel(key: String) { call("DELETE", "/api/channels/${seg(key)}") }

    suspend fun webSources(): List<WebSource> {
        val arr = JSONObject(call("GET", "/api/web_sources")).optJSONArray("sources") ?: JSONArray()
        return (0 until arr.length()).map {
            val o = arr.getJSONObject(it)
            WebSource(o.str("id"), o.str("name"), o.str("kind"), o.optJSONArray("urls").strings(),
                o.optBoolean("enabled"), o.optBoolean("custom"), o.optInt("found"))
        }
    }
    suspend fun addWebSource(url: String) { call("POST", "/api/web_sources", JSONObject().put("url", url), slow = true) }
    suspend fun setWebSourceEnabled(id: String, enabled: Boolean) { call("PATCH", "/api/web_sources/${seg(id)}", JSONObject().put("enabled", enabled)) }
    suspend fun removeWebSource(id: String) { call("DELETE", "/api/web_sources/${seg(id)}") }

    suspend fun profile(): String = JSONObject(call("GET", "/api/profile")).str("profile")
    suspend fun saveProfile(text: String): String = JSONObject(call("PUT", "/api/profile", JSONObject().put("profile", text))).str("profile")

    suspend fun scan(): ScanResult {
        val o = JSONObject(call("POST", "/api/scan", slow = true))
        return ScanResult(o.optBoolean("running"), if (o.isNull("evaluated")) null else o.optInt("evaluated"))
    }
}
