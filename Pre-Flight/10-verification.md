# 10 — Verification

Prove the setup works **before** you start building. Every command below should succeed in a **fresh terminal**.

---

## Core

```bash
git --version
node --version
npm --version
java -version          # must report 17.x (or 21.x), NOT 23
echo $JAVA_HOME        # PowerShell: $env:JAVA_HOME
```

- [ ] All return a version, no "not recognized"
- [ ] `java -version` shows **17** or **21** — if it still shows 23, your PATH order is wrong

## Android

```bash
echo $ANDROID_HOME                # PowerShell: $env:ANDROID_HOME
adb --version
sdkmanager --list_installed
emulator -list-avds
emulator -accel-check
adb devices
```

- [ ] `ANDROID_HOME` points at a real SDK directory
- [ ] `adb --version` works
- [ ] At least one AVD listed
- [ ] `-accel-check` reports acceleration is available
- [ ] `adb devices` lists a running emulator or a connected phone as `device`

## Flutter

```bash
flutter --version
dart --version
flutter doctor -v
flutter devices
flutter emulators
```

- [ ] `flutter doctor` is clean for every platform you target
- [ ] Android licenses accepted (no ⚠️ about licenses)
- [ ] `flutter devices` shows at least one target

## Kotlin / Android native

```bash
cd <your-project>
./gradlew --version
./gradlew tasks
./gradlew assembleDebug
```

- [ ] Gradle wrapper runs and reports JVM 17
- [ ] A debug APK builds

## React Native / Expo

```bash
npx expo-doctor          # Expo projects
npx react-native doctor  # bare RN projects
```

- [ ] No ❌ entries

## iOS (macOS only)

```bash
xcodebuild -version
xcrun simctl list devices | grep Booted
pod --version
```

- [ ] Xcode reports a version
- [ ] A simulator can boot

---

## The real test: build and run a hello-world

Don't declare setup finished until an app actually renders on a screen.

```bash
# Flutter
flutter create hello && cd hello && flutter run

# React Native / Expo
npx create-expo-app@latest hello && cd hello && npx expo start

# Android native — create in Android Studio, then:
./gradlew installDebug
```

- [ ] App launches on emulator or device
- [ ] **Hot reload works** — change a string, save, see it update without a full rebuild
- [ ] Debugger attaches and a breakpoint hits
- [ ] A release build completes: `flutter build apk --release` / `./gradlew assembleRelease`

---

## Green light

You are ready to start development when:

- [ ] A hello-world app runs on a **real device**
- [ ] A hello-world app runs on an **emulator**
- [ ] Hot reload / fast refresh works
- [ ] Breakpoints hit in the debugger
- [ ] A **release** build completes and is signed
- [ ] The project is in git with a correct `.gitignore`
- [ ] A teammate (or a clean machine) could follow these files and reach the same state

---

## Common failure modes

| Symptom | Cause |
|---|---|
| `adb` not recognized | `platform-tools` not on PATH, or terminal not restarted |
| Gradle: "Unsupported class file major version" | Wrong JDK — you're on 23, need 17 |
| Emulator extremely slow | Hardware acceleration off; enable Windows Hypervisor Platform |
| Flutter plugin errors on Windows | Developer Mode is off |
| "Failed to install, path too long" | Long path support not enabled |
| Builds take forever | Antivirus scanning the build and cache folders |
| `unauthorized` in `adb devices` | RSA prompt not accepted on the phone |
| CocoaPods errors on Apple Silicon | System Ruby — install Ruby via rbenv |
| iOS build attempted on Windows | Not possible — use a Mac, cloud Mac, or CI |
