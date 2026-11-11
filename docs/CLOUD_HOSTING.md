# Running ScoutAI in the cloud (so your laptop can sleep)

## Short answer

ScoutAI is a **long-running process**: it keeps a Telegram connection open all day, keeps a database,
and keeps a Telegram login file. That rules out most "free website hosting":

| Service | Free? | Works for ScoutAI? |
|---|---|---|
| **Render** (free web service) | Yes | **No.** It sleeps after 15 minutes without web traffic, and its disk is wiped on every restart, which would delete your database and your Telegram login. |
| **Railway, Fly.io, Koyeb** | Trial credit only, or a card hold | Not free in practice for an always-on app. |
| **PythonAnywhere free** | Yes | No: no always-on tasks, and outgoing connections are restricted. |
| **Oracle Cloud "Always Free" VM** | Yes, with a card for identity check | **Yes.** A small always-on Linux server. Signup can be fiddly, and Oracle cut and changed its free limits in 2026, so check their page. |
| **Google Cloud "e2-micro"** | Yes, with a card; only in 3 US regions | **Yes.** One small always-on VM (1 GB RAM, 30 GB disk). Read the billing page carefully: wrong region or extra disks cost money. |

So the honest options are: **keep running it on your laptop** (zero setup, works today), or use a
**free always-on VM** from Oracle or Google. Both VM options need a credit/debit card for sign-up. I
can't create the account or enter card details for you, which is why this is a guide and not done
for you. ScoutAI needs very little: about 250 MB of memory.

Laptop vs. cloud:

- Laptop: free, nothing to set up, but it only works while the laptop is awake and online.
- Cloud VM: runs 24/7, but you manage a Linux server, a card is on file, free limits can change, and
  your Telegram login lives on a machine you don't physically control.

If the laptop is on most of the day, just keep using the laptop. Move to the cloud if missed alerts at
night or while travelling are a real problem.

## Setting it up on a free VM (Ubuntu)

1. **Create the VM.** Oracle: Compute > Instances > Create, shape `VM.Standard.E2.1.Micro` (Always Free),
   image Ubuntu 22.04, and download the SSH key. Google: Compute Engine > Create instance, series E2,
   type `e2-micro`, region `us-west1`, `us-central1` or `us-east1`, 30 GB standard disk.
   Leave the firewall alone; you only need SSH (port 22).
2. **Stop ScoutAI on your laptop** (`stop_scout.bat`). A Telegram login file must be used from one
   place at a time, or Telegram logs both out.
3. **Copy the project and your private files to the VM.** On your computer, in the ScoutAI folder:

   ```bash
   scp -r -i <your-ssh-key> . ubuntu@<server-ip>:~/ScoutAI
   ```

   This includes `.env`, `user_session.session`, `bot_session.session`, `scout.db` and (if you use the
   calendar) `google_credentials.json`. Do not use a public file-sharing service for these files.
4. **Install and start it** (connect with `ssh -i <your-ssh-key> ubuntu@<server-ip>`):

   ```bash
   cd ~/ScoutAI
   bash deploy/install_ubuntu.sh
   ```

   It installs Python, adds a small swap file, creates the environment, and registers ScoutAI as a
   service that starts at boot and restarts after crashes. Watch it with `journalctl -u scoutai -f`.
5. **Telegram may ask you to log in again** because the server has a different IP address. If the log
   shows a login prompt, stop the service (`sudo systemctl stop scoutai`), run `venv/bin/python setup.py`
   once to sign in with the code Telegram sends you, then `sudo systemctl start scoutai`.

Updating later: `git pull` (or copy the new files), then `sudo systemctl restart scoutai`.

## Opening the dashboard (and the phone app) from the VM

The dashboard listens only on the VM itself by default. Two safe ways in:

- **SSH tunnel** (nothing to install on the phone): `ssh -L 8000:localhost:8000 ubuntu@<server-ip>`,
  then open http://localhost:8000 on your computer.
- **Tailscale** (best for the phone): install the free Tailscale app on the VM and on your phone, sign in
  with the same account, set `WEB_PASSWORD=...` in the VM's `.env`, restart ScoutAI, and open
  `http://<the VM's Tailscale address>:8000` on the phone.

Don't open port 8000 to the whole internet: the connection isn't encrypted.
