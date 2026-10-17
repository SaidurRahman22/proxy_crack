/// Turns whatever the operator typed into `PROXY.country` into the
/// two-letter chip the list renders.
///
/// The backend column is deliberately free text (`CharField(max_length=64)`)
/// — the CSV importer writes whatever the provider's export contained, which
/// in practice is sometimes `NL`, sometimes `Netherlands`, and for the one
/// real proxy in the dev database, an empty string. There is no
/// `country_code` field on the wire, so the client derives one.
///
/// The design system bans emoji as icons, so this feeds a text chip rather
/// than a flag.
abstract final class CountryCode {
  /// Shown when the operator recorded nothing. Not "XX" or a guess: an
  /// unknown location is a real state and the UI should say so.
  static const String unknown = '--';

  static const Map<String, String> _byName = <String, String>{
    'netherlands': 'NL',
    'holland': 'NL',
    'germany': 'DE',
    'deutschland': 'DE',
    'france': 'FR',
    'united kingdom': 'GB',
    'great britain': 'GB',
    'england': 'GB',
    'uk': 'GB',
    'sweden': 'SE',
    'switzerland': 'CH',
    'iceland': 'IS',
    'romania': 'RO',
    'united states': 'US',
    'united states of america': 'US',
    'usa': 'US',
    'america': 'US',
    'canada': 'CA',
    'brazil': 'BR',
    'singapore': 'SG',
    'japan': 'JP',
    'south korea': 'KR',
    'korea': 'KR',
    'india': 'IN',
    'australia': 'AU',
    'south africa': 'ZA',
    'united arab emirates': 'AE',
    'uae': 'AE',
    'turkey': 'TR',
    'türkiye': 'TR',
    'poland': 'PL',
    'spain': 'ES',
    'italy': 'IT',
    'portugal': 'PT',
    'norway': 'NO',
    'finland': 'FI',
    'denmark': 'DK',
    'ireland': 'IE',
    'austria': 'AT',
    'belgium': 'BE',
    'czechia': 'CZ',
    'czech republic': 'CZ',
    'hungary': 'HU',
    'bulgaria': 'BG',
    'greece': 'GR',
    'ukraine': 'UA',
    'russia': 'RU',
    'mexico': 'MX',
    'argentina': 'AR',
    'chile': 'CL',
    'colombia': 'CO',
    'hong kong': 'HK',
    'taiwan': 'TW',
    'thailand': 'TH',
    'vietnam': 'VN',
    'malaysia': 'MY',
    'indonesia': 'ID',
    'philippines': 'PH',
    'new zealand': 'NZ',
    'israel': 'IL',
    'egypt': 'EG',
    'nigeria': 'NG',
    'kenya': 'KE',
    'luxembourg': 'LU',
    'estonia': 'EE',
    'latvia': 'LV',
    'lithuania': 'LT',
    'moldova': 'MD',
    'serbia': 'RS',
    'croatia': 'HR',
    'slovakia': 'SK',
    'slovenia': 'SI',
  };

  /// `'NL'`, `'Netherlands'` and `'netherlands'` all become `NL`.
  /// Anything unrecognised and longer than two characters is truncated
  /// rather than dropped — `'Kaza'` reading as `KA` is still more useful in
  /// a 44 px chip than nothing, and the full name is on the row beside it.
  static String fromCountry(String? country) {
    final raw = country?.trim() ?? '';
    if (raw.isEmpty) return unknown;
    if (raw.length == 2) return raw.toUpperCase();
    final mapped = _byName[raw.toLowerCase()];
    if (mapped != null) return mapped;
    return raw.substring(0, 2).toUpperCase();
  }
}
