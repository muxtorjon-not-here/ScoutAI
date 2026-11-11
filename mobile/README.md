# ScoutAI for Android

A native phone version of the ScoutAI web dashboard (Kotlin, Jetpack Compose, Material 3). It talks to the
same FastAPI server as the browser dashboard, so everything you do on the phone shows up there and the other way round.

## 1. Prepare the server

The app needs the dashboard reachable from the phone, which means **password mode**:

1. In the ScoutAI `.env` set `WEB_PASSWORD=<something long>` and restart ScoutAI. The server then listens on the network.
2. Find the address the phone should use:
   - Same Wi-Fi as the PC: `http://<PC LAN IP>:8000` (run `ipconfig`, e.g. `http://192.168.1.20:8000`). Allow Python through Windows Firewall for private networks.
   - Away from home / ScoutAI on a cloud VM: install [Tailscale](https://tailscale.com) on both, then use the Tailscale IP (`http://100.x.y.z:8000`) or MagicDNS name.

## 2. Install the APK

The debug APK is built to `mobile/app/build/outputs/apk/debug/app-debug.apk`.

**Option A: copy the file.** Send the APK to the phone (USB cable, Telegram "Saved messages", Drive). Open it; when Android asks, allow
"Install unknown apps" for the app you opened it from (Settings > Apps > Special access > Install unknown apps), then tap Install.

**Option B: `adb`.** Enable *Developer options* and *USB debugging* on the phone, connect it, then:

```bash
adb devices
adb install -r mobile/app/build/outputs/apk/debug/app-debug.apk
```

(`adb` lives in `%LOCALAPPDATA%\Android\Sdk\platform-tools`.)

## 3. First run

Enter the **Server address** (e.g. `http://192.168.1.20:8000`; if you leave out the port, `:8000` is assumed) and the **dashboard password**
(`WEB_PASSWORD`). The address, password and login token are stored encrypted on the phone (EncryptedSharedPreferences). If the server
restarts and the token stops working, the app logs in again silently with the saved password.

Plain `http://` is allowed because LAN and Tailscale addresses have no HTTPS certificate; the connection is protected by the password/token, so only use it on networks you trust (Tailscale is encrypted end to end).

## 4. Using it

| Screen | What you can do |
| --- | --- |
| **Board** | Tabs Inbox / Planned / Working / Finished / Dismissed with counts. Cards are sorted by deadline, with a countdown badge (red = overdue/today, amber = within 3 days), score and category chips. Tap **Why it matches** to expand. Buttons: main action (Plan > Start > Finish), Dismiss, open the original post, edit deadline (bottom sheet with date picker), and a menu to move to any status or delete. **Swipe right** = main action, **swipe left** = dismiss; an **Undo** snackbar follows every status change. Search (magnifier), filter by category / minimum score and change sort (filter icon). Pull down to refresh; it also refreshes by itself every minute while open. **+** adds a manual opportunity. |
| **Sources** | Add Telegram channels, groups and topics by pasting `@names` or `t.me` links; remove them; turn opportunity websites on or off, add an RSS/Atom feed; **Scan now** (up to ~1 minute). |
| **Profile** | Edit the AI profile used to score opportunities. |
| **Settings** | Server info, test connection, notifications on/off, log out. |

Moving an item to Planned or Working also puts it on Google Calendar; that happens on the server, nothing to configure in the app.

**Notifications:** a background job checks roughly every 30 minutes and notifies about new Inbox items with score 8+ or a deadline within 2 days
(Android 13+ asks for permission on first launch). Android may delay it when battery saver is on; Android decides the exact timing.

Offline: the last board is cached and shown with an "Offline" banner; actions need a connection.

## Build it yourself

Requires Android Studio (its bundled JBR is the JDK, the system Java 1.8 is too old):

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
cd mobile
.\gradlew.bat assembleDebug
```

`local.properties` (git-ignored) must contain `sdk.dir=C:/Users/<you>/AppData/Local/Android/Sdk`; Android Studio creates it when you open the folder.

To try the app without Telegram credentials, run the stub API (same JSON shapes, password `test`):

```bash
python mobile/tools/stub_server.py
```

and log in with `http://<PC address>:8000` (or `http://10.0.2.2:8000` from the Android emulator).
