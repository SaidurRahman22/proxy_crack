import 'package:flutter/material.dart';

import '../../../core/theme/app_dimens.dart';
import '../../../core/theme/app_theme.dart';
import '../../../data/async_value.dart';
import '../../../data/models/chain_diversity.dart';
import '../../../shared/widgets/state_views.dart';
import '../../../shared/widgets/surface.dart';

/// R2 — "is this actually a chain?"
///
/// A three-hop chain whose hops share an ASN, a datacentre or a vendor gives
/// almost none of the separation the user thinks they bought, and nothing
/// else in the product would tell them.
///
/// ## Two rules this widget exists to honour
///
/// 1. **`unknown` outranks `high`** (D20). The ladder comes from
///    [DiversitySeverity.rank], which mirrors the server's `SEVERITY_ORDER`
///    exactly. A hop nobody checked is not a safe hop, so it is never shown
///    below a measured-but-lesser problem, never coloured like a pass, and
///    the coverage ceiling (D21) is stated rather than hidden.
/// 2. **Never imply anonymity.** The payload ships a `vocabulary_note` so a
///    client cannot render a score without it. It is displayed verbatim.
///    Nothing here says "anonymous", "private" or "untraceable"; the honest
///    description is layered egress routing, and this panel keeps to it.
class DiversityPanel extends StatelessWidget {
  const DiversityPanel({
    super.key,
    required this.diversity,
    required this.onRetry,
  });

  final AsyncValue<ChainDiversityReport>? diversity;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final value = diversity;
    if (value == null) return const SizedBox.shrink();

    return value.map(
      loading: () => const _DiversitySkeleton(),
      error: (message, detail, failure) => Surface(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: FailureInline(
          message: message,
          detail: detail,
          failure: failure,
          onRetry: onRetry,
        ),
      ),
      data: (report) => _DiversityReportView(report: report),
    );
  }
}

class _DiversityReportView extends StatelessWidget {
  const _DiversityReportView({required this.report});

  final ChainDiversityReport report;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final worst = report.worstSeverity;
    final accent = _severityColor(context, worst);

    return Surface(
      padding: const EdgeInsets.all(AppSpacing.md),
      borderColor: worst != null && worst.isBlocking
          ? accent.withValues(alpha: 0.38)
          : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Icon(_severityIcon(worst), size: 20, color: accent),
              const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      'Chain separation',
                      style: context.text.titleSmall,
                    ),
                    const SizedBox(height: 2),
                    Text(report.summary, style: context.text.bodySmall),
                  ],
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              _ScoreDial(score: report.score, color: accent),
            ],
          ),

          // The coverage ceiling, stated plainly. D21 makes "unknown is not
          // a pass" structural: no number of clean findings lifts a chain
          // nobody looked up above 50, and the user is told why rather than
          // being left to wonder why a clean-looking chain scores half.
          if (report.coverage.unknown > 0) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            Container(
              padding: const EdgeInsets.all(AppSpacing.sm + AppSpacing.xs),
              decoration: BoxDecoration(
                color: tokens.mutedForeground.withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(AppRadius.control),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Icon(
                    Icons.help_outline,
                    size: 16,
                    color: tokens.mutedForeground,
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: Text(
                      '${report.coverage.unknown} of '
                      '${report.coverage.hops} hops could not be checked, so '
                      'this chain cannot score above '
                      '${report.coverage.scoreCeiling.round()} however clean '
                      'the rest looks.',
                      style: context.text.bodySmall,
                    ),
                  ),
                ],
              ),
            ),
          ],

          if (report.findings.isNotEmpty) ...<Widget>[
            const SizedBox(height: AppSpacing.md),
            for (final group in report.bySeverity)
              for (final finding in group.value)
                Padding(
                  key: ValueKey<String>(
                    'finding-${finding.code}-${finding.positions.join('-')}',
                  ),
                  padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                  child: _FindingRow(finding: finding),
                ),
          ],

          if (report.resolution.errorMessage != null) ...<Widget>[
            const SizedBox(height: AppSpacing.sm),
            Text(
              'Some hops could not be resolved, so they were analysed as '
              'unknown: ${report.resolution.errorMessage}',
              style: context.text.bodySmall,
            ),
          ],

          const SizedBox(height: AppSpacing.md),
          const Divider(height: 1),
          const SizedBox(height: AppSpacing.sm + AppSpacing.xs),
          // Served with every analysis so a client cannot render a score
          // without it. Shown verbatim.
          Text(
            report.vocabularyNote,
            style: context.text.bodySmall?.copyWith(
              color: tokens.mutedForeground,
            ),
          ),
        ],
      ),
    );
  }
}

