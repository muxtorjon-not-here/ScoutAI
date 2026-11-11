package com.scoutai.app

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.*
import java.time.LocalDate
import java.time.temporal.ChronoUnit
import java.util.concurrent.TimeUnit

/** Every ~30 min: fetch the board and notify about new inbox items with score >= 8 or a deadline within 2 days. */
class PollWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val store = Store(applicationContext)
        if (!store.loggedIn || !store.notifications) return Result.success()
        val list = try { Api(store).opportunities().first } catch (e: AuthException) { return Result.success() } catch (e: Exception) { return Result.retry() }

        val today = LocalDate.now()
        val notified = store.notifiedIds.toMutableSet()
        val interesting = list.filter { o ->
            o.status == "UNTRACKED" && (o.score >= 8 ||
                (o.deadlineDate != null && ChronoUnit.DAYS.between(today, o.deadlineDate) in 0..2))
        }
        val fresh = interesting.filter { it.id.toString() !in notified }
        if (!store.pollBaselineDone) { // first run: don't flood with everything already on the board
            store.pollBaselineDone = true
        } else if (fresh.isNotEmpty()) {
            notify(applicationContext, fresh)
        }
        notified.addAll(interesting.map { it.id.toString() })
        store.notifiedIds = notified.toList().takeLast(500).toSet()
        return Result.success()
    }

    companion object {
        private const val NAME = "scoutai_poll"
        const val CHANNEL = "opportunities"

        fun schedule(ctx: Context) {
            val req = PeriodicWorkRequestBuilder<PollWorker>(30, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun cancel(ctx: Context) { WorkManager.getInstance(ctx).cancelUniqueWork(NAME) }

        fun createChannel(ctx: Context) {
            if (Build.VERSION.SDK_INT >= 26) {
                ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(
                    NotificationChannel(CHANNEL, "New opportunities", NotificationManager.IMPORTANCE_DEFAULT)
                        .apply { description = "High-score or soon-due opportunities" })
            }
        }

        private fun notify(ctx: Context, items: List<Opp>) {
            if (Build.VERSION.SDK_INT >= 33 &&
                ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
            createChannel(ctx)
            val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val style = NotificationCompat.InboxStyle()
            items.take(6).forEach { style.addLine("★${it.score} ${it.title}") }
            val n = NotificationCompat.Builder(ctx, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(if (items.size == 1) items[0].title else "${items.size} new opportunities")
                .setContentText(if (items.size == 1) items[0].summary.take(100) else items.first().title)
                .setStyle(if (items.size == 1) null else style)
                .setContentIntent(open).setAutoCancel(true).build()
            runCatching { NotificationManagerCompat.from(ctx).notify(1001, n) }
        }
    }
}
