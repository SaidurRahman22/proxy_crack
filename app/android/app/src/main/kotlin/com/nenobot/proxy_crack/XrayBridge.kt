package com.nenobot.proxy_crack

import android.util.Log
import libXray.DialerController
import libXray.LibXray
import libXray.ProcessFinder
import org.json.JSONObject

/**
 * The only place in the app that touches libXray.
 *
 * Everything in libXray v26.10.10 goes through one entry point:
 *
 * ```
 * LibXray.invoke("""{"apiVersion":3,"method":"runXray","payload":{"xrayJson":"..."}}""")
 *   -> """{"success":true,"data":{},"error":""}"""
 * ```
 *
 * Verified against `invoke_model.go` at the shipped revision, not against
 * documentation: `LibXrayInvokeRequest{APIVersion,Method,Payload}` with JSON
 * tags `apiVersion` / `method` / `payload`, method names in lowerCamelCase.
 */
object XrayBridge {

    private const val TAG = "XrayBridge"
    private const val API_VERSION = 3

    private const val METHOD_RUN = "runXray"
    private const val METHOD_STOP = "stopXray"
    private const val METHOD_STATE = "getXrayState"
    private const val METHOD_TEST = "testXray"
    private const val METHOD_VERSION = "xrayVersion"

    /**
     * Called by the engine, on a Go thread, immediately before each of its
     * own sockets connects. Must hand the descriptor to
     * `VpnService.protect()` so the engine's uplink does not get routed back
     * into the interface it is serving.
     *
     * Null while no tunnel is up, and the controller then answers `false`,
     * which libXray turns into a dial error. That is the correct failure:
     * an unprotected socket inside a VPN is either an infinite loop or a
     * leak, and refusing to dial is better than either.
     */
    @Volatile
    var protector: ((Int) -> Boolean)? = null

    @Volatile
    private var controllerRegistered = false

    private val controller = DialerController { fd ->
        protector?.invoke(fd.toInt()) ?: false
    }

    /**
     * Registers the protect hook exactly once per process.
     *
     * This is not an optimisation. Xray-core's
     * `transport/internet/system_dialer.go` does
     * `Controllers = append(Controllers, ctl)` — registration **appends to a
     * global slice and nothing ever removes an entry**. Calling it on every
     * connect would stack one more controller per connect, each invoked on
     * every dial, for the life of the process.
     */
    fun ensureControllerRegistered() {
        if (controllerRegistered) return
        synchronized(this) {
            if (controllerRegistered) return
            LibXray.registerDialerController(controller)
            LibXray.registerListenerController(controller)
            controllerRegistered = true
        }
    }

    // --- per-app routing (Android only) ------------------------------------

    /**
     * Answers "which UID owns this connection?" for the `process` routing
     * rule. Null while no tunnel is up.
     *
     * Set by the service, because the only way to answer is
     * `ConnectivityManager.getConnectionOwnerUid`, and the platform grants
     * that to the **active `VpnService`** — not to arbitrary callers.
     */
    @Volatile
    var uidLookup: ((String, String, Int, String, Int) -> Int)? = null

    @Volatile
    private var processFinderRegistered = false

    /**
     * Called by the engine on a Go thread, once per new connection, while a
     * `process` routing rule exists.
     *
     * Returns `-1` when we cannot tell, which libXray passes through as a
     * UID that matches no rule — so an unanswerable lookup means "route it
     * like everything else", never "send it outside the tunnel".
     *
     * Must not throw: this crosses a cgo boundary and an exception here
     * takes the engine with it.
     */
    private val processFinder = object : ProcessFinder {
        override fun findProcessByConnection(
            network: String,
            srcIP: String,
            srcPort: Long,
            destIP: String,
            destPort: Long
        ): Long {
            return try {
                val uid = uidLookup?.invoke(
                    network,
                    srcIP,
                    srcPort.toInt(),
                    destIP,
                    destPort.toInt()
                ) ?: -1
                uid.toLong()
            } catch (t: Throwable) {
                Log.w(TAG, "process lookup failed", t)
                -1L
            }
        }
    }

