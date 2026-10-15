import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/routing/mock_proxy_engine.dart';
import '../core/routing/proxy_engine.dart';
import '../core/theme/app_theme.dart';
import '../data/repositories/catalog_repository.dart';
import '../features/shell/app_shell.dart';
import 'app_controller.dart';
import 'app_scope.dart';

/// Composition root.
///
/// This is the *only* place that names a concrete [ProxyEngine]. Dropping in
/// the real sing-box/Xray engine later is a one-line change here -- see
/// architecture.md section 2.
class ProxyCrackApp extends StatefulWidget {
  const ProxyCrackApp({
    super.key,
    this.engineOverride,
    this.repositoryOverride,
  });

  /// Injection points for tests.
  final ProxyEngine? engineOverride;
  final CatalogRepository? repositoryOverride;

  @override
  State<ProxyCrackApp> createState() => _ProxyCrackAppState();
}

class _ProxyCrackAppState extends State<ProxyCrackApp> {
  late final AppController _controller = AppController(
    engine: widget.engineOverride ?? MockProxyEngine(),
    repository: widget.repositoryOverride ?? MockCatalogRepository(),
  );

  @override
  void initState() {
    super.initState();
    _controller.load();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: AppTheme.systemOverlay,
      child: AppScope(
        controller: _controller,
        child: MaterialApp(
          title: 'Proxy Crack',
          debugShowCheckedModeBanner: false,
          themeMode: ThemeMode.dark,
          theme: AppTheme.dark,
          darkTheme: AppTheme.dark,
          home: const AppShell(),
        ),
      ),
    );
  }
}
