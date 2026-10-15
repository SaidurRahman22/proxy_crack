import 'package:flutter/widgets.dart';

/// Window size classes.
///
/// Android is phase one; Windows, Linux and macOS builds come out of this
/// same codebase later. Screens therefore branch on *window width*, never on
/// `Platform.isAndroid`, and never on an assumed orientation.
///
/// Thresholds follow Material 3 window size classes.
enum WindowSize {
  /// < 600 dp. Phones in portrait, small windows. Bottom navigation.
  compact,

  /// 600-1023 dp. Tablets, phones in landscape, half-screen desktop windows.
  /// Navigation rail, single pane.
  medium,

  /// >= 1024 dp. Desktop. Navigation rail plus a two-pane body.
  expanded;

  static const double mediumMin = 600;
  static const double expandedMin = 1024;

  static WindowSize fromWidth(double width) {
    if (width >= expandedMin) return WindowSize.expanded;
    if (width >= mediumMin) return WindowSize.medium;
    return WindowSize.compact;
  }

  static WindowSize of(BuildContext context) =>
      fromWidth(MediaQuery.sizeOf(context).width);

  /// True when navigation chrome should sit at the bottom of the window.
  bool get usesBottomNav => this == WindowSize.compact;

  /// True when a list/detail split is worth showing.
  bool get supportsTwoPane => this == WindowSize.expanded;
}

/// Layout constants that scale instead of being hard-coded to a phone.
abstract final class Layout {
  /// Content is centred and capped on wide windows so lines of text do not
  /// stretch to 1600 px.
  static const double readableMaxWidth = 720;

  /// Cap for a single card column inside a multi-column grid.
  static const double cardMaxWidth = 460;

  /// Width reserved for the list pane in a two-pane layout.
  static const double listPaneWidth = 420;

  /// Horizontal page gutter, responsive to the window size class.
  static double gutter(WindowSize size) => switch (size) {
        WindowSize.compact => 16,
        WindowSize.medium => 24,
        WindowSize.expanded => 32,
      };

  /// How many equal columns a card grid should use at this width.
  static int columnsFor(double width) {
    if (width >= 1280) return 3;
    if (width >= WindowSize.mediumMin) return 2;
    return 1;
  }
}

/// Centres and width-caps page content without changing it on a phone.
class ReadableWidth extends StatelessWidget {
  const ReadableWidth({
    super.key,
    required this.child,
    this.maxWidth = Layout.readableMaxWidth,
  });

  final Widget child;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: child,
      ),
    );
  }
}
