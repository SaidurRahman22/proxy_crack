import 'package:flutter/material.dart';

import 'app/proxy_crack_app.dart';

void main() {
  // No orientation lock and no platform gate: this shell is the foundation
  // for the later Windows/Linux/macOS builds as well as Android, so every
  // layout has to survive being resized.
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProxyCrackApp());
}
