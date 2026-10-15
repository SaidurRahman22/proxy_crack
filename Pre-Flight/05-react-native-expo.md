# 05 — React Native / Expo (JavaScript + TypeScript)

**The lightest path on this machine** — Node is already installed, and Expo can build iOS in the cloud without a Mac.

---

## 1. Node and package manager

- [x] **Node.js v24.18.0** installed
  - ⚠️ React Native sometimes lags on brand-new Node majors. If you hit odd Metro errors, drop to the current **Node 22 LTS** via `fnm` or `nvm-windows`.
- [x] **npm 11.16.0** installed
- [ ] *(optional)* a faster package manager — `pnpm` or `yarn` — `npm i -g pnpm`
- [ ] *(macOS only)* **Watchman** — `brew install watchman` — not needed on Windows

## 2. Choose your flavor

| | **Expo** ✅ recommended | **React Native CLI (bare)** |
|---|---|---|
| Setup | minutes | hours |
| Native code access | via config plugins + prebuild | direct |
| iOS build from Windows | ✅ EAS Build | ❌ |
| Best for | almost everything | deep native integration |

> Expo is no longer "the limited option" — you can eject to native code at any time with `npx expo prebuild`, and config plugins cover most native needs.

## 3. Expo setup

- [ ] Create the project (no global CLI install needed):
  ```bash
  npx create-expo-app@latest . --template default
  ```
- [ ] **Expo Go** app installed on a physical phone (Play Store / App Store) — **this is the fastest path to seeing your app run, with no Android SDK at all**
- [ ] Start the dev server: `npx expo start` → scan the QR code
- [ ] **EAS CLI** for cloud builds: `npm i -g eas-cli`
- [ ] Expo account created, then `eas login` and `eas init`
- [ ] Understand **Expo Go vs Development Build**: Expo Go runs a fixed set of native modules; once you add a custom native dependency you need a dev build (`eas build --profile development`)

## 4. React Native CLI (bare) setup

- [ ] Everything in [01-android-sdk-jdk.md](01-android-sdk-jdk.md) — JDK 17, Android SDK, `ANDROID_HOME`
- [ ] Create: `npx @react-native-community/cli@latest init MyApp`
- [ ] *(macOS)* CocoaPods + Xcode — see [04-swift-ios-apple.md](04-swift-ios-apple.md)
- [ ] Run `npx react-native doctor` and clear every ❌
- [ ] Know that **New Architecture** (Fabric + TurboModules) is the default in current RN versions — check third-party library compatibility before adopting them

## 5. VS Code extensions

- [ ] **Expo Tools** (expo.vscode-expo-tools)
- [ ] **React Native Tools** (msjsdiag.vscode-react-native)
- [ ] ESLint + Prettier
- [ ] *(if using NativeWind)* Tailwind CSS IntelliSense

## 6. Project decisions to make up front

- [ ] **TypeScript** — the default Expo template already includes it. Use it.
- [ ] **Navigation** — **Expo Router** (file-based, recommended) or React Navigation
- [ ] **Styling** — StyleSheet, **NativeWind** (Tailwind for RN), Tamagui, or Unistyles
- [ ] **State** — Zustand or Jotai for client state, **TanStack Query** for server state
- [ ] **Forms** — React Hook Form + Zod
- [ ] **ESLint + Prettier** configured and enforced
- [ ] **Bundle identifier / package name** set in `app.json` — permanent once published
- [ ] **Environment handling** — `.env` + `expo-constants`, with `.env` gitignored

## 7. Build and release tooling

- [ ] `eas build --platform android` — cloud APK/AAB
- [ ] `eas build --platform ios` — **cloud macOS builder; this is how you ship iOS from Windows**
- [ ] `eas submit` — uploads straight to Play Console / App Store Connect
- [ ] **EAS Update** — OTA JS updates without a store review
- [ ] Apple Developer account still required for the iOS App Store, even with EAS

---

## ⚡ Fastest possible start from this machine

```bash
cd /d/project/Proxy
npx create-expo-app@latest . --template default
npx expo start
# install Expo Go on your phone, scan the QR
```

No JDK change, no Android SDK, no emulator. Roughly 10 minutes to a running app on a real device. Add the Android SDK later when you need a release build or a native module.

---

**Next:** [07-devices-emulators.md](07-devices-emulators.md)
