# Connect your Eufy recorder: a plain-English guide

**Experimental preview:** this guide describes the guided setup screen. Full
Supervisor, account and live-camera acceptance is still pending. Do not remove
a working installation to try this preview; use a separate test installation.

## What are we connecting?

Your **NVR** is the recorder box that your cameras plug into. This project lets
Home Assistant show live pictures from that box. It does not replace the box,
erase its recordings or change when it records.

There are two parts:

- The **app** talks to the recorder and makes its video available.
- The **integration** gives Home Assistant camera items to use on dashboards.

Think of the app as the video delivery service, and the integration as the list
of cameras Home Assistant can choose from.

## Before you sign in

Have these ready:

1. The email and password for the Eufy account that **owns** the recorder.
   A shared guest account may let you watch in the phone app but not give this
   project permission to control the recorder connection.
2. Access to that account's email. Eufy may send a short code to check it is you.
3. Your account country—for example, Australia.
4. A working internet connection. The video is intended to travel over your home
   network, but signing in and arranging the connection still use Eufy's service.

Do not post your password, email code or private sign-in token in screenshots,
support messages or GitHub issues.

## First-time setup: protect local video

This applies to the **new guided development version**, with its matching
integration installed. Leave a working older installation alone.

1. Start the new app. It can wait safely without a local video password.
2. Open **Settings → Devices & services → Add integration → Eufy NVR**.
3. Choose **Connect installed Eufy NVR app**, then select the new app.
4. If it has no local video password, read the confirmation and tick
   **Create the local video password and restart this new app**. Submit it.
   This creates a long random password to protect video inside your home.
   Only this new app restarts, so it can load the password. Home Assistant itself
   does not restart. Do not edit the app Configuration at the same time.
5. Keep the integration setup open. In another Home Assistant tab, open the
   app's **Open Web UI** and follow the Eufy sign-in steps below.
6. When the camera service is running, return to the integration setup and
   select **Submit**. Home Assistant reads the local password itself and checks
   the connection before adding cameras. You do not copy or remember it.

There are two different passwords: your Eufy password proves account ownership;
the local video password protects the video service inside your home. This step
does not change your Eufy password and does not send the local password to Eufy.
It saves the local password in the app's private configuration and the completed
integration's private connection settings. Protect your Home Assistant backups.

If a local password already exists, setup does not replace it—even if it is
short or incorrect. If a reply times out, a password may already have been
saved. Check the app Configuration and restart only that app if needed. Do not
clear the password or repeatedly try creating a new one.

The integration flow has been exercised in isolated Home Assistant Core, and
the app has passed isolated Linux checks. The full Supervisor setup screen and
real Eufy sign-in still need end-to-end acceptance. These are not instructions
to upgrade a working household installation.

## Step 1: sign in to Eufy

Open the setup from inside Home Assistant using an approved administrator
account. An administrator is someone allowed to change the system's settings.
The screen is not meant to be opened directly using the app's network address.

| Setting | What to enter | Why you are doing it |
| --- | --- | --- |
| Eufy email | Your Eufy account email | Tells Eufy which account owns the recorder. |
| Eufy password | That account's password | Proves you are allowed to use the account. It does not change the password. |
| Remember my Eufy sign-in | Leave off, or tick after reading the warning | Lets the app keep your email/password to renew access automatically. Off means you will sign in yourself after expiry. |
| Account country | Where the account is registered | Sends the correct country with the sign-in request. Use Australia for an Australian account. |
| Server group | Your known working US, EU or IE setting | Chooses which Eufy login service to ask. It is separate from your country. |

The Australian installation tested for this project uses **country Australia**
and **server group US**. That does not mean the house is in America. It means
that account signs in through that server group. Do not repeatedly try every
group if you are unsure: stop and ask for help.

If your country is missing from the list, do not choose a false country.
The first version has a limited country list; support needs to add the right one.

Select **Sign in** once and wait. A check can take up to 45 seconds.

The setup keeps your Eufy password while it finishes these checks, then clears
it from its working session. After you choose a recorder, it saves a private
**token**: a temporary key that lets the app connect without asking for your
password every time. Treat that saved key as private, too.

This new setup does not change or remove passwords already saved in the older
app settings. It also does not change the separate local video password.

## Step 2: finish any extra check

Eufy may skip this step, ask for a picture check, ask for an email code, or ask
for both.

