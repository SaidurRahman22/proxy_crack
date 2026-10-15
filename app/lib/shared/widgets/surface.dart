import 'package:flutter/material.dart';

import '../../core/theme/app_dimens.dart';
import '../../core/theme/app_theme.dart';

/// The app's one card primitive: `--color-card` fill, hairline border,
/// 12px radius, no decoration beyond that (Swiss/minimal).
class Surface extends StatelessWidget {
  const Surface({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppSpacing.md),
    this.onTap,
    this.borderColor,
    this.background,
    this.semanticLabel,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final Color? borderColor;
  final Color? background;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final shape = BorderRadius.circular(AppRadius.card);

    final content = Padding(padding: padding, child: child);

    return Semantics(
      label: semanticLabel,
      container: semanticLabel != null,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: background ?? Theme.of(context).colorScheme.surfaceContainer,
          borderRadius: shape,
          border: Border.all(color: borderColor ?? tokens.border),
        ),
        child: onTap == null
            ? content
            : Material(
                color: Colors.transparent,
                borderRadius: shape,
                clipBehavior: Clip.antiAlias,
                child: InkWell(
                  onTap: onTap,
                  borderRadius: shape,
                  child: content,
                ),
              ),
      ),
    );
  }
}

/// Small uppercase section label. Swiss grid signposting.
class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key, this.trailing});

  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: <Widget>[
        Expanded(
          child: Text(
            text.toUpperCase(),
            style: context.text.labelSmall,
          ),
        ),
        ?trailing,
      ],
    );
  }
}
