import 'package:flutter/material.dart';

import '../../../core/motion/motion.dart';
import '../../../core/routing/proxy_engine.dart';
import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';

/// The emotional centre of the app: one large, unambiguous control.
///
/// The *fill* reports the current state; the *label* names the action the tap
/// performs. Size is capped so it still fits a 375 px viewport with the stats
/// block visible underneath.
class ConnectButton extends StatefulWidget {
  const ConnectButton({
    super.key,
    required this.status,
    required this.onPressed,
  });

  final EngineStatus status;
  final VoidCallback? onPressed;

  @override
  State<ConnectButton> createState() => _ConnectButtonState();
}

class _ConnectButtonState extends State<ConnectButton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: AppMotion.pulse,
  );

  @override
  void didUpdateWidget(covariant ConnectButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncPulse();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _syncPulse();
  }

  void _syncPulse() {
    final shouldRun = widget.status.state == EngineState.connecting &&
        MotionScope.allowAmbient(context);
    if (shouldRun && !_pulse.isAnimating) {
      _pulse.repeat(reverse: true);
    } else if (!shouldRun && _pulse.isAnimating) {
      _pulse.stop();
      _pulse.value = 0;
    }
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final scheme = Theme.of(context).colorScheme;
    final state = widget.status.state;

    final (Color fill, Color ring, Color content, IconData icon, String action) =
        switch (state) {
      EngineState.disconnected => (
          tokens.accent,
          tokens.accent,
          tokens.onAccent,
          Icons.power_settings_new,
          'Connect',
        ),
      EngineState.connecting => (
          scheme.secondary,
          tokens.accent,
          tokens.subtleForeground,
          Icons.sync,
          'Connecting',
        ),
      EngineState.connected => (
          scheme.surfaceContainer,
          tokens.accent,
          tokens.accent,
          Icons.lock_outline,
          'Disconnect',
        ),
      EngineState.disconnecting => (
          scheme.secondary,
          tokens.subtleForeground,
          tokens.subtleForeground,
          Icons.sync,
          'Stopping',
        ),
      EngineState.error => (
          scheme.surfaceContainer,
          tokens.destructiveText,
          tokens.destructiveText,
          Icons.priority_high,
          'Retry',
        ),
    };

    final enabled = widget.onPressed != null && !widget.status.isBusy;

    return LayoutBuilder(
      builder: (context, constraints) {
        final size = constraints.maxWidth.isFinite
            ? constraints.maxWidth.clamp(180.0, 232.0)
            : 220.0;

        return Center(
          child: Semantics(
            button: true,
            enabled: enabled,
            label: '$action. Tunnel is ${_stateWord(state)}.',
            child: AnimatedBuilder(
              animation: _pulse,
              builder: (context, child) {
                final glow = state == EngineState.connected
                    ? 0.55
                    : state == EngineState.connecting
                        ? 0.25 + _pulse.value * 0.5
                        : 0.0;
                return Container(
                  width: size,
                  height: size,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: ring.withValues(alpha: 0.28 + glow * 0.5),
                      width: 2,
                    ),
                    boxShadow: glow > 0
                        ? <BoxShadow>[
                            BoxShadow(
                              color: ring.withValues(alpha: glow * 0.35),
                              blurRadius: 28 + glow * 16,
                              spreadRadius: 1,
                            ),
                          ]
                        : null,
                  ),
                  padding: const EdgeInsets.all(AppSpacing.sm + AppSpacing.xs),
                  child: child,
                );
              },
              child: Material(
                color: fill,
                shape: const CircleBorder(),
                clipBehavior: Clip.antiAlias,
                child: InkWell(
                  onTap: enabled ? widget.onPressed : null,
                  customBorder: const CircleBorder(),
                  child: Padding(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: <Widget>[
                        Icon(icon, size: 40, color: content),
                        const SizedBox(height: AppSpacing.sm),
                        FittedBox(
                          fit: BoxFit.scaleDown,
                          child: Text(
                            action.toUpperCase(),
                            style: context.text.titleMedium?.copyWith(
                              color: content,
                              letterSpacing: 1.6,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  static String _stateWord(EngineState state) => switch (state) {
        EngineState.disconnected => 'disconnected',
        EngineState.connecting => 'connecting',
        EngineState.connected => 'connected',
        EngineState.disconnecting => 'disconnecting',
        EngineState.error => 'in an error state',
      };
}
