# 09 — CI, Testing, and Monitoring

Not needed on day one, but far cheaper to set up before the codebase grows.

---

## Version control workflow

- [ ] Repo created (GitHub / GitLab / Bitbucket)
- [ ] Branching strategy chosen — trunk-based with short-lived branches works for most small teams
- [ ] `.gitignore` covers: `build/`, `.gradle/`, `node_modules/`, `ios/Pods/`, `*.jks`, `*.keystore`, `.env`, `local.properties`, `.dart_tool/`
- [ ] **Secrets scanning** enabled — a leaked API key in git history is permanent
- [ ] Branch protection on `main` — no direct pushes
- [ ] Conventional commits if you want automated changelogs

## Testing

- [ ] **Unit tests** — the business logic layer
  - Flutter: `flutter test` · Kotlin: JUnit + MockK · RN: Jest
- [ ] **Widget / component tests**
  - Flutter: `testWidgets` · Compose: `createComposeRule` · RN: React Native Testing Library
- [ ] **Integration / instrumented tests**
  - Flutter: `integration_test` · Android: Espresso · iOS: XCUITest
- [ ] **E2E tests** — **Maestro** is the simplest cross-platform option; Patrol (Flutter), Detox (RN) are alternatives
- [ ] Coverage reporting wired up
- [ ] Tests run on every PR

## CI/CD

- [ ] Pick a provider:
  - **GitHub Actions** — free tier, `macos-latest` runners for iOS
  - **Codemagic** — purpose-built for Flutter and mobile
  - **Bitrise** — mobile-focused, lots of prebuilt steps
  - **EAS Build** — if you're on Expo
- [ ] Pipeline stages: lint → test → build → distribute
- [ ] **Signing secrets in CI** — base64-encode the keystore into a secret, never commit it
- [ ] Build caching configured (Gradle cache, pub cache, node_modules) — the difference between 3-minute and 20-minute builds
- [ ] **fastlane** for store uploads, or `eas submit`
- [ ] Automated distribution to testers — **Firebase App Distribution** or TestFlight

## Crash reporting and monitoring — set up before the first public release

- [ ] **Sentry** or **Firebase Crashlytics** integrated
- [ ] **Source maps / dSYMs / ProGuard mappings uploaded** — without these, release crash reports are unreadable garbage
- [ ] Release tagging so crashes map to a specific version
- [ ] Alerting on crash-rate spikes
- [ ] **Play Console Vitals** and **Xcode Organizer** checked after each release

## Analytics and product telemetry

- [ ] Firebase Analytics, PostHog, Amplitude, or Mixpanel
- [ ] Key events defined **before** launch — retrofitting analytics onto a live app means a lost cohort
- [ ] Consent handling for GDPR regions

## Over-the-air updates

- [ ] **Expo EAS Update** (React Native) or **Shorebird** (Flutter)
- [ ] Understand the limits: JS/Dart changes only — native changes still require a store release
- [ ] Review both stores' current policies on OTA updates before relying on them

## Performance and release hygiene

- [ ] Release builds use **R8/ProGuard** (Android) — with rules tested, since obfuscation breaks reflection-based code
- [ ] App size checked — Android App Bundle, iOS app thinning
- [ ] Startup time measured
- [ ] Obvious memory leaks checked with the profiler
- [ ] Offline behavior tested
- [ ] Deep links / universal links tested on both platforms

---

**Next:** [10-verification.md](10-verification.md)
