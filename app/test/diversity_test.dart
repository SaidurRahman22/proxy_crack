import 'package:flutter_test/flutter_test.dart';
import 'package:proxy_crack/data/models/chain_diversity.dart';

void main() {
  group('severity ladder (D20)', () {
    test('unknown ranks ABOVE high', () {
      // The single most important ordering rule in R2. A hop with no ASN
      // data is not a diverse hop — it is a hop nobody checked, and an
      // operator can act on a known problem far more easily than on an
      // unmeasured one. Mirrors `proxies.diversity.SEVERITY_ORDER`.
      expect(
        DiversitySeverity.unknown.rank,
        lessThan(DiversitySeverity.high.rank),
      );
      expect(
        DiversitySeverity.critical.rank,
        lessThan(DiversitySeverity.unknown.rank),
      );
    });

    test('the full ladder matches the server, value for value', () {
      expect(
        <DiversitySeverity, int>{
          for (final s in DiversitySeverity.values) s: s.rank,
        },
        <DiversitySeverity, int>{
          DiversitySeverity.critical: 0,
          DiversitySeverity.unknown: 1,
          DiversitySeverity.high: 2,
          DiversitySeverity.medium: 3,
          DiversitySeverity.low: 4,
          DiversitySeverity.info: 5,
        },
      );
    });

    test('critical and unknown both block; the rest do not', () {
      expect(DiversitySeverity.critical.isBlocking, isTrue);
      expect(DiversitySeverity.unknown.isBlocking, isTrue);
      expect(DiversitySeverity.high.isBlocking, isFalse);
    });
  });

  group('report parsing and ordering', () {
    final json = <String, dynamic>{
      'score': 41.0,
      'worst_severity': 'unknown',
      'summary':
          'Some hops could not be checked, so this chain\'s diversity is '
          'unverified rather than confirmed.',
      'vocabulary_note':
          'This measures correlation resistance between the hops you chose '
          '-- whether they share a network, an operator or a jurisdiction. '
          'It is not anonymity.',
      'coverage': <String, dynamic>{
        'hops': 3,
        'known': 2,
        'unknown': 1,
        'fraction': 0.667,
        'score_ceiling': 83.3,
      },
      'counts': <String, dynamic>{
        'asns': 2,
        'asn_orgs': 1,
        'providers': 1,
        'countries': 2,
        'networks': 2,
      },
      'findings': <Map<String, dynamic>>[
        <String, dynamic>{
          'code': 'shared_asn_org',
          'severity': 'medium',
          'title': 'Two hops share an operator',
          'message': 'Hops 1 and 2 are run by the same company.',
          'positions': <int>[0, 1],
          'detail': <String, dynamic>{'asn_org': 'Example Transit'},
        },
        <String, dynamic>{
          'code': 'hop_intel_unknown',
          'severity': 'unknown',
          'title': 'A hop could not be checked',
          'message': 'No network record is cached for hop 3.',
          'positions': <int>[2],
        },
        <String, dynamic>{
          'code': 'shared_provider',
          'severity': 'high',
          'title': 'Two hops came from one vendor',
          'message': 'Hops 1 and 2 were bought from Vendor A.',
          'positions': <int>[0, 1],
        },
      ],
      'hops': <Map<String, dynamic>>[
        <String, dynamic>{
          'position': 0,
          'proxy': 11,
          'provider': 'Vendor A',
          'asn': 64500,
          'asn_org': 'Example Transit',
          'country': 'NL',
          'intel_known': true,
          'intel_status': 'ok',
          'block': '198.51.100.0/24',
        },
        <String, dynamic>{
          'position': 1,
          'proxy': 12,
          'provider': 'Vendor A',
          'asn': 64501,
          'asn_org': 'Example Transit',
          'country': 'DE',
          'intel_known': true,
          'intel_status': 'ok',
          'prefix': '203.0.113.0/22',
        },
        <String, dynamic>{
          'position': 2,
          'proxy': 13,
          'provider': null,
          'asn': null,
          'country': null,
          'intel_known': false,
          'intel_status': 'no_record',
        },
      ],
      'resolution': <String, dynamic>{'resolved': true, 'error': null},
      'analysed_at': '2026-10-17T10:00:00Z',
    };

    test('findings sort with unknown above high, whatever the wire order',
        () {
      final report = ChainDiversityReport.fromJson(json);
      expect(
        report.sortedFindings.map((f) => f.severity),
        <DiversitySeverity>[
          DiversitySeverity.unknown,
          DiversitySeverity.high,
          DiversitySeverity.medium,
        ],
      );
    });

    test('grouping preserves ladder order and skips empty bands', () {
      final report = ChainDiversityReport.fromJson(json);
      expect(report.bySeverity.map((e) => e.key), <DiversitySeverity>[
        DiversitySeverity.unknown,
        DiversitySeverity.high,
        DiversitySeverity.medium,
      ]);
      expect(report.bySeverity.first.value, hasLength(1));
    });

    test('the coverage ceiling is carried, not recomputed', () {
      final report = ChainDiversityReport.fromJson(json);
      expect(report.coverage.unknown, 1);
      expect(report.coverage.scoreCeiling, 83.3);
      expect(report.coverage.isComplete, isFalse);
    });

    test('the vocabulary note survives parsing verbatim', () {
      // The server ships it so a client cannot render a score without it.
      // If it ever arrives empty, the UI has nothing to show and the claim
      // would quietly become stronger than it should be.
      final report = ChainDiversityReport.fromJson(json);
      expect(report.vocabularyNote, json['vocabulary_note']);
      expect(report.vocabularyNote, contains('not anonymity'));
    });

    test('hop positions read 1-based for humans', () {
      final report = ChainDiversityReport.fromJson(json);
      final shared = report.findings.firstWhere(
        (f) => f.code == 'shared_asn_org',
      );
      expect(shared.positionLabel, 'Hops 1 and 2');
      final unknown = report.findings.firstWhere(
        (f) => f.code == 'hop_intel_unknown',
      );
      expect(unknown.positionLabel, 'Hop 3');
    });

    test('an unchecked hop reports as unknown, not as clean', () {
      final report = ChainDiversityReport.fromJson(json);
      final last = report.hops.last;
      expect(last.intelKnown, isFalse);
      expect(last.intelStatus, 'no_record');
      expect(last.asn, isNull);
      expect(report.worstSeverity, DiversitySeverity.unknown);
    });

    test('a failed resolution is reported, not swallowed', () {
      final failed = Map<String, dynamic>.of(json)
        ..['resolution'] = <String, dynamic>{
          'resolved': false,
          'error': <String, dynamic>{
            'message': 'No healthy proxy satisfies hop 2.',
            'code': 'hop_unsatisfied',
            'position': 1,
          },
        };
      final report = ChainDiversityReport.fromJson(failed);
      expect(report.resolution.resolved, isFalse);
      expect(report.resolution.errorMessage, contains('hop 2'));
    });

    test('an unrecognised severity degrades to unknown, never to info', () {
      // A future server band must not be rendered as the mildest thing on
      // the ladder — that is the one failure mode that makes a chain look
      // safer than it is.
      final report = ChainDiversityReport.fromJson(<String, dynamic>{
        'findings': <Map<String, dynamic>>[
          <String, dynamic>{
            'code': 'future_band',
            'severity': 'catastrophic',
            'title': 'T',
            'message': 'M',
          },
        ],
      });
      expect(report.findings.single.severity, DiversitySeverity.unknown);
    });
  });
}
