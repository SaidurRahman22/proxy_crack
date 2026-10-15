# Pre-Flight — Mobile App Development Setup Checklist

Everything to install, configure, and verify **before** writing the first line of a mobile app.

Written Windows-first (this machine is Windows 11), with macOS/Linux notes wherever they differ.
Tick boxes as you go: change `- [ ]` to `- [x]`.

---

## How to use this folder

| Order | File | Who needs it |
|---|---|---|
| 1 | [00-common-baseline.md](00-common-baseline.md) | **Everyone** — hardware, OS flags, Git, editor |
| 2 | [01-android-sdk-jdk.md](01-android-sdk-jdk.md) | **Everyone targeting Android** — JDK, SDK, Gradle |
| 3 | [02-flutter-dart.md](02-flutter-dart.md) | Flutter / Dart |
| 3 | [03-kotlin-android.md](03-kotlin-android.md) | Kotlin / Java native + Kotlin Multiplatform |
| 3 | [04-swift-ios-apple.md](04-swift-ios-apple.md) | Swift / SwiftUI — **macOS only** |
| 3 | [05-react-native-expo.md](05-react-native-expo.md) | React Native / Expo (JS + TypeScript) |
| 3 | [06-other-stacks.md](06-other-stacks.md) | .NET MAUI (C#), Capacitor/Ionic, NativeScript, Unity |
| 4 | [07-devices-emulators.md](07-devices-emulators.md) | **Everyone** — emulators, real phones, device farms |
| 5 | [08-accounts-signing-stores.md](08-accounts-signing-stores.md) | **Everyone shipping** — store accounts, keys, certs |
| 6 | [09-ci-testing-monitoring.md](09-ci-testing-monitoring.md) | Recommended before the first release |
| 7 | [10-verification.md](10-verification.md) | **Everyone** — prove the setup actually works |

Do **1 → 2 → your stack → 4 → 7**. Files 5 and 6 can wait until you are close to shipping.

---

## Pick your stack first

| Stack | Language | iOS from Windows? | Best editor | Install weight |
|---|---|---|---|---|
| **Flutter** | Dart | ❌ needs a Mac or CI | VS Code or Android Studio | ~3 GB + Android SDK |
| **Kotlin (native Android)** | Kotlin | n/a — Android only | Android Studio (VS Code is weak here) | Android Studio + SDK |
| **Kotlin Multiplatform** | Kotlin | ❌ needs a Mac for the iOS half | Android Studio / Fleet | heavy |
| **Swift / SwiftUI** | Swift | ❌ **macOS required, no workaround** | Xcode | Mac + ~15 GB |
| **React Native + Expo** | TypeScript | ✅ via EAS cloud build | VS Code | lightest |
| **.NET MAUI** | C# | ❌ needs a Mac to build iOS | VS Code / Visual Studio | ~5 GB |
| **Capacitor / Ionic** | TypeScript + web | ❌ needs a Mac for iOS | VS Code | light |

**Rule of thumb:** on a Windows machine, Android work is fully local for every stack; iOS work always needs a Mac, a cloud Mac, or a cloud build service. Plan for that before you choose.

---

## State of this machine — updated 2026-10-15

✅ **Setup complete and verified end-to-end.** See [INSTALL-LOG.md](INSTALL-LOG.md) for full detail.

| Tool | Status |
|---|---|
| Node.js / npm / Git / Python | ✅ already present |
| **JDK 17** (Temurin 17.0.20.1) | ✅ `D:\dev\jdk-17`, `JAVA_HOME` set |
| **Android SDK** | ✅ `D:\devndroid-sdk` — platform 36, build-tools 36.0.0, NDK 28.2 |
| **adb** 37.0.1 / **emulator** 37.2.12 | ✅ on PATH |
| **Flutter 3.47.7** (Dart 3.13.5) | ✅ `D:\devlutter`, doctor passes |
| **AVD** `Pixel8_API36` | ✅ Android 16, x86_64, WHPX accelerated |
| VS Code extensions | ✅ Dart, Flutter, GitLens, EditorConfig, Error Lens |
| Hello-world build → install → run | ✅ verified on emulator |
| `git config user.name` | ⚠️ still unset |
| Defender exclusions / Android Studio | ⚠️ need admin — see [ELEVATED-TASKS.ps1](ELEVATED-TASKS.ps1) |
| Visual Studio C++ | ⏭️ skipped — Flutter *Windows desktop* only, not mobile |

**To launch the emulator:** double-click **"Android Emulator"** on your Desktop, or run
`emulator -avd Pixel8_API36` in a **new** terminal.

## Budget before you begin

| Resource | Amount |
|---|---|
| Disk — Flutter + Android SDK + emulator images | **25–40 GB** |
| Disk — Xcode + simulators (on a Mac) | **30–50 GB** |
| RAM to run an emulator comfortably | **16 GB** minimum |
| Google Play Console | **$25** one-time |
| Apple Developer Program | **$99 / year** |
| First-time setup wall-clock | **3–6 hours** including downloads |
