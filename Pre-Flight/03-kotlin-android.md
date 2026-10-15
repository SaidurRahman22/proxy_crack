# 03 — Kotlin / Java (Native Android) + Kotlin Multiplatform

**Prerequisites:** [00-common-baseline.md](00-common-baseline.md) and [01-android-sdk-jdk.md](01-android-sdk-jdk.md) complete.

---

## ⚠️ Editor reality check

VS Code is a **poor fit for native Android development**. There is no official Kotlin language server from JetBrains; the community extensions give you syntax highlighting and little else. You lose the layout inspector, the visual Compose preview, the APK analyzer, the profilers, and reliable refactoring.

- [ ] **Use Android Studio as the primary IDE for Kotlin.** Keep VS Code for the web/docs/config side of the project.
- [ ] *(If you insist on VS Code)* install `fwcd.kotlin` + `vscjava.vscode-gradle` and accept that builds run via `./gradlew` in the terminal.

## 1. Android Studio

- [ ] `winget install Google.AndroidStudio`
- [ ] Complete the setup wizard (installs SDK, platform-tools, an emulator image)
- [ ] Sign in to your Google account for Firebase/Play integration (optional)
- [ ] Configure the embedded JDK: `File → Settings → Build, Execution, Deployment → Build Tools → Gradle → Gradle JDK` → JDK 17
- [ ] Increase IDE memory if you have RAM: `Help → Change Memory Settings` → 4096 MB

## 2. Kotlin

- [ ] Kotlin plugin — **bundled with Android Studio**, no separate install
- [ ] Pin the Kotlin version in `gradle/libs.versions.toml` rather than scattering it across build files
- [ ] Use **Kotlin 2.x (K2 compiler)** for new projects
- [ ] Decide **Kotlin DSL (`.kts`)** vs Groovy for build scripts — `.kts` is the current default and gives you autocomplete

## 3. Project scaffolding decisions

- [ ] **UI toolkit:** **Jetpack Compose** (default for new work) or XML Views (legacy, still common)
- [ ] If Compose: enable the **Compose Compiler Gradle plugin** (required from Kotlin 2.0 onward)
- [ ] **`minSdk`** — decide your floor; 24+ is a common compromise, 26+ unlocks more APIs
- [ ] **`targetSdk` / `compileSdk`** — must satisfy the current Play Store requirement
- [ ] **`applicationId`** — reverse-DNS, permanent once published. Choose carefully.
- [ ] **Version catalog** (`gradle/libs.versions.toml`) set up for dependency management
- [ ] **Architecture:** MVVM or MVI; Jetpack ViewModel + StateFlow is the standard spine
- [ ] **DI:** Hilt (Google's, annotation-based) or Koin (Kotlin-native, simpler)
- [ ] **Async:** Coroutines + Flow — not RxJava for new projects
- [ ] **Networking:** Retrofit + OkHttp, or Ktor Client
- [ ] **Local storage:** Room (SQL) and DataStore (replaces SharedPreferences)
- [ ] **Serialization:** `kotlinx.serialization`

## 4. Code quality tooling

- [ ] **ktlint** — formatting
- [ ] **detekt** — static analysis
- [ ] Android Lint — built in, but configure `lint.xml` and decide which checks are errors
- [ ] *(optional)* Spotless to run formatters from Gradle
- [ ] Pre-commit hook that runs ktlint + detekt

## 5. Java-only Android (legacy)

- [ ] Same SDK/JDK/Gradle setup as above
- [ ] Only choose Java for maintaining existing code — **Kotlin is the official language for new Android development** and all current Google samples and libraries assume it

---

## Kotlin Multiplatform (KMP) — share code with iOS

- [ ] All of the above, plus:
- [ ] **`kdoctor`** — the KMP equivalent of `flutter doctor`:
  ```bash
  brew install kdoctor   # macOS
  kdoctor
  ```
- [ ] **Kotlin Multiplatform plugin** in Android Studio
- [ ] Understand the split: shared business logic in `commonMain`; UI either **Compose Multiplatform** (shared) or native SwiftUI per platform
- [ ] **A Mac is required to build, run, or test the iOS target** — Xcode, CocoaPods, and the iOS simulator all live there
- [ ] CocoaPods integration if consuming iOS pods — `sudo gem install cocoapods`
- [ ] Decide early: **Compose Multiplatform UI** (one UI, faster) vs **native UI per platform** (better platform feel, more work)
- [ ] Expect a heavier toolchain and slower builds than single-platform Android

> On Windows you can develop and build the **Android** and **shared** targets of a KMP project. The iOS target will not compile locally.

---

**Next:** [07-devices-emulators.md](07-devices-emulators.md)