### Picture check

Read the letters or numbers in the picture and type them into the box.
This is often called a **CAPTCHA**. It helps Eufy check that a person is signing
in, rather than a program repeatedly guessing passwords.

If a fresh picture appears after a wrong answer, use the new picture.

### Email code

Open your email and look for Eufy's six-digit code. Check the junk folder, too.
Enter the code in setup. It proves you can access the account's email.

Do not enter your normal password in the code box. If the code never arrives,
cancel and ask for help rather than repeatedly restarting the app.

## Step 3: choose and save the recorder

Choose the recorder you want Home Assistant to use, then select
**Save this recorder**.

Only supported T8N00 recorders appear. If none appear, check the account and model.
If two recorders have the same name, give them different names in the Eufy phone
app before trying again. Do not guess which one is which.

Saving replaces this app's saved account connection. It does not reset the
recorder or erase recordings.

**“Account connection saved” is not the same as “live video works.”**
In the new guided version, saving starts the camera service automatically.
The screen then tells you whether that service started. Open a camera tile to
check the actual picture: a running service alone is not proof of a live feed.
This startup connection has local automated tests, but is not installed yet.

## What happens when the app starts?

The guided version checks its saved sign-in before starting the camera service:

- **No saved connection:** it waits for you to sign in on the setup page.
- **Saved connection accepted:** it starts the camera service without another
  password entry.
- **Internet or Eufy service unavailable:** it keeps your saved details and checks
  again later. If the camera service is already running, it leaves it alone.
- **Saved sign-in rejected:** it stops this app's camera service and asks you to
  sign in again. It does not restart Home Assistant or change recorder settings.

If the camera service fails to start, use **Try the connection again** after
checking the app log. It will not keep restarting in a loop.

### Optional remembered sign-in

**Remember my Eufy sign-in is off by default.** If you leave it off, the app
clears your password after signing in and you sign in again when access expires.
If you tick it and save your recorder, the app keeps the email/password in its
private account file. When Eufy provides a usable expiry time, the app aims to
renew during the last five minutes before it. It can also renew after Eufy
rejects the saved access. It checks that the account and recorder ownership
still match, then validates the new token before restarting its video service.
That restart may briefly reconnect the camera tiles; it does not restart HA.

The app does not encrypt the saved password itself. Someone who can read the
app's private files or backups could read it. Protect your backups and keep
Home Assistant administrator access limited to people you trust.

If renewal needs a picture check or email code, is rejected, or has an uncertain
reply, automatic attempts pause. You handle the next sign-in yourself. Restarting
does not bypass that pause, so the app cannot keep trying a failed password.
There is also a one-hour minimum between automatic sign-in attempts. If Eufy
rejects a replacement sooner, the app pauses for you rather than starting a loop.

**Forget saved sign-in** removes the remembered email/password from the current
app file. It keeps the current access token, recorder settings and local video
password. It does not change your Eufy account password or erase older backups.
A request already sent to Eufy may finish, but it cannot save the forgotten
credentials again.

If Eufy supplies no usable expiry time, the app checks access every 15 minutes.
It does not guess an expiry date or treat a network outage as a bad password.
With a known expiry, checks are scheduled around that date, with a maximum
six-hour interval. These checks are not an instant expiry notification, and
unexpected revocation can still interrupt video. Timing, real challenges and recovery
still need hardware acceptance testing; uninterrupted access is not promised.
The installed app's current login behaviour is unchanged.

The setup reads Home Assistant's administrator list and its saved website
addresses automatically. You should not need to find user IDs or paste a Home
Assistant access token. If Home Assistant has no configured web address, setup
stops and explains that an administrator needs to check the Network settings.
It will not guess an address or silently change those settings.

**Permission to review before installation:** this automatic check needs
Home Assistant API access. The new code only reads the user list and settings,
but the permission itself allows much more. It has not been enabled on the
working installation.

## What the buttons mean

- **Check progress:** asks which step the current attempt reached. Use this if a
  reply did not arrive, instead of starting another sign-in straight away.
- **Cancel setup:** clears the unfinished setup. It does not delete a connection
  you already saved.
- **Open setup again:** starts a fresh setup session after cancellation or expiry.

Setup closes after ten minutes. This avoids leaving account details waiting in
memory if you walk away. Opening it again does not bypass a sign-in cooldown.

