# ScoutAI

A personal assistant that reads public Telegram channels for you and tells you about the opportunities that fit **you**: scholarships, internships, hackathons, competitions and so on.

An AI reads every new post, scores it from 1 to 10 against your profile, and pulls out the title and deadline. The good ones land in a private Telegram chat with your own bot, and you can track them on a small web dashboard that runs on your computer.

- **Score 8 or more:** you get an alert right away.
- **Score 5 to 7:** it goes into a daily digest, so your chat stays quiet.
- **Deadline today or tomorrow:** sent immediately, whatever the score.
- Track each opportunity as **Planned, Working, Finished or Dismissed**.
- Everything runs on **your own computer**, with your own keys. Nobody else can see your data.

> This repository is the program, not a hosted service. Each person runs their own copy with their own bot. That takes about 10 minutes the first time, and this guide walks you through every step.

---

## What you need

| Thing | Notes |
|---|---|
| A **Windows** PC | The one-click launcher is for Windows. Mac and Linux steps are at the bottom. |
| **Python 3.10 or newer** | Free: https://www.python.org/downloads/ |
| A **Telegram** account | The app on your phone is enough. |
| Internet | The bot only works while your computer is on and online. |

You will also create three free things during setup: Telegram API keys, a Telegram bot, and an AI key. The setup program tells you exactly what to click.

---

## Step 1: Install Python (skip if you already have it)

1. Go to https://www.python.org/downloads/ and download the newest Python.
2. Run the installer. **On the very first screen, tick "Add python.exe to PATH"**, then click *Install Now*.

To check it worked, open the Start menu, type `cmd`, press Enter, and run `python --version`. You should see a version number such as `Python 3.12.4`.

## Step 2: Download ScoutAI

**Easiest way (no tools needed):**

1. On this page, click the green **Code** button, then **Download ZIP**.
2. Right-click the ZIP in your Downloads folder and choose **Extract All...**
3. Move the extracted `ScoutAI-main` folder somewhere permanent, for example `Documents`.

**If you use Git:**

```bash
git clone https://github.com/muxtorjon-not-here/ScoutAI.git
```

## Step 3: Double-click `run.bat`

Open the `ScoutAI-main` folder and double-click **`run.bat`**.

If Windows shows a blue "Windows protected your PC" box, click **More info**, then **Run anyway**. The file is a short script you can read in any text editor.

A black window opens. The first time, it installs what it needs (a minute or two) and then starts the **setup wizard**. The wizard asks five things, one at a time, and checks each answer before moving on.

### The setup wizard, step by step

**1. Telegram API keys.** These let ScoutAI read channels as you.
- Go to https://my.telegram.org and log in with your phone number.
- Click **API development tools** and fill in the form. The app name can be anything, such as `ScoutAI`. Leave the rest as it is.
- Copy the **api_id** (a number) and **api_hash** (a long code) into the wizard.

**2. Your bot.** This is the chat that sends you alerts.
- In Telegram, search for **@BotFather** (the one with the blue check mark) and send `/newbot`.
- Choose a name, then a username that ends in `bot`, such as `jane_scout_bot`.
- BotFather replies with a **token** that looks like `123456789:AAH...`. Paste it into the wizard.

**3. Your Telegram user ID.** This makes sure only *you* can use your bot.
- The wizard shows a link to your new bot. Open it and press **Start**.
- The wizard sees your message and fills in your ID automatically. If it can't, message **@userinfobot** and paste the number it gives you.

**4. AI key.** The AI is what reads and scores posts. Free options:
- **Groq (recommended, fast):** https://console.groq.com/keys, then *Create API Key*. It starts with `gsk_`.
- **Gemini:** https://aistudio.google.com/apikey, then *Create API key*.

**5. Channels to watch.** Type the `@usernames` of public channels, separated by commas, or press Enter to skip and add them later. Public channels have a link like `t.me/channelname`.

After that, the wizard asks a few quick questions about you (where you live, your level, what you want, what to ignore). The AI uses your answers to decide what matters. Then it asks for your **phone number** and the **login code** Telegram sends to your Telegram app. This logs ScoutAI in so it can read channels. If you have two-step verification on, it asks for that password too.

When the wizard says **All set**, ScoutAI starts. Your browser opens the dashboard at http://localhost:8000 and your bot sends you "🟢 ScoutAI is online".

You only do this once. Next time, `run.bat` skips straight to starting the bot.

---

## Using ScoutAI

### In Telegram (your bot)

Open your bot and send `/help`. The commands:

