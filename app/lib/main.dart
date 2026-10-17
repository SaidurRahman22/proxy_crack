import 'package:flutter/material.dart';

import 'app/app_services.dart';
import 'app/proxy_crack_app.dart';

void main() {
  // No orientation lock and no platform gate: this shell is the foundation
  // for the later Windows/Linux/macOS builds as well as Android, so every
  // layout has to survive being resized.
  WidgetsFlutterBinding.ensureInitialized();

  // Built here rather than inside the widget so the base URL, the token
  // store and the transport chain exist exactly once, and so a test can
  // substitute them wholesale.
  //
  //   flutter run \
  //     --dart-define=PROXY_CRACK_API_BASE_URL=http://10.0.2.2:8000
  //     --dart-define=PROXY_CRACK_USE_MOCK_API=false
  //
  // See app/README-api.md for the emulator / device / desktop addresses.
  runApp(ProxyCrackApp(services: AppServices.create()));
}
