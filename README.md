# Laden Ops

Personal dashboard in the laden.no palette: void black, hack green, cyan, Orbitron titles. It is a small GTK window, not the Electron project in `dashboard-build`.

The first open asks you to log in or create an account with email and password. There is no account-server field. The password is the vault password. **Save** in Control writes `vault.conf` into the Laden Ops folder on that device, mode private to your user, and uploads a sealed copy to Laden's cloud at `https://laden.no/ldash` (Docker api-main). If that path is unreachable, Ldash automatically tries the read-only standby at `https://laden.no/ldash-standby` (systemd ldash-account). Standby accepts login and vault fetch only; register and vault writes return 503 so nothing silently diverges. The service keeps the account row in SQLite under `/var/lib/ldash` and a sealed file under `/var/lib/ldash/vaults/<id>/vault.conf`. The direct link is `/ldash/vault/<id>/vault.conf`. Opening the link does not show the kit. Login and create succeed from the local vault if the cloud is down. After the first successful create or login, Ldash asks once whether this device should sign in automatically next time. The account service is `account/server.py`. It does not log the password or the kit.

Ldash 1.0 is published by **Laden AS** ([laden.no](https://laden.no)). The Windows setup lists that publisher. The Android package is signed with a Laden AS certificate, and the fingerprint is in `android/dist/PUBLISHER.txt` beside the APK. Mac and iPhone show Laden AS in the app info. Each install file has a `SHA256SUMS` file in the same folder. Check it before you open the file:

```bash
sha256sum -c SHA256SUMS
```

Windows SmartScreen and macOS Gatekeeper still ask the first time. Those prompts clear only after a commercial Authenticode certificate and an Apple Developer ID. The name on the installer is Laden AS either way.

## Linux

Ldash 1.0 for Linux is this repository. Clone it, or download the source archive from the Releases page, then:

```bash
sh linux/install.sh
ldash
```

The downloadable archive is `linux/dist/Ldash-1.0-Linux.tar.gz`. Unpack it and run the installer from that folder. The installer checks for Python 3, GTK 4, WebKitGTK 6, and the Python D-Bus bindings. On Arch and CachyOS the packages are `gtk4`, `webkitgtk-6.0`, `python-gobject`, and `python-dbus`. It adds a **Laden Ops** menu entry, starts at login, and puts `ldash` on `~/.local/bin`. The window is a bare frame. Hold the top edge to move it. Close hides the window. **Ctrl+`** shows it again. Quit from Control exits. **Save** in Control writes the kit to `~/.config/laden-ops/kit.json`, readable only by your user. Notes stay in `~/.config/laden-ops`.

On this machine the same app is `python3 ~/laden-dash/main.py`. A second start shows the window that is already running.

## First open

A new install asks for an email, a password, a username, a city, and the programs for the Programs panel. The password is also the vault password. It is stored only as a hash on that device, and as a sealed `vault.conf` locally plus a sealed cloud copy on Laden when reachable. Ldash then creates the user folder: config, `account.json` (no password), and `vault-workspace`. Weather uses the public Open-Meteo forecast for the city you typed.

An existing kit on this Linux machine stays as it is. Opening Laden Ops here does not ask again.

## Windows

`windows/dist/Ldash-1.0-Windows-Setup.exe` is the Ldash 1.0 installer. It copies the app, a private Python runtime, and the Microsoft WebView2 setup. You do not install Python or GTK yourself.

On a Windows PC, run that setup. The setup window names the publisher as Laden AS. It offers a desktop shortcut and sign-in startup. The window is a bare frame: no title bar. Hold the top edge to move it, and resize from the edges. Close hides the window. **Ctrl+`** shows it again. Quit from Control exits. The first open creates the account and the folder in `%APPDATA%\laden-ops`. **Save** in Control writes `kit.json` into that folder. Uninstall keeps that folder. Compare `windows/dist/SHA256SUMS` before you run the setup.

This installer was built on Linux and has not been launched on a Windows PC from here.

Rebuild with `sh windows/build.sh`. That needs network access for the .NET SDK, the embeddable Python zip, the WebView2 bootstrapper, and Inno Setup. The Linux app is unchanged: `python3 ~/laden-dash/main.py`.

## macOS

`macos/dist/Ldash-1.0-macOS-Setup.pkg` is the Ldash 1.0 installer. It includes the app and a private Python 3.12 runtime for both Apple Silicon and Intel. You do not install Python, GTK, or .NET yourself. The window uses the Mac WebKit that is already on the system.

On a Mac, open that package. If macOS says the installer is from an unidentified developer, right-click it, choose Open, then Open. It installs **Laden Ops** into Applications, starts it, and adds a login item. Finder’s Get Info line credits Laden AS. The window is a bare frame: no title bar. Hold the top edge to move it. Close hides the window. **Ctrl+`** shows it again. Quit from Control exits. The first open creates the account and the folder in `~/Library/Application Support/laden-ops`. **Save** in Control writes `kit.json` into that folder. Uninstall with `/Applications/Laden Ops.app/Contents/Resources/uninstall.command`. That removes the app and the login item, and leaves the notes folder. Compare `macos/dist/SHA256SUMS` before you open the package.

This package was built on Linux and has not been launched on a Mac from here. It is ad-hoc signed, not notarized.

Rebuild with `sh macos/build.sh`.

## iPhone

`iphone/LadenOps.xcodeproj` is the iPhone app. This Linux machine has no Xcode, so there is no installable package here and it has not been run on a phone. On a Mac, open that project, select the Laden Ops target, set the signing team to your Apple ID, choose your iPhone, and press Run. If the phone says the developer is not trusted, go to Settings → General → VPN & Device Management and trust it. A free Apple ID install lasts about a week; press Run again when it expires.

The app is the same dashboard in the system WebKit view. The first open is the same account step: email, a password that is also the vault password, a username, a city, and web links for the Programs panel. That writes `account.json` under Application Support and the vault under Documents. **Save** in Control writes `kit.json` beside `account.json`. The app info credits Laden AS. Web links open in Safari. API monitors, the vault, the journal, the economy assistant, and Caleb use the providers already in the app (xAI, Groq, or OpenRouter). Case and budget PDFs open on the phone. Notes are in the Files app under On My iPhone → Laden Ops → vault-workspace. Deleting the app deletes those notes. This project has not been compiled here, and there is no IPA.

There is no global hotkey, no terminal, and no list of other apps or their windows. A launcher that is a shell command does not run. After the account step, Unlock checks that password. With no custom master, Unlock asks for Face ID or the device passcode, and the text in the box is not checked. Quit is not shown; leave the app with the Home gesture. The vault title uses your callsign.

## Android

`android/dist/Ldash-1.0-Android.apk` is the Ldash 1.0 release package, signed with the Laden AS certificate. It was built on this machine and has not been installed on a device from here. On the phone, allow install from this source, or from a computer run `adb install android/dist/Ldash-1.0-Android.apk`. Package name `org.laden.opsdash`. Before installing, compare `android/dist/SHA256SUMS` and check that `android/dist/PUBLISHER.txt` names Laden AS. The certificate SHA-256 is `06cca145682c44725bc071e08d75906eaf382135a0afa4c3f02d7bdf0d9cadbc`. Rebuilding is `sh android/build.sh`. The signing key stays in `~/.config/laden-ops/signing` and is not part of the download.

The app is the same dashboard in the system WebView. The first open is the same account step, and it writes `account.json` plus `vault-workspace` in the app's private files. **Save** in Control writes `kit.json` beside `account.json`. Web links open in the browser. API monitors, the vault, the journal, the economy assistant, and Caleb use the providers already in the app (xAI, Groq, or OpenRouter). Case and budget PDFs open through the system viewer. Notes stay in the app's private storage. Sharing or exporting a vault.conf is how they leave the phone. Deleting the app deletes those notes.

There is no global hotkey, no terminal, and no list of other apps or their windows. A launcher that is a shell command does not run. After the account step, Unlock checks that password. With no custom master, Unlock asks for a fingerprint, face unlock, or the device passcode, and the text in the box is not checked. Back hides the app. Quit is not shown. The vault title uses your callsign.

## vault.conf

Export from Control writes one `laden.vault.conf`. The same file opens on Linux, Windows, Mac, iPhone, and Android. It carries programs, the calendar, the sticky note, subscriptions, Q-due, economy, settings, vault entries, journal cases, and the documents stored with a case. Version stays 1, so an older kit still imports. Replace kit restores the case. Merge vault entries only adds vault records and leaves the journal in place. On a phone, Export opens the system share sheet. Seal the file when it leaves the device. It can contain credentials and the API key.

The Ldash 1.0 Windows setup, Mac package, and Android package above are the launch builds. They carry cases in vault.conf, the account step, and the weather panel. Linux picks up the same page the next time Laden Ops is opened.

## What you can set

- First-time setup: autodetects the OS default browser and terminal (overridable), then offers a searchable checklist of installed apps plus manual add. Nothing is uploaded until vault.conf is confirmed. Import-.conf still works; an existing local vault.conf stays the vault.
- Window layout (Control): Right half (default), Left half, or Free window. Desktop only; follows the current monitor work area and re-applies on resolution change. Saved on this machine only.
- Programs: any command or URL. The first open picks the starting set
- Weather: city forecast from Open-Meteo, no key
- Monitor: host stats, plus an app, a KWin window, or an HTTP API, and which fields are drawn (spark, bar, gauge, or readout)
- Subscriptions: one section each, with cost, cycle, next date, and a note
- Calendar: appointments and notes
- Sticky note, and switches to hide the note or the calendar
- Size classes: add or remove classes, set each class's span, and assign a class to every panel
- Vault: legal docs, credentials, and engagement notes behind your login password or a master you set. Pins open a URL, a folder, or a terminal. Each journal scope is a folder under engagements, with dump, case, and a live summary
- B-side journal: dump notes, file them into buckets, attach a terminal directory or a browser URL, and ask Caleb for a brief. Caleb's thoughts is that scope's live summary. Export PDF prints the case
- Control: the switch in the top-right corner. Save writes the kit into the Laden Ops folder on this device. Show or hide panels, edit the layout, flip B-side, choose the AI provider and key, set the vault master, quick-add a credential, and export or import a vault.conf
