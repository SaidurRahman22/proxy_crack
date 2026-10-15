/// Display formatting. Pure functions, no widgets, easy to unit test.
abstract final class Format {
  static const int _kb = 1024;
  static const int _mb = 1024 * 1024;
  static const int _gb = 1024 * 1024 * 1024;

  /// `1.4 GB`, `812 MB`, `96 KB`, `0 B`
  static String bytes(int value) {
    if (value >= _gb) return '${(value / _gb).toStringAsFixed(2)} GB';
    if (value >= _mb) return '${(value / _mb).toStringAsFixed(1)} MB';
    if (value >= _kb) return '${(value / _kb).round()} KB';
    return '$value B';
  }

  /// `4.2 MB/s`
  static String rate(double bytesPerSecond) {
    if (bytesPerSecond >= _mb) {
      return '${(bytesPerSecond / _mb).toStringAsFixed(1)} MB/s';
    }
    if (bytesPerSecond >= _kb) {
      return '${(bytesPerSecond / _kb).round()} KB/s';
    }
    return '${bytesPerSecond.round()} B/s';
  }

  /// `01:24:07` or `04:31`
  static String duration(Duration d) {
    final h = d.inHours;
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return h > 0 ? '${h.toString().padLeft(2, '0')}:$m:$s' : '$m:$s';
  }

  /// `412 ms` / `1.3 s`
  static String latency(int ms) =>
      ms >= 1000 ? '${(ms / 1000).toStringAsFixed(1)} s' : '$ms ms';

  /// `2 min ago`, `3 h ago`, `yesterday`
  static String relativeTime(DateTime when) {
    final diff = DateTime.now().difference(when);
    if (diff.inMinutes < 1) return 'just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes} min ago';
    if (diff.inHours < 24) return '${diff.inHours} h ago';
    if (diff.inDays == 1) return 'yesterday';
    return '${diff.inDays} d ago';
  }
}
