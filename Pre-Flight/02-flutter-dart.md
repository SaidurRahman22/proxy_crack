# 02 — Flutter / Dart

Cross-platform: Android, iOS, web, Windows, macOS, Linux from one Dart codebase.

**Prerequisites:** [00-common-baseline.md](00-common-baseline.md) and [01-android-sdk-jdk.md](01-android-sdk-jdk.md) complete.

---

## 1. Install the Flutter SDK

Pick one:

- [ ] **winget** — `winget install --id Google.Flutter`
- [ ] **Manual** — download the stable zip, extract to `C:\src\flutter`
  - ⚠️ **Not** in `C:\Program Files` (needs write access without elevation) and **not** in a path with spaces
- [ ] **FVM** (Flutter Version Management) — recommended if you will work on more than one project:
  ```bash
  dart pub global activate fvm
  fvm install stable
  fvm use stable
  ```

Steps after install:

- [ ] Add `<flutter-root>\bin` to **PATH**
- [ ] Restart the terminal, verify: `flutter --version` and `dart --version`
- [ ] Dart comes bundled with Flutter — **do not** install it separately

## 2. Windows-specific requirements

- [ ] **Developer Mode is ON** (`Settings → System → For developers`) — Flutter plugins use symlinks and will fail without it
- [ ] Antivirus exclusions for the Flutter SDK folder and `~\AppData\Local\Pub\Cache`

## 3. Run the doctor

- [ ] `flutter doctor -v`
- [ ] Resolve every ❌ and ⚠️ except the ones for platforms you don't target
- [ ] Accept Android licenses: `flutter doctor --android-licenses`
- [ ] If Flutter can't find the SDK: `flutter config --android-sdk "%LOCALAPPDATA%\Android\Sdk"`
- [ ] If Flutter can't find Studio: `flutter config --android-studio-dir "C:\Program Files\Android\Android Studio"`

A clean Windows result looks like: Flutter ✓, Android toolchain ✓, Chrome ✓, Visual Studio (for Windows desktop) ✓ or ⚠️, Android Studio ✓, VS Code ✓, connected device ✓.

## 4. Choose target platforms

- [ ] Enable/disable what you need so builds stay lean:
  ```bash
  flutter config --enable-android
  flutter config --no-enable-web          # if you don't target web
  flutter config --no-enable-windows-desktop
  ```
- [ ] *(Windows desktop target only)* install **Visual Studio** with the **Desktop development with C++** workload — this is Visual Studio, not VS Code

## 5. VS Code setup

- [ ] **Flutter** extension (Dart-Code.flutter) — pulls in the Dart extension automatically
- [ ] **Dart** extension (Dart-Code.dart-code)
- [ ] *(optional)* Awesome Flutter Snippets, Error Lens, Pubspec Assist
- [ ] Verify the command palette has `Flutter: New Project` and the status bar shows a device selector
- [ ] Know the keys: **hot reload** on save, `r` in the terminal, **hot restart** `R`
- [ ] Open **DevTools** — `Flutter: Open DevTools` — for the widget inspector, performance overlay, and memory view

## 6. Project conventions to set up front

- [ ] `flutter create my_app --org com.yourcompany` — the org becomes your `applicationId` / bundle ID and is **painful to change later**
- [ ] Add **`flutter_lints`** (or `very_good_analysis` for a stricter set) to `dev_dependencies`
- [ ] Configure `analysis_options.yaml`
- [ ] Decide state management **before** you build: Riverpod, Bloc, Provider, or `signals` — mixing them later is the usual source of Flutter spaghetti
- [ ] Decide navigation: `go_router` is the common default
- [ ] Set up flavors/environments (dev / staging / prod) early — retrofitting is messy
- [ ] Pin the Flutter version for the team — `.fvmrc` or a documented version in the README

## 7. iOS support (only on a Mac)

- [ ] macOS machine with **Xcode** installed
- [ ] `xcode-select --install` for the Command Line Tools
- [ ] **CocoaPods** — `sudo gem install cocoapods`
- [ ] `flutter doctor` should show the iOS toolchain ✓
- [ ] See [04-swift-ios-apple.md](04-swift-ios-apple.md) for the full Apple-side checklist

> **On Windows you cannot build iOS.** Options: a Mac, a cloud Mac (MacStadium, Scaleway), or CI with macOS runners (Codemagic, GitHub Actions, Bitrise). Your Dart code is still fully portable — only the build step needs the Mac.

## 8. Useful extras

- [ ] `dart pub global activate flutterfire_cli` — if using Firebase
- [ ] `melos` — if you plan a monorepo with multiple packages
- [ ] **Shorebird** — code push / OTA updates for Flutter
- [ ] `flutter_launcher_icons` and `flutter_native_splash` — icon and splash generation

---

**Next:** [07-devices-emulators.md](07-devices-emulators.md)