    /**
     * Registers the per-app routing hook once per process.
     *
     * [sdkInt] is `Build.VERSION.SDK_INT`. libXray uses it to decide
     * whether to read the `/proc/net` tables itself (below API 30) or to call this
     * finder (API 30 and up), so passing the real value is not optional.
     *
     * Unlike `registerDialerController`, libXray *assigns* rather than
     * appends here, so re-registering would be harmless — it is still done
     * once, for symmetry and to keep the two hooks reasoned about the same
     * way.
     */
    fun ensureProcessFinderRegistered(sdkInt: Int) {
        if (processFinderRegistered) return
        synchronized(this) {
            if (processFinderRegistered) return
            try {
                LibXray.registerProcessFinder(processFinder, sdkInt.toLong())
                processFinderRegistered = true
            } catch (t: Throwable) {
                // Per-app routing is unavailable; the config's process rule
                // then matches nothing. Worth a log, not worth refusing a
                // tunnel over.
                Log.w(TAG, "registerProcessFinder failed", t)
            }
        }
    }

    /**
     * Points the Go runtime's own resolver at a protected socket.
     *
     * Android can expose a loopback DNS server while a VPN is active, which
     * the Go resolver cannot reach. This affects how the *engine* resolves
     * names (a hop given as a hostname), not how proxied app traffic
     * resolves — that is handled by the config's `dns` block. Best effort:
     * a failure here is not a reason to refuse a tunnel whose hops are all
     * IP addresses, which is the normal case.
     */
    fun setProcessDns(server: String) {
        try {
            LibXray.setDNS(controller, server)
        } catch (t: Throwable) {
            Log.w(TAG, "setDNS failed, continuing with the default resolver", t)
        }
    }

    fun resetProcessDns() {
        try {
            LibXray.resetDNS()
        } catch (t: Throwable) {
            Log.w(TAG, "resetDNS failed", t)
        }
    }

    /** Returns null on success, or the engine's own error message. */
    fun runXray(configJson: String): String? =
        invoke(METHOD_RUN, JSONObject().put("xrayJson", configJson)).error

    /** Returns null on success, or the engine's own error message. */
    fun stopXray(): String? = invoke(METHOD_STOP, null).error

    /**
     * Builds an instance from [configJson] and closes it without starting.
     *
     * Used to check a generated config against the real parser without
     * establishing a TUN. Returns null when the engine accepted it.
     * libXray refuses this while an instance is running, so it is only
     * meaningful from a disconnected state.
     */
    fun testXray(configJson: String): String? =
        invoke(METHOD_TEST, JSONObject().put("xrayJson", configJson)).error

    fun isRunning(): Boolean {
        val result = invoke(METHOD_STATE, null)
        if (result.error != null) return false
        return result.data?.optBoolean("running", false) ?: false
    }

    fun version(): String {
        val result = invoke(METHOD_VERSION, null)
        return result.data?.optString("version", "unknown") ?: "unknown"
    }

    private class Result(val error: String?, val data: JSONObject?)

    private fun invoke(method: String, payload: JSONObject?): Result {
        return try {
            val request = JSONObject()
                .put("apiVersion", API_VERSION)
                .put("method", method)
            if (payload != null) request.put("payload", payload)

            // Never log `request`: for runXray it carries every hop's
            // credentials.
            val raw = LibXray.invoke(request.toString())
            val response = JSONObject(raw)
            if (response.optBoolean("success", false)) {
                Result(null, response.optJSONObject("data"))
            } else {
                val message = response.optString("error", "").ifEmpty {
                    "the engine refused the request"
                }
                Result(message, null)
            }
        } catch (t: Throwable) {
            Result(t.message ?: t.javaClass.simpleName, null)
        }
    }

    /**
     * Best-effort mapping from an engine error string onto the failure kinds
     * the UI knows how to explain.
     *
     * §9.7: with `dialerProxy` a failure at hop 1 surfaces as a generic dial
     * failure at the exit hop, so this can say *what* went wrong but never
     * *which hop*. Anything it cannot classify stays `unknown` rather than
     * being guessed at.
     */
    fun classifyFailure(message: String?): String {
        val text = message?.lowercase() ?: return VpnStateBus.FAILURE_UNKNOWN
        return when {
            "auth" in text || "password" in text || "credential" in text ->
                VpnStateBus.FAILURE_AUTH
            "refused" in text || "timeout" in text || "timed out" in text ||
                "unreachable" in text || "no route" in text || "dial" in text ->
                VpnStateBus.FAILURE_HOP_UNREACHABLE
            "network is unreachable" in text -> VpnStateBus.FAILURE_NETWORK
            else -> VpnStateBus.FAILURE_UNKNOWN
        }
    }
}
