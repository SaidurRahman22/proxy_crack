import 'package:flutter/foundation.dart';

/// Severity of a diversity finding.
///
/// ## `unknown` ranks ABOVE `high` — D20
///
/// This is the one thing about this enum that is not obvious and must not be
/// "tidied up" into alphabetical or intuitive order. A hop with no ASN data
/// is **not** a diverse hop; it is a hop nobody checked. An operator can act
/// on a known problem far more easily than on an unmeasured one, and showing
/// "unknown" below "high" would let a chain nobody looked up read as safer
/// than one with a measured, named weakness.
///
/// The server sorts by the same ladder (`proxies.diversity.SEVERITY_ORDER`)
/// and caps the score of an unchecked chain at 50 (D21), so the ordering
/// here is a mirror of a server rule, not a client opinion.
enum DiversitySeverity {
  critical('critical', 0, 'Critical'),
  unknown('unknown', 1, 'Unverified'),
  high('high', 2, 'High'),
  medium('medium', 3, 'Medium'),
  low('low', 4, 'Low'),
  info('info', 5, 'Info');

  const DiversitySeverity(this.wire, this.rank, this.label);

  final String wire;

  /// Lower sorts first. Matches `SEVERITY_ORDER` exactly.
  final int rank;

  final String label;

  static DiversitySeverity? fromWire(String? value) {
    if (value == null) return null;
    for (final severity in DiversitySeverity.values) {
      if (severity.wire == value) return severity;
    }
    return null;
  }

  /// True for the two bands that should stop a user before they connect.
  bool get isBlocking =>
      this == DiversitySeverity.critical || this == DiversitySeverity.unknown;
}

/// One named thing worth knowing about a chain.
@immutable
class DiversityFinding {
  const DiversityFinding({
    required this.code,
    required this.severity,
    required this.title,
    required this.message,
    this.positions = const <int>[],
    this.detail = const <String, dynamic>{},
  });

  /// Stable machine code, e.g. `entry_exit_shared_asn`.
  final String code;

  final DiversitySeverity severity;
  final String title;
  final String message;

  /// 0-based hop positions this finding is about.
  final List<int> positions;

  final Map<String, dynamic> detail;

  /// `Hops 1 and 3` — 1-based, because the UI numbers hops from 1.
  String get positionLabel {
    if (positions.isEmpty) return '';
    final human = positions.map((p) => p + 1).toList()..sort();
    if (human.length == 1) return 'Hop ${human.first}';
    final last = human.removeLast();
    return 'Hops ${human.join(', ')} and $last';
  }

  factory DiversityFinding.fromJson(Map<String, dynamic> json) =>
      DiversityFinding(
        code: json['code'] as String? ?? 'unknown',
        severity:
            DiversitySeverity.fromWire(json['severity'] as String?) ??
            DiversitySeverity.unknown,
        title: json['title'] as String? ?? 'Finding',
        message: json['message'] as String? ?? '',
        positions: (json['positions'] as List<dynamic>? ?? const <dynamic>[])
            .whereType<num>()
            .map((n) => n.toInt())
            .toList(growable: false),
        detail:
            (json['detail'] as Map<String, dynamic>?) ??
            const <String, dynamic>{},
      );
}

/// How much of the chain the analysis could actually see.
@immutable
class DiversityCoverage {
  const DiversityCoverage({
    required this.hops,
    required this.known,
    required this.unknown,
    required this.fraction,
    required this.scoreCeiling,
  });

  final int hops;
  final int known;
  final int unknown;

  /// 0.0–1.0.
  final double fraction;

  /// `50 + 50 × coverage` (D21). No number of clean findings lifts a chain
  /// nobody looked up above 50 — "unknown is not a pass" is structural here
  /// rather than advisory.
  final double scoreCeiling;

  bool get isComplete => unknown == 0 && hops > 0;

  factory DiversityCoverage.fromJson(Map<String, dynamic> json) =>
      DiversityCoverage(
        hops: (json['hops'] as num?)?.toInt() ?? 0,
        known: (json['known'] as num?)?.toInt() ?? 0,
        unknown: (json['unknown'] as num?)?.toInt() ?? 0,
        fraction: (json['fraction'] as num?)?.toDouble() ?? 0,
        scoreCeiling: (json['score_ceiling'] as num?)?.toDouble() ?? 50,
      );

  static const DiversityCoverage none = DiversityCoverage(
    hops: 0,
    known: 0,
    unknown: 0,
    fraction: 0,
    scoreCeiling: 50,
  );
}

/// One analysed position, joined to whatever intel is cached for it.
@immutable
class DiversityHop {
  const DiversityHop({
    required this.position,
    required this.intelKnown,
    required this.intelStatus,
    this.proxyId,
    this.provider,
    this.asn,
    this.asnOrg,
    this.country,
    this.network,
  });

  final int position;

  /// False when nothing is cached for this host. The UI must say
  /// "unchecked", never leave it looking like a clean result.
  final bool intelKnown;

  /// Why, when [intelKnown] is false — e.g. `no_record`, `lookup_failed`.
  final String intelStatus;

  final int? proxyId;

  /// Pseudonymised to "Vendor A"/"Vendor B" before it leaves the server
  /// (D23). The user learns that two hops came from one vendor without the
  /// API publishing the operator's supply chain to every subscriber —
  /// which, given P4, would be our own API handing out evidence of the thing
  /// the provider's terms forbid.
  final String? provider;

  final int? asn;
  final String? asnOrg;
  final String? country;

  /// `block` when the announced prefix was coarser than a /24 (v4) or /48
  /// (v6), otherwise the prefix itself.
  final String? network;