| Command | What it does |
|---|---|
| `/add channel1 @channel2 https://t.me/channel3` | Start watching channels or groups |
| `/add https://t.me/c/123456/27` | Watch just one topic of a group (see below) |
| `/remove channel1` | Stop watching (paste the same link to remove a topic) |
| `/channels` | List the channels, groups and topics you watch |
| `/board` | Show your active opportunities |
| `/planned`, `/working`, `/done` | Opportunities by status |
| `/add_opp Title \| Deadline` | Track something you found yourself |
| `/digest` | Send the saved score 5 to 7 opportunities now |
| `/catchup` | Rescan the last 10 posts of every channel |
| `/web`, `/web_add <feed>`, `/web_scan` | Opportunity websites (see below) |
| `/calendar` | Sync Planned/Working opportunities to Google Calendar |
| `/profile` | Show what you told the AI about yourself |
| `/set_profile new text` | Rewrite your profile |

Every opportunity arrives as a card with buttons to set its status, edit its deadline, or open the original post.

### Groups and topics

ScoutAI can watch group chats too, not only channels.

- **Public group:** `/add @groupname` works like a channel.
- **One topic of a group:** some groups are split into topics (like "News" or "Jobs"). Open the topic in Telegram, long-press or right-click a message in it, choose **Copy Link**, and send `/add <that link>`. Only that topic is watched, which keeps the noise down. A link such as `https://t.me/c/2495717582/27` is a topic link; the number at the end is the topic.
- **Private group:** your Telegram account must already be a member, because ScoutAI reads as you and can't join private groups. Add them with the same `/add` link. The bot never posts in groups.
- If you paste a link to a single message in a group that has no topics, the whole group is watched.

Groups are chatty, so the AI will spend more time filtering them. Topics are the better choice when the group has one.

### Opportunity websites

Besides Telegram, ScoutAI reads a list of well-known opportunity websites every 2 hours (Opportunity Desk, Opportunities For Africans, Scholars4Dev, Scholarships Corner, Devpost hackathons, CTFtime, Codeforces and more). Each new listing goes through the same AI scoring as a Telegram post, after a quick title check that drops obvious mismatches, so only matches for your profile reach you.

- `/web` lists the sites and how many opportunities each has produced. `/web_off <id>` and `/web_on <id>` pause or resume one.
- `/web_add https://site.com/feed` adds any site that has an RSS or Atom feed. `/web_scan` scans right now.
- The dashboard's Channels tab has the same list with switches.

### The same opportunity from many sources

When several channels or websites announce the same opportunity, you get it once. ScoutAI compares the registration links in the posts and then the titles and deadlines of what it already saved. Copies of things you already have are skipped, including ones you dismissed.

### Google Calendar reminders

When you press **Plan** or **Working** on an opportunity that has a deadline, ScoutAI puts it on your Google Calendar:
a bar from 5 days before the deadline until the deadline, plus a 9:00 event on the first day with a popup and email reminder, so your phone buzzes. Set `CALENDAR_REMIND_DAYS=5,2,1` in `.env` to get more reminders. Moving it to Finished or Dismissed, changing its deadline, or deleting it updates the calendar. Opportunities without a deadline are skipped; set a deadline and it is added. `/calendar` syncs everything that is already Planned or Working.

One-time setup (about 10 minutes, free, no billing needed):

1. Go to https://console.cloud.google.com and create a project (any name).
2. Open **APIs & Services > Library**, search for **Google Calendar API** and click **Enable**.
3. Open **IAM & Admin > Service Accounts > Create service account** (any name, skip the optional steps). Open it, go to **Keys > Add key > Create new key > JSON**. A file downloads.
4. Rename it `google_credentials.json` and put it in the ScoutAI folder. It is private, like `.env`, and is never uploaded to GitHub.
5. Copy the service account's e-mail address (looks like `name@project.iam.gserviceaccount.com`).
6. Open https://calendar.google.com signed in as the calendar's owner, hover the calendar under **My calendars**, click the three dots > **Settings and sharing**. Under **Share with specific people** add the service account's e-mail with the permission **Make changes to events**.
7. In the same settings page, copy the **Calendar ID** (for your main calendar it is your Gmail address) into `.env`:

   ```
   GOOGLE_CALENDAR_ID=you@gmail.com
   ```

8. Restart ScoutAI and send `/calendar` to the bot. If something is wrong, the bot tells you what.

### Running it in the cloud

See [docs/CLOUD_HOSTING.md](docs/CLOUD_HOSTING.md): which free hosts work (a free Oracle or Google VM), which don't (Render and similar sleep and erase files), and a one-command installer.

### On the dashboard

While the bot is running, open **http://localhost:8000**. It has three views:

- **Board:** tabs for Inbox, Planned, Working, Finished and Dismissed. You can sort by deadline, score or newest, and keys `1` to `5` switch tabs.
- **Table:** every opportunity in one list.
- **Channels & AI Profile:** add or remove channels, groups and topics, edit your profile, and trigger a rescan.

By default the dashboard is only reachable from your own computer.

#### Opening the dashboard on your phone

