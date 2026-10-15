import 'package:flutter/widgets.dart';

import 'app_controller.dart';

/// Makes the single [AppController] available to the widget tree and rebuilds
/// dependents when it notifies.
class AppScope extends InheritedNotifier<AppController> {
  const AppScope({
    super.key,
    required AppController controller,
    required super.child,
  }) : super(notifier: controller);

  /// Subscribes the calling widget to controller changes.
  static AppController of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppScope>();
    assert(scope != null, 'AppScope.of() called outside an AppScope.');
    return scope!.notifier!;
  }

  /// Reads the controller without subscribing -- for callbacks.
  static AppController read(BuildContext context) {
    final scope = context.getInheritedWidgetOfExactType<AppScope>();
    assert(scope != null, 'AppScope.read() called outside an AppScope.');
    return scope!.notifier!;
  }
}
