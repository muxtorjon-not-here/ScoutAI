package com.scoutai.app

import android.app.Application

class ScoutApp : Application() {
    override fun onCreate() {
        super.onCreate()
        PollWorker.createChannel(this)
        val store = Store(this)
        if (store.loggedIn && store.notifications) PollWorker.schedule(this)
    }
}