1. Open the `.env` file in Notepad and set a password: `WEB_PASSWORD=choose-something-long`
2. Restart ScoutAI. The black window now shows a second line, `On your phone (same Wi-Fi): http://192.168.x.x:8000`.
3. Connect your phone to the **same Wi-Fi** as the computer and open that address in your phone's browser. Sign in with your password.
4. The first time, Windows may ask whether to allow Python through the firewall. Allow it on **private networks** only.

Tips: use "Add to Home Screen" in your browser to get an app-like icon. The computer must be on and running ScoutAI, and the address can change if your router gives the computer a new IP. To reach it away from home, install the free [Tailscale](https://tailscale.com) app on both devices and use the computer's Tailscale address instead. Don't expose the port to the internet directly: the connection is not encrypted.

### Running it quietly in the background

`run.bat` keeps a black window open. If you would rather not see it:

- Double-click **`start_hidden.vbs`**. It runs with no window and no taskbar icon.
- Double-click **`stop_scout.bat`** to stop it.
- Double-click **`view_log.bat`** to see what it has been doing.

Run `run.bat` once first, because the hidden launcher needs the setup to be finished. To start ScoutAI automatically when Windows starts, press `Win + R`, type `shell:startup`, and put a shortcut to `start_hidden.vbs` in the folder that opens.

---

## Tips for good results

- **Watch focused channels.** Ten good channels beat a hundred noisy ones.
- **Write a specific profile.** "Looking for fully funded summer programs in computer science, I'm in Tashkent, I speak English" gives much better filtering than "scholarships".
- **Check the score.** If good posts show up as 5 to 7, run `/digest` or wait for the daily digest. Jumping straight to 8+ for everything is not realistic.
- **Be patient with a lot of new channels.** Adding 10 at once can take a few minutes because the AI scores one post at a time to stay within free limits.

---

## Keeping your account safe

- **`.env`** holds your bot token and keys. **`user_session.session`** is a full login to your Telegram account. **Never share, upload or send these files to anyone**, including to a friend who is helping you.
- ScoutAI only reads **public channels**, and only your user ID can talk to your bot.
- If you ever leak a token or key, create a new one: `/revokebot` in @BotFather, and the key pages of your AI provider. Then run `setup.py` again.
- If you update ScoutAI by downloading a new ZIP, copy your old `.env`, `user_session.session`, `bot_session.session` and `scout.db` into the new folder so you keep your settings and history.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Python is not installed` | Install Python (Step 1) and tick **Add python.exe to PATH**. Then run `run.bat` again. |
| The window closes instantly | Open `cmd`, go to the folder, run `run.bat`, and read the error. |
| `Missing or placeholder values in .env` | Delete `.env`, then run `run.bat` again to redo the wizard. |
| Bot says nothing at startup | Open your bot in Telegram and press **Start**. Bots cannot message you first. |
| `Cannot find any entity corresponding to "xyz"` | That channel name is wrong or the channel is private. Use `/remove xyz` and `/add` the correct `@username`. |
| Login code never arrives | It comes as a message inside your Telegram app (not an SMS) from "Telegram". Check all your logged-in devices. |
| `ScoutAI is already running` | Run `stop_scout.bat`, then start again. |
| Dashboard won't open | Open http://localhost:8000 by hand. If the port is busy, close other copies of ScoutAI. |
| AI errors like 429 in `scout.log` | You hit the free rate limit. ScoutAI waits and retries by itself, so just leave it running. |
| Want to start over | Close ScoutAI, delete `.env`, `user_session.session`, `bot_session.session` and `scout.db`, then run `run.bat`. |

Still stuck? Open an **Issue** on this repository and paste the last 20 lines of `scout.log`. **Remove any tokens or keys first.**

---

## Mac and Linux

The launcher is Windows only, but the program itself is plain Python. In a terminal inside the ScoutAI folder:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py     # first time only: the same wizard as above
python main.py
```

Then open http://localhost:8000.

---

## For developers

| File | Purpose |
|---|---|
| `main.py` | Telegram clients, scoring pipeline, bot commands, digest scheduler |
| `ai_evaluator.py` | Groq and Gemini calls, retries, and model fallbacks |
| `database.py` | SQLite storage (`scout.db`) |
| `web_sources.py` | Opportunity websites: feed list, fetching, batch triage |
| `dedupe.py` | Finds the same opportunity posted in different words |
| `calendar_sync.py` | Google Calendar events for Planned/Working items |
| `deploy/`, `docs/` | Cloud installer and hosting guide |
| `web_api.py`, `templates/index.html` | Dashboard API and single-page UI |
| `config.py` | Reads `.env` |
| `setup.py` | The first-run wizard |

Everything personal (`.env`, session files, `scout.db`, `scout.log`, `default_profile.txt`) is git-ignored.
