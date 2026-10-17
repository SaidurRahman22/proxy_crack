package com.nenobot.proxy_crack

import android.os.Handler
import android.os.Looper
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The single source of truth for tunnel state, owned by the process rather
 * than by the Activity.
 *
 * engine-decision.md §4.2 rule 4: *"State is authoritative in L4 and replayed
 * on subscribe."* An `EventChannel` stream only exists while Dart is
 * listening, and the Activity is destroyed and recreated on rotation or when
 * the user returns from the launcher — but the service and the tunnel outlive
 * both. Without a replay on `onListen`, the UI would show "Disconnected" over
 * a live VPN every time the user came back to the app.
 *
 * Every field here is deliberately primitive: this crosses a platform channel
 * and must survive being turned into a `Map`.
 */
object VpnStateBus {

    const val STATE_DISCONNECTED = "disconnected"
    const val STATE_CONNECTING = "connecting"
    const val STATE_CONNECTED = "connected"
    const val STATE_DISCONNECTING = "disconnecting"
    const val STATE_ERROR = "error"

    // These strings are the wire form of Dart's `EngineFailure`. Keep them
    // in step with `proxy_engine.dart`; anything unrecognised degrades to
    // `unknown` on the Dart side rather than throwing.
    const val FAILURE_PERMISSION_DENIED = "permissionDenied"
    const val FAILURE_HOP_UNREACHABLE = "hopUnreachable"
    const val FAILURE_AUTH = "authenticationFailed"
    const val FAILURE_NETWORK = "networkUnavailable"
    const val FAILURE_UNKNOWN = "unknown"

    data class Snapshot(
        val state: String,
        val detail: String?,
        val progress: Double?,
        val failure: String?,
        val connectedAtMillis: Long?
    ) {
        fun toMap(): Map<String, Any?> = mapOf(
            "state" to state,
            "detail" to detail,
            "progress" to progress,
            "failure" to failure,
            "connectedAtMillis" to connectedAtMillis
        )
    }

    private val main = Handler(Looper.getMainLooper())

    private val statusListeners = CopyOnWriteArrayList<(Map<String, Any?>) -> Unit>()
    private val statsListeners = CopyOnWriteArrayList<(Map<String, Any?>) -> Unit>()

    @Volatile
    private var snapshot = Snapshot(STATE_DISCONNECTED, null, null, null, null)

    @Volatile
    private var stats: Map<String, Any?> = zeroStats()

    fun current(): Map<String, Any?> = snapshot.toMap()

    fun currentStats(): Map<String, Any?> = stats

    fun isActive(): Boolean =
        snapshot.state != STATE_DISCONNECTED && snapshot.state != STATE_ERROR

    fun publish(
        state: String,
        detail: String? = null,
        progress: Double? = null,
        failure: String? = null,
        connectedAtMillis: Long? = null
    ) {
        val next = Snapshot(state, detail, progress, failure, connectedAtMillis)
        snapshot = next
        val payload = next.toMap()
        // Platform-channel sinks must be fed from the main thread. The
        // engine publishes from Go threads and the watchdog from a handler,
        // so normalise here rather than at every call site.
        main.post { statusListeners.forEach { it(payload) } }
    }

    fun publishStats(
        bytesDown: Long,
        bytesUp: Long,
        downBytesPerSecond: Double,
        upBytesPerSecond: Double,
        sessionSeconds: Long
    ) {
        val payload = mapOf(
            "bytesDown" to bytesDown,
            "bytesUp" to bytesUp,
            "downBytesPerSecond" to downBytesPerSecond,
            "upBytesPerSecond" to upBytesPerSecond,
            "sessionSeconds" to sessionSeconds
        )
        stats = payload
        main.post { statsListeners.forEach { it(payload) } }
    }

    fun resetStats() {
        stats = zeroStats()
        val payload = stats
        main.post { statsListeners.forEach { it(payload) } }
    }

    fun addStatusListener(listener: (Map<String, Any?>) -> Unit) {
        statusListeners.add(listener)
        // Replay. This is the whole point of the class.
        listener(snapshot.toMap())
    }

    fun removeStatusListener(listener: (Map<String, Any?>) -> Unit) {
        statusListeners.remove(listener)
    }

    fun addStatsListener(listener: (Map<String, Any?>) -> Unit) {
        statsListeners.add(listener)
        listener(stats)
    }

    fun removeStatsListener(listener: (Map<String, Any?>) -> Unit) {
        statsListeners.remove(listener)
    }

    private fun zeroStats(): Map<String, Any?> = mapOf(
        "bytesDown" to 0L,
        "bytesUp" to 0L,
        "downBytesPerSecond" to 0.0,
        "upBytesPerSecond" to 0.0,
        "sessionSeconds" to 0L
    )
}
