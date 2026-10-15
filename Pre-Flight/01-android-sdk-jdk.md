# 01 — JDK, Android SDK, Gradle

Required by **Flutter, Kotlin, Java, React Native, .NET MAUI, and Capacitor** — every stack that produces an Android app.

---

## 1. JDK

⚠️ **This machine has JDK 23.** Android Gradle Plugin does not support it. Install an LTS JDK alongside it.

- [x] Install **JDK 17** (safest — the version AGP 8.x targets) or **JDK 21** (also LTS, supported by newer AGP):
  ```powershell
  winget install EclipseAdoptium.Temurin.17.JDK
  ```
- [x] Set **`JAVA_HOME`** to the JDK 17 root (no trailing `\bin`):
  ```powershell
  [Environment]::SetEnvironmentVariable("JAVA_HOME", "C:\Program Files\Eclipse Adoptium\jdk-17.0.x-hotspot", "User")
  ```
- [ ] Add `%JAVA_HOME%\bin` to **PATH**, ahead of any other JDK
- [ ] Verify in a **new** terminal: `java -version` → should report 17.x

> Android Studio bundles its own JBR (JetBrains Runtime). You can point Gradle at it instead:
> `org.gradle.java.home=C:\Program Files\Android\Android Studio\jbr` in `gradle.properties`.
> Keeping a standalone JDK 17 is cleaner for CLI and CI builds.

## 2. Android SDK — choose one install route

### Route A — Android Studio (recommended, easiest)

- [ ] `winget install Google.AndroidStudio`
- [ ] Run it once and complete the **Setup Wizard** (downloads SDK + platform-tools + an emulator image)
- [ ] You can still write code in VS Code — Studio is here for the SDK, the emulator, the AVD manager, and the profilers

### Route B — Command-line tools only (lighter, no IDE)

- [x] Download `commandlinetools-win-*.zip` from the Android developer site
- [x] Extract to `%LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest\` (the `latest` folder name matters)
- [ ] Install packages:
  ```powershell
  sdkmanager "platform-tools" "emulator" "platforms/android-36" "build-tools/36.0.0" "system-images/android-36/google_apis/x86_64"
  ```
  > ⚠️ **Use `/` separators, not `;`.** `sdkmanager.bat` runs via `cmd.exe`, which treats `;` as an
  > argument separator — so the widely-documented `platforms;android-36` form silently splits into two
  > bogus package names, skips the install, and **still exits 0**. The current Android CLI accepts `/`.
  > Always confirm with `ls %ANDROID_HOME%\platforms` rather than trusting the exit code.

## 3. SDK components to install

- [ ] **Android SDK Platform-Tools** — gives you `adb` and `fastboot`
- [ ] **Android SDK Build-Tools** — latest stable
- [ ] **Android SDK Platform** — the API level you will `compileSdk` against
- [ ] **Android Emulator**
- [ ] **A system image** — pick `google_apis` (or `google_apis_playstore` if you need the Play Store in the emulator), `x86_64` on Intel/AMD, `arm64-v8a` on Apple Silicon
- [ ] **Android SDK Command-line Tools (latest)** — Flutter and CI need this even if you used Studio
- [ ] *(optional)* **Google Play services / Google Repository** if you use Firebase or Maps

> **Target API level:** Google Play enforces a minimum `targetSdk` for new and updated apps, and it moves up every August. Check the current requirement in the Play Console before locking yours in.

## 4. Environment variables

- [x] **`ANDROID_HOME`** = `%LOCALAPPDATA%\Android\Sdk`
- [x] **`ANDROID_SDK_ROOT`** = same value (some older tools still read this name)
- [ ] Add to **PATH**:
  - [x] `%ANDROID_HOME%\platform-tools`
  - [x] `%ANDROID_HOME%\emulator`
  - [ ] `%ANDROID_HOME%\cmdline-tools\latest\bin`
  ```powershell
  [Environment]::SetEnvironmentVariable("ANDROID_HOME", "$env:LOCALAPPDATA\Android\Sdk", "User")
  [Environment]::SetEnvironmentVariable("ANDROID_SDK_ROOT", "$env:LOCALAPPDATA\Android\Sdk", "User")
  ```
- [ ] **Restart the terminal** (and VS Code) so it picks up the new environment
- [ ] Verify: `adb --version`

## 5. Accept SDK licenses

Builds fail silently-ish until you do this.

- [ ] `sdkmanager --licenses` — on current CLI versions this reports *"no longer needed"*; licenses are
      accepted at install time and a `licenses/` folder appears under `%ANDROID_HOME%` automatically
- [ ] *(Flutter users)* run `flutter doctor --android-licenses` — Flutter checks for that `licenses/`
      folder specifically, so run it even if sdkmanager says it is unnecessary

## 6. Gradle

- [x] **Do NOT install Gradle globally.** Every Android project ships a **Gradle wrapper** (`gradlew` / `gradlew.bat`) that pins the correct version. Use it.
- [ ] Know the layout: `settings.gradle(.kts)`, root `build.gradle(.kts)`, module `app/build.gradle(.kts)`, `gradle/libs.versions.toml` (version catalog)
- [ ] Tune `gradle.properties` for your machine:
  ```properties
  org.gradle.jvmargs=-Xmx4096m -XX:MaxMetaspaceSize=1024m
  org.gradle.parallel=true
  org.gradle.caching=true
  android.useAndroidX=true
  ```
- [ ] Add `~/.gradle` to your antivirus exclusions (see file 00)
- [ ] First `./gradlew build` will download a lot — expect several minutes

## 7. Emulator acceleration

- [x] Windows Hypervisor Platform enabled (file 00)
- [ ] Verify: `emulator -accel-check`
- [ ] If it reports no acceleration, the emulator will run at ~1 fps — fix this before blaming your code

---

**Next:** your stack file — [02 Flutter](02-flutter-dart.md) · [03 Kotlin](03-kotlin-android.md) · [05 React Native](05-react-native-expo.md) · [06 Other](06-other-stacks.md)
