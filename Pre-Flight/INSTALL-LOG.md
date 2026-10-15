# Install Log — 2026-10-15

Actual state of this machine after executing the Pre-Flight checklist.
Everything below was installed **without administrator privileges**.

---

## Installed

| Component | Version | Location |
|---|---|---|
| JDK | Temurin **17.0.20.1+1** | `D:\dev\jdk-17` |
| Android SDK | — | `D:\dev\android-sdk` (7.9 GB) |
| ├ platform-tools (adb) | 37.0.1 | |
| ├ emulator | 37.2.12 | |
| ├ build-tools | 36.0.0 | |
| ├ platform | android-36 (Android 16 "Baklava") | |
| ├ NDK | 28.2.13676358 | |
| └ system image | google_apis / x86_64 / API 36 | |
| Flutter | **3.47.7** stable (Dart 3.13.5) | `D:\dev\flutter` (3.3 GB) |
| AVD | `Pixel8_API36` — Pixel 8, API 36, x86_64 | `~\.android\avd` |

### Environment variables (User scope)
```
JAVA_HOME         = D:\dev\jdk-17
ANDROID_HOME      = D:\dev\android-sdk
ANDROID_SDK_ROOT  = D:\dev\android-sdk
PATH  += %JAVA_HOME%\bin
        %ANDROID_HOME%\platform-tools
        %ANDROID_HOME%\emulator
        %ANDROID_HOME%\cmdline-tools\latest\bin
        D:\dev\flutter\bin
```

### VS Code extensions
`dart-code.dart-code`, `dart-code.flutter`, `eamodio.gitlens`, `editorconfig.editorconfig`, `usernamehw.errorlens`

### Git (global)
`core.autocrlf=true`, `init.defaultBranch=main`, `core.longpaths=true`, `user.email=padakhepict@padakhep.org`
⚠️ **`user.name` still unset.**

### Convenience
- `D:\dev\start-emulator.bat` + Desktop shortcut **"Android Emulator"**

---

## Verified end-to-end ✅

```
flutter doctor          -> all mobile checks pass, "All Android licenses accepted"
flutter build apk       -> app-debug.apk, 144 MB, 139 s
adb install             -> Success
adb launch + screencap  -> Flutter counter app rendering on emulator-5554
emulator -accel-check   -> WHPX installed and usable
emulator cold boot      -> ~6 s
```

---

## Gotchas hit (and fixes) — these cost the most time

1. **`sdkmanager "platforms;android-36"` silently installs nothing and still exits 0.**
   `sdkmanager.bat` runs via `cmd.exe`, which treats `;` as an argument separator, so the
   package name splits into garbage. Use `/`: `platforms/android-36`.
   **Always verify on disk, never trust the exit code.**

2. **Gradle build fails on a missing NDK.** The Flutter template pins `flutter.ndkVersion`
   and Gradle cannot auto-install it. Install explicitly: `ndk/28.2.13676358`.

3. **`sdkmanager --licenses` is a no-op** on current CLI versions ("no longer needed").
   Licenses are accepted at install time; a `licenses/` folder appears automatically.

4. **Oracle Java 23 outranks JDK 17 in the terminal.** Oracle registers
   `C:\Program Files\Common Files\Oracle\Java\javapath` in **Machine** PATH, which Windows
   resolves before User PATH. Harmless — Gradle and Flutter both read `JAVA_HOME`.
   `java -version` will report 23; that is cosmetic. See `ELEVATED-TASKS.ps1` task 3.

5. **Don't launch the emulator from a background task** — it dies with its parent process.
   Use `Start-Process` (or the desktop shortcut) so it detaches.

6. **A new terminal is required** after the env var changes. Old terminals and old VS Code
   windows will not have `flutter`, `adb`, or `emulator` on PATH.

---

## Not done

| Item | Why | Needed for |
|---|---|---|
| `git config user.name` | awaiting your name | commit authorship |
| Windows Defender exclusions | needs admin | **build speed — recommended** |
| Android Studio | needs admin | only for Kotlin native (file 03) |
| JDK 17 as default `java` | needs admin, cosmetic | nothing |
| PowerShell 7 | needs admin, optional | nothing |
| Visual Studio C++ | deliberately skipped | Flutter **Windows desktop** only, not mobile |
| Real project + `git init` | awaiting app name / org id | VS Code device picker, F5, hot reload |
| React Native / MAUI / Capacitor | different stack | files 05, 06 |
| Store accounts, signing, CI | pre-release concerns | files 08, 09 |

See [ELEVATED-TASKS.ps1](ELEVATED-TASKS.ps1) for the admin items.