class _FindingRow extends StatelessWidget {
  const _FindingRow({required this.finding});

  final DiversityFinding finding;

  @override
  Widget build(BuildContext context) {
    final color = _severityColor(context, finding.severity);
    final positions = finding.positionLabel;

    return Semantics(
      label:
          '${finding.severity.label}. ${finding.title}. '
          '${positions.isEmpty ? '' : '$positions. '}${finding.message}',
      excludeSemantics: true,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          // Shape as well as colour: a 3 px bar plus the severity word, so
          // the signal survives colour-blindness and a greyscale screenshot.
          Container(
            width: 3,
            height: 34,
            margin: const EdgeInsets.only(top: 2),
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: AppSpacing.sm + AppSpacing.xs),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Text(
                      finding.severity.label.toUpperCase(),
                      style: context.text.labelMedium?.copyWith(color: color),
                    ),
                    if (positions.isNotEmpty) ...<Widget>[
                      Text(
                        '  ·  $positions',
                        style: context.text.labelMedium?.copyWith(
                          color: context.tokens.mutedForeground,
                        ),
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 1),
                Text(finding.title, style: context.text.titleSmall),
                const SizedBox(height: 1),
                Text(finding.message, style: context.text.bodySmall),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 0–100 with the worst severity's colour. Not a progress bar: the number
/// is a judgement, not a completion.
class _ScoreDial extends StatelessWidget {
  const _ScoreDial({required this.score, required this.color});

  final double score;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Separation score ${score.round()} out of 100',
      excludeSemantics: true,
      child: Container(
        width: 52,
        height: 44,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(AppRadius.control),
          border: Border.all(color: color.withValues(alpha: 0.32)),
        ),
        child: Text(
          '${score.round()}',
          style: context.text.titleMedium?.copyWith(
            color: color,
            fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
          ),
        ),
      ),
    );
  }
}

class _DiversitySkeleton extends StatelessWidget {
  const _DiversitySkeleton();

  @override
  Widget build(BuildContext context) {
    return Surface(
      padding: const EdgeInsets.all(AppSpacing.md),
      child: Semantics(
        label: 'Checking chain separation',
        liveRegion: true,
        child: Row(
          children: const <Widget>[
            SkeletonBox(width: 20, height: 20),
            SizedBox(width: AppSpacing.sm + AppSpacing.xs),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  SkeletonBox(width: 150, height: 14),
                  SizedBox(height: AppSpacing.sm),
                  SkeletonBox(width: 220, height: 12),
                ],
              ),
            ),
            SizedBox(width: AppSpacing.sm),
            SkeletonBox(width: 52, height: 44),
          ],
        ),
      ),
    );
  }
}

/// Severity → token colour.
///
/// `unknown` is deliberately the muted foreground rather than anything that
/// reads as a pass or as a graded warning: it is the absence of information,
/// and it must not look like either end of the scale.
Color _severityColor(BuildContext context, DiversitySeverity? severity) {
  final tokens = context.tokens;
  return switch (severity) {
    DiversitySeverity.critical => tokens.destructiveText,
    DiversitySeverity.unknown => tokens.mutedForeground,
    DiversitySeverity.high => tokens.warning,
    DiversitySeverity.medium => tokens.warning,
    DiversitySeverity.low => tokens.subtleForeground,
    DiversitySeverity.info => tokens.subtleForeground,
    null => tokens.accent,
  };
}

IconData _severityIcon(DiversitySeverity? severity) => switch (severity) {
  DiversitySeverity.critical => Icons.dangerous_outlined,
  DiversitySeverity.unknown => Icons.help_outline,
  DiversitySeverity.high || DiversitySeverity.medium =>
    Icons.warning_amber_outlined,
  DiversitySeverity.low || DiversitySeverity.info => Icons.info_outline,
  null => Icons.verified_outlined,
};