  factory DiversityHop.fromJson(Map<String, dynamic> json) => DiversityHop(
    position: (json['position'] as num?)?.toInt() ?? 0,
    intelKnown: json['intel_known'] as bool? ?? false,
    intelStatus: json['intel_status'] as String? ?? 'unknown',
    proxyId: (json['proxy'] as num?)?.toInt(),
    provider: json['provider'] as String?,
    asn: (json['asn'] as num?)?.toInt(),
    asnOrg: json['asn_org'] as String?,
    country: json['country'] as String?,
    network: json['block'] as String? ?? json['prefix'] as String?,
  );
}

/// Why a `GET /api/chains/{id}/diversity/` could not resolve the chain.
///
/// The request still succeeds: fixed hops are analysed and unresolved
/// positions are reported as unknown. A 409 would be the wrong answer to
/// "warn me about this chain" — `POST /resolve/` is where a client finds out
/// that it cannot connect.
@immutable
class DiversityResolution {
  const DiversityResolution({required this.resolved, this.errorMessage});

  final bool resolved;
  final String? errorMessage;

  static const DiversityResolution ok = DiversityResolution(resolved: true);

  factory DiversityResolution.fromJson(Map<String, dynamic> json) {
    final error = json['error'];
    String? message;
    if (error is Map<String, dynamic>) {
      message = error['message'] as String?;
    } else if (error is String) {
      message = error;
    }
    return DiversityResolution(
      resolved: json['resolved'] as bool? ?? false,
      errorMessage: message,
    );
  }
}

/// The full R2 report for a chain.
@immutable
class ChainDiversityReport {
  const ChainDiversityReport({
    required this.score,
    required this.summary,
    required this.vocabularyNote,
    required this.coverage,
    required this.findings,
    required this.hops,
    required this.counts,
    this.worstSeverity,
    this.analysedAt,
    this.resolution = DiversityResolution.ok,
  });

  /// 0–100, **derived from the findings** (D24), so it can never disagree
  /// with the list shown beneath it.
  final double score;

  /// One sentence written for a user, not an operator.
  final String summary;

  /// `VOCABULARY_NOTE`. The server ships it with every analysis so a client
  /// cannot render a score without it, and the app honours that: this string
  /// is displayed verbatim, never paraphrased, and the UI never describes
  /// any of this as anonymity. Chaining commercial proxies bought under one
  /// account with one payment trail is **not** Tor.
  final String vocabularyNote;

  final DiversityCoverage coverage;

  /// Already in server order; [sortedFindings] re-asserts it client-side.
  final List<DiversityFinding> findings;

  final List<DiversityHop> hops;

  /// Distinct values actually observed: `asns`, `asn_orgs`, `providers`,
  /// `countries`, `networks`. Unknowns are excluded rather than counted as
  /// one more distinct value.
  final Map<String, int> counts;

  final DiversitySeverity? worstSeverity;
  final DateTime? analysedAt;
  final DiversityResolution resolution;

  /// Findings worst-first under the D20 ladder, with `unknown` above `high`.
  List<DiversityFinding> get sortedFindings {
    final sorted = List<DiversityFinding>.of(findings);
    sorted.sort((a, b) {
      final byRank = a.severity.rank.compareTo(b.severity.rank);
      return byRank != 0 ? byRank : a.code.compareTo(b.code);
    });
    return sorted;
  }

  /// Findings grouped by severity, in ladder order, skipping empty bands.
  List<MapEntry<DiversitySeverity, List<DiversityFinding>>> get bySeverity {
    final grouped = <DiversitySeverity, List<DiversityFinding>>{};
    for (final finding in sortedFindings) {
      grouped.putIfAbsent(finding.severity, () => <DiversityFinding>[]).add(
        finding,
      );
    }
    final ordered = grouped.keys.toList()
      ..sort((a, b) => a.rank.compareTo(b.rank));
    return <MapEntry<DiversitySeverity, List<DiversityFinding>>>[
      for (final severity in ordered)
        MapEntry<DiversitySeverity, List<DiversityFinding>>(
          severity,
          grouped[severity]!,
        ),
    ];
  }

  /// True when the score is held down by missing data rather than by a
  /// measured problem — a very different message to the user.
  bool get isLimitedByCoverage =>
      coverage.unknown > 0 && score >= coverage.scoreCeiling - 0.05;

  factory ChainDiversityReport.fromJson(Map<String, dynamic> json) =>
      ChainDiversityReport(
        score: (json['score'] as num?)?.toDouble() ?? 0,
        summary: json['summary'] as String? ?? '',
        vocabularyNote: json['vocabulary_note'] as String? ?? '',
        coverage: json['coverage'] is Map<String, dynamic>
            ? DiversityCoverage.fromJson(
                json['coverage'] as Map<String, dynamic>,
              )
            : DiversityCoverage.none,
        findings: (json['findings'] as List<dynamic>? ?? const <dynamic>[])
            .whereType<Map<String, dynamic>>()
            .map(DiversityFinding.fromJson)
            .toList(growable: false),
        hops: (json['hops'] as List<dynamic>? ?? const <dynamic>[])
            .whereType<Map<String, dynamic>>()
            .map(DiversityHop.fromJson)
            .toList(growable: false),
        counts: <String, int>{
          for (final entry
              in ((json['counts'] as Map<String, dynamic>?) ??
                      const <String, dynamic>{})
                  .entries)
            if (entry.value is num) entry.key: (entry.value as num).toInt(),
        },
        worstSeverity: DiversitySeverity.fromWire(
          json['worst_severity'] as String?,
        ),
        analysedAt: DateTime.tryParse(json['analysed_at'] as String? ?? ''),
        resolution: json['resolution'] is Map<String, dynamic>
            ? DiversityResolution.fromJson(
                json['resolution'] as Map<String, dynamic>,
              )
            : DiversityResolution.ok,
      );
}
