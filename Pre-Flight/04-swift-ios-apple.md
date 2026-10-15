# 04 — Swift / SwiftUI and the Apple Toolchain

---

## 🚫 Hard gate: macOS is required

There is **no supported way** to build, sign, run, or submit an iOS app from Windows or Linux. Xcode is macOS-only, and Apple's signing and simulator tooling is part of it.

This is not a limitation you can engineer around locally. Your options:

| Option | Cost | Good for |
|---|---|---|
| **Mac hardware** (Mac mini is the cheap entry) | one-time | serious iOS work |
| **Cloud Mac** — MacStadium, Scaleway, MacinCloud | monthly | occasional builds |
| **CI with macOS runners** — GitHub Actions, Codemagic, Bitrise | per-minute / free tier | automated release builds |
| **Expo EAS Build** (React Native only) | free tier available | RN apps, no Mac at all |

- [ ] Decide which of the above you will use **before** committing to an iOS target

---

## On the Mac

### 1. System

- [ ] macOS version recent enough for your target Xcode (Xcode drops older macOS fast)
- [ ] **30–50 GB free disk** for Xcode plus simulator runtimes
- [ ] Apple ID signed in

### 2. Xcode

- [ ] Install **Xcode** from the Mac App Store (or Apple Developer downloads for a specific version)
- [ ] Launch it once to install additional components
- [ ] **Command Line Tools**:
  ```bash
  xcode-select --install
  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
  ```
- [ ] Accept the license: `sudo xcodebuild -license accept`
- [ ] Verify: `xcodebuild -version`
- [ ] *(optional)* **Xcodes.app** or `xcodes` CLI to manage multiple Xcode versions

### 3. Simulators

- [ ] Download at least one **iOS Simulator runtime** — `Xcode → Settings → Platforms`
- [ ] Verify: `xcrun simctl list devices`

### 4. Dependency managers

- [ ] **Swift Package Manager** — built into Xcode, prefer it for new dependencies
- [ ] **CocoaPods** — still required by many Flutter and React Native plugins:
  ```bash
  sudo gem install cocoapods
  pod --version
  ```
- [ ] *(Apple Silicon)* if CocoaPods misbehaves, install Ruby via `rbenv` rather than using system Ruby
- [ ] *(optional)* Carthage — legacy, rarely needed now

### 5. Ruby and fastlane

- [ ] **rbenv** or **rvm** — don't rely on macOS system Ruby
- [ ] **bundler** — `gem install bundler`
- [ ] **fastlane** — `brew install fastlane` — for automating signing, builds, screenshots, and TestFlight uploads

### 6. Code quality

- [ ] **SwiftLint** — `brew install swiftlint`
- [ ] **swift-format** — formatting
- [ ] Decide **SwiftUI** (modern default) vs **UIKit** (mature, more control) vs mixed

---

## Apple account and signing

- [ ] **Free Apple ID** — enough to run on a simulator and sideload to your own device for 7 days
- [ ] **Apple Developer Program — $99/year** — required for TestFlight, the App Store, push notifications, and most capabilities
- [ ] **Bundle Identifier** reserved (reverse-DNS, permanent)
- [ ] **Certificates**: Development + Distribution
- [ ] **Provisioning profiles**: Development, Ad Hoc, App Store
- [ ] **App Store Connect** app record created
- [ ] *(strongly recommended)* **fastlane match** — stores signing assets in a private git repo so your team and CI share them without manual Keychain surgery
- [ ] Understand **automatic vs manual signing** — automatic is fine solo, manual is usually needed in CI

## Physical iPhone testing

- [ ] Device connected and **trusted** on the Mac
- [ ] **Developer Mode** enabled on the device — `Settings → Privacy & Security → Developer Mode` (iOS 16+)
- [ ] Device registered in your developer account (automatic with a paid account and automatic signing)

---

**Next:** [07-devices-emulators.md](07-devices-emulators.md) · [08-accounts-signing-stores.md](08-accounts-signing-stores.md)