## If something goes wrong

### Sharing a support report

The development integration's **Download diagnostics** report keeps stream
counts and general connection status. It replaces the host address and camera
names, and leaves out passwords and raw error messages. It does not change the
names on your dashboard. Review a report before posting it anyway, and never
attach the app's private account file, a Home Assistant backup or a screenshot
containing sign-in details. This does not make older versions' logs safe to share.

### Why “healthy” can still mean “waiting for sign-in”

Healthy means the app is responding and its own control tasks are working.
It does not mean a camera is already showing video. Waiting for your email code
or for Eufy to respond should not make Home Assistant restart the app: that
would interrupt setup without fixing the problem. If video cannot start, the
setup page explains the next step separately.

This behaviour is implemented in the development test package, not installed
on your working system. Developer testing instructions are in
[Guided candidate testing](GUIDED_CANDIDATE.md).

| Message | What it means | What to do |
| --- | --- | --- |
| Sign-in not accepted | Eufy rejected this step. | Check the details for that step. Do not keep guessing. |
| Sign-in paused | Too many attempts, or Eufy asked us to stop. | Wait for the displayed delay. Eufy may still require more time afterward. |
| Could not confirm permission | The account may not own the recorder, or permission details are missing. | Check the owner account. Ask for support if it is already the owner. |
| Could not save | The app could not write its private connection file. | Check Home Assistant's free storage, then try saving again. |
| Sign-in safety check unavailable | The app cannot safely track login attempts. | Stop and ask for support. Do not delete protection files or restart repeatedly. |
| Someone else is setting up | Another approved user has an unfinished setup. | Wait for them to finish or cancel. This prevents conflicting choices. |

## Camera settings: what they achieve

### Add all camera tiles to a dashboard

In the new development version, the matching integration loads the camera card
for you. You do not copy a JavaScript file or register a dashboard resource.

1. Finish connecting the app and integration. Reload your browser page once if
   it was already open during installation, so it receives the new card code.
2. Open the dashboard you want, choose **Edit dashboard**, then **Add card**.
3. Search for **Eufy NVR cameras** and select it.
4. Choose your **NVR camera group**. If there is only one recorder, it is usually
   already selected. Select **Save**, then finish editing the dashboard.

The card receives the grid size, names and positions automatically. Fixed and
tracking views are placed next to each other. Clicking a tile enlarges it while
reusing the same player. Add one card containing all views, rather than a
separate live card for each camera; that avoids unnecessary decoding work.

If it says it is waiting for the layout, check that the matching new app and
integration are running and that shared grid is enabled. An older app may
provide video but no automatic layout. The new card waits rather than guessing
which part of the picture belongs to each camera. Existing manually configured
cards keep their settings; they are not silently replaced.

The implementation has local tests, including a real go2rtc metadata test.
The full sequence still needs testing in real Home Assistant before release.

These are explanations of the existing app settings, not extra steps you need
to change during sign-in.

| Setting | Recommended starting point | What it achieves |
| --- | --- | --- |
| Shared grid | On | Requests one combined live picture, then cuts it into tiles on the viewing device. This avoids starting a separate video connection for every tile. |
| Lens choice | Both | Includes both the fixed wide-angle picture and the moving/tracking picture where the camera reports two lenses. |
| Grid size | Automatic (0) | Chooses a 2×2, 3×3 or 4×4 picture layout that fits the detected lenses. |
| Video copy | On | Passes through the existing video instead of making Home Assistant re-encode it. This reduces server work, but your browser must support the HEVC video format. |

Eight dual-lens cameras can produce sixteen tiles. The software has tests for
that layout, but eight physical cameras have not been tested here. The verified
household has four dual-lens cameras: eight views.

The tiles share one picture. They are **not** sixteen separate full-resolution
streams. With sixteen tiles in a 1920×1080 combined picture, each tile has at
most 480×270 pixels. Making a tile larger makes it easier to see, but does not
create extra detail.

Further tablet, performance and long-running playback checks were deferred.
Do not treat this guide as proof that those checks have passed.

## Optional support

This is a free independent project by Efficient X (EFX), maintained by Brendon.
If it helps you, you can [buy EFX a coffee](https://buymeacoffee.com/efficientx).
A tip is optional and does not buy extra features or guaranteed support.
