# Extracting your own app credential

Besides your personal Yuno Energy login, this add-on needs a second value to talk to Yuno's API: a fixed, shared credential that's embedded in the official Android app itself — the same for every install, not tied to your account. It's how the app identifies itself as "a real Yuno Energy app" to their backend, separately from *who's* logged in.

This repository doesn't ship that value. Instead, here's how to pull your own copy directly out of the public app — it only takes a couple of minutes, and you don't need to know anything about Android or reverse engineering to do it.

## What you're looking for

Somewhere inside the app's code is a plain text string shaped like `identifier:password` — a short hyphenated name, a colon, then a longer password-looking string. That's it. `extract_credential.py` below finds it for you automatically.

## Step 1 — get the APK

Download the Yuno Energy Android app as an installable file (not through the Play Store app on your phone — you need the actual `.apk`/`.xapk` file on your computer). [APKPure](https://apkpure.com/yuno-energy/ie.yuno) is a common source for this; any mirror that gives you the real, unmodified app works.

## Step 2 — run the extractor

```
python extract_credential.py YunoEnergy.xapk
```

(Works with a plain `.apk` too, if that's what you downloaded.) No extra dependencies — just the Python standard library.

You should see exactly one candidate printed, something like:

```
Found 1 candidate(s):

  Some-Identifier-Here:aRandomLookingPassword123

Use this exact value (including the colon) as your `yuno_app_credential` add-on option.
```

Copy that whole string (identifier, colon, and password together) into the add-on's `yuno_app_credential` configuration option.

## Prefer not to run a script?

You can find the same string with a plain text search instead:

1. Rename the `.xapk`/`.apk` to `.zip` and extract it (Windows/macOS both handle `.zip` natively; for a `.xapk` you'll find a second `.apk` inside — extract that one too).
2. Open the resulting `classes.dex` file in a free hex/text editor that can search for ASCII text inside binary files — [HxD](https://mh-nexus.de/en/hxd/) is a good free option on Windows.
3. Search (Ctrl+F, text mode) for a colon (`:`) and scan nearby matches for one shaped like a short hyphenated name followed by a password-looking string, as described above.

## If this ever stops working

Yuno could change this credential in a future app update, in which case `extract_credential.py` might come back empty (or find the wrong thing) against a newer APK. If that happens, the fallback is decompiling the app properly with a tool like [jadx](https://github.com/skylot/jadx) and looking for how the app builds its `Authorization: Basic ...` header — that's how this project found it originally. Pull requests updating this guide (or the script) are welcome.
