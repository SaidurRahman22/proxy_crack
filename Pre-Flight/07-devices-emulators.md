# 07 — Devices, Emulators, Simulators

You need somewhere to actually run the app. Applies to every stack.

---

## Android emulator (AVD)

- [ ] Hardware acceleration confirmed — `emulator -accel-check` (see [00](00-common-baseline.md))
- [ ] Create an AVD:
  - **Android Studio** → `Device Manager` → `Create Device`, or
  - CLI: `avdmanager create avd -n Pixel8_API36 -k "system-images;android-36;google_apis;x86_64" -d pixel_8`
- [ ] Pick an image that matches your CPU: `x86_64` on Intel/AMD, `arm64-v8a` on Apple Silicon
- [ ] Choose `google_apis_playstore` **only** if you need the Play Store inside the emulator (it blocks root access)
- [ ] Allocate sensible RAM (2–4 GB) and enough internal storage
- [ ] Launch: `emulator -avd Pixel8_API36` or `flutter emulators --launch <id>`
- [ ] Verify it is visible: `adb devices`
- [ ] Know the troubleshooting moves: **Cold Boot Now**, **Wipe Data**, and `adb kill-server && adb start-server`

**Keep at least two AVDs** — one at your `minSdk` and one at your `targetSdk`. Most API-level bugs only appear on the old one.

## Physical Android device (faster and more honest than an emulator)

- [ ] **Developer Options** unlocked — `Settings → About phone` → tap **Build number** 7 times
- [ ] **USB debugging** enabled in Developer Options
- [ ] USB cable that supports **data**, not charge-only — a surprisingly common time sink
- [ ] OEM USB driver installed if Windows doesn't detect the device (Samsung, Xiaomi, etc. ship their own)
- [ ] Accept the **"Allow USB debugging?"** RSA prompt on the phone
- [ ] Verify: `adb devices` → shows `device`, not `unauthorized` or `offline`
- [ ] *(optional)* **Wireless debugging** — Android 11+:
  ```bash
  adb pair <ip>:<port>      # code from Settings → Developer options → Wireless debugging
  adb connect <ip>:<port>
  ```
- [ ] *(optional)* **scrcpy** — mirror and control the phone from your desktop: `winget install Genymobile.scrcpy`

## iOS simulator — macOS only

- [ ] Simulator runtime downloaded in Xcode → Settings → Platforms
- [ ] `xcrun simctl list devices` shows bootable devices
- [ ] `open -a Simulator` to launch
- [ ] Remember: the simulator does **not** support the camera, push notifications, or real performance profiling

## Physical iPhone — macOS only

- [ ] Device trusted on the Mac
- [ ] **Developer Mode** enabled — `Settings → Privacy & Security → Developer Mode` (iOS 16+)
- [ ] Device registered in the Apple Developer portal
- [ ] Free Apple ID works for 7-day sideloads; a paid account is needed for anything longer

## Cloud device farms (optional, valuable before release)

- [ ] **Firebase Test Lab** — real devices, free tier, integrates with CI
- [ ] **BrowserStack App Live** / **Sauce Labs** — manual testing on real hardware
- [ ] **AWS Device Farm**
- [ ] Useful for catching OEM-specific bugs (Samsung One UI, Xiaomi MIUI) you can't reproduce locally

## Test matrix to define now

- [ ] Minimum Android version you support
- [ ] Minimum iOS version you support
- [ ] One small-screen phone, one large phone, one tablet
- [ ] A foldable, if your audience uses them
- [ ] Dark mode and large-font accessibility settings
- [ ] Slow/offline network behavior

---

**Next:** [08-accounts-signing-stores.md](08-accounts-signing-stores.md)
