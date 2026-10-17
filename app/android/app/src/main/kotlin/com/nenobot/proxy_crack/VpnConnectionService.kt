package com.nenobot.proxy_crack

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.TrafficStats
import android.net.VpnService
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.os.Process
import android.system.OsConstants
import android.util.Log
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.concurrent.atomic.AtomicBoolean
import org.json.JSONObject

/**
 * The real tunnel.
 *
 * Owns everything Android — consent, the TUN, the foreground notification,
 * network changes, teardown — and nothing about chains. The complete engine
 * config arrives from Dart; the only thing this class puts into it is the
 * file descriptor, which cannot exist before `establish()` has run.
 *
 * ## Fail closed
 *
 * If the engine stops while the tunnel is up, the TUN is **left in place**.
 * Nothing reads from it, so every packet the system writes is dropped. That
 * is the entire point: the alternative — tearing the interface down — hands
 * traffic straight back to the physical network, which is exactly the leak a
 * kill switch exists to prevent. The notification says so, and only an
 * explicit disconnect removes the interface.
 */
class VpnConnectionService : VpnService() {

    companion object {
        private const val TAG = "VpnConnectionService"

        const val ACTION_CONNECT = "com.nenobot.proxy_crack.action.CONNECT"
        const val ACTION_DISCONNECT = "com.nenobot.proxy_crack.action.DISCONNECT"

        const val EXTRA_CONFIG = "config"
        const val EXTRA_MTU = "mtu"
        const val EXTRA_IPV4_ADDRESS = "ipv4Address"
        const val EXTRA_IPV4_PREFIX = "ipv4Prefix"
        const val EXTRA_IPV6_ADDRESS = "ipv6Address"
        const val EXTRA_IPV6_PREFIX = "ipv6Prefix"
        const val EXTRA_ROUTE_IPV6 = "routeIpv6"
        const val EXTRA_DNS_SERVERS = "dnsServers"
        const val EXTRA_SUPPORTS_UDP = "supportsUdp"
        const val EXTRA_HOP_COUNT = "hopCount"
        const val EXTRA_ROUTE_SUMMARY = "routeSummary"
        const val EXTRA_CHAIN_NAME = "chainName"

        /** Mirrors `XrayConfigBuilder.tunFdEnvKey`. */
        private const val TUN_FD_ENV_KEY = "xray.tun.fd"
        private const val TUN_FD_PLACEHOLDER = "__RUNTIME_FD__"

        /**
         * The resolver the Go runtime itself uses while the tunnel is up,
         * reached over a protected socket. Not what app traffic resolves
         * through — that is the config's `dns` block.
         */
        private const val PROCESS_DNS = "1.1.1.1:53"

        private const val NOTIFICATION_ID = 0x9001
        private const val CHANNEL_ID = "proxy_crack_tunnel"

        private const val WATCHDOG_INTERVAL_MS = 4_000L
        private const val STATS_INTERVAL_MS = 1_000L

        /**
         * How long teardown gives the engine to stop before closing the
         * TUN file descriptor anyway.
         *
         * `stopXray()` is a synchronous call into the Go runtime. A wedged
         * engine — one blocked dialling a hop that will never answer —
         * does not return from it. Waiting for it before closing the
         * descriptor means the user's disconnect never completes, the
         * interface stays up, nothing drains it, and the device has no
         * network and no way out from inside the app. That is exactly the
         * failure this constant exists to make impossible.
         *
         * A leaked engine is a smaller problem than a bricked device, and
         * the service is stopping anyway, so the process goes with it.
         */
        private const val ENGINE_STOP_DEADLINE_MS = 2_000L
    }

    private val handler = Handler(Looper.getMainLooper())

    /**
     * Held for the whole session. If this reference is dropped and the
     * descriptor finalised, the tunnel dies silently — no error, no event,
     * just a device that stops having internet.
     */
    private var tunnel: ParcelFileDescriptor? = null

    private var connectedAtMillis: Long = 0
    private var hopCount: Int = 0
    private var routeSummary: String = ""
    private var chainName: String = ""
    private var supportsUdp: Boolean = true
    private var engineAlive: Boolean = false

    private var baselineRx: Long = 0
    private var baselineTx: Long = 0
    private var lastRx: Long = 0
    private var lastTx: Long = 0
    private var lastSampleAt: Long = 0

    private var connectivity: ConnectivityManager? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    /**
     * Teardown guards.
     *
     * [tearingDown] makes a teardown idempotent — the notification action,
     * the UI, `onRevoke` and `onDestroy` can all arrive at once, and only
     * the first should do anything. [finishing] makes the closing half
     * idempotent too, because it is raced on purpose: the engine's worker
     * thread and the deadline both aim at it, and whichever wins, the
     * descriptor is closed exactly once.
     */
    private val tearingDown = AtomicBoolean(false)
    private val finishing = AtomicBoolean(false)

    private val watchdog = object : Runnable {
        override fun run() {
            if (tunnel == null) return
            if (engineAlive && !XrayBridge.isRunning()) {
                onEngineDied()
                return
            }
            handler.postDelayed(this, WATCHDOG_INTERVAL_MS)
        }
    }

    private val statsTicker = object : Runnable {
        override fun run() {
            if (tunnel == null) return
            sampleThroughput()
            handler.postDelayed(this, STATS_INTERVAL_MS)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Before anything slow. A foreground service that has not called
        // startForeground() within a few seconds of being started is killed
        // with ForegroundServiceDidNotStartInTimeException.
        //
        // A disconnect gets its own notification text rather than the
        // connecting one: the shade should not flash "Connecting" at a user
        // who just pressed Disconnect.
        startForegroundCompat(
            buildNotification(
                if (intent?.action == ACTION_DISCONNECT) {
                    VpnStateBus.STATE_DISCONNECTING
                } else {
                    VpnStateBus.STATE_CONNECTING
                }
            )
        )

        when (intent?.action) {
            ACTION_DISCONNECT -> {
                teardown(VpnStateBus.STATE_DISCONNECTED, null, null)
            }

            ACTION_CONNECT -> {
                connect(intent)
            }

            else -> {
                // No Intent of ours. That means the system started us —
                // always-on VPN, or a sticky restart. We have no chain and
                // no credentials (§7.1 note 8: credentials are never
                // persisted), so there is nothing to reconnect to. Say so
                // instead of sitting on a dead foreground service.
                //
                // The manifest opts out of always-on for this reason; a
                // headless reconnect needs cached auth and a credential
                // re-fetch, which is phase 4 work.
                teardown(
                    VpnStateBus.STATE_ERROR,
                    "The tunnel was started by the system without a saved " +
                        "route. Open the app and connect again.",
                    VpnStateBus.FAILURE_UNKNOWN
                )
            }
        }

        // Never START_STICKY: a restart with a null Intent could only ever
        // land in the branch above.
        return START_NOT_STICKY
    }

    private fun connect(intent: Intent) {
        // A CONNECT can land on an instance that has already torn down, if
        // a reconnect beats `onDestroy`. Without this the guards would
        // still be latched and the *next* disconnect would be a no-op — a
        // tunnel with no off switch, which is the whole defect.
        tearingDown.set(false)
        finishing.set(false)

        val configJson = intent.getStringExtra(EXTRA_CONFIG)
        if (configJson.isNullOrEmpty()) {
            teardown(
                VpnStateBus.STATE_ERROR,
                "No tunnel configuration was supplied.",
                VpnStateBus.FAILURE_UNKNOWN
            )
            return
        }

        hopCount = intent.getIntExtra(EXTRA_HOP_COUNT, 0)
        routeSummary = intent.getStringExtra(EXTRA_ROUTE_SUMMARY).orEmpty()
        chainName = intent.getStringExtra(EXTRA_CHAIN_NAME).orEmpty()
        supportsUdp = intent.getBooleanExtra(EXTRA_SUPPORTS_UDP, true)

        VpnStateBus.publish(
            VpnStateBus.STATE_CONNECTING,
            detail = "Opening the tunnel interface",
            progress = 0.25
        )

        val descriptor = try {
            establishTunnel(intent)
        } catch (t: Throwable) {
            Log.e(TAG, "establish() failed", t)
            teardown(
                VpnStateBus.STATE_ERROR,
                "Android refused to create the tunnel interface.",
                VpnStateBus.FAILURE_PERMISSION_DENIED
            )
            return
        }

        if (descriptor == null) {
            // Documented return value: consent was revoked between the
            // prepare() check and now.
            teardown(
                VpnStateBus.STATE_ERROR,
                "VPN permission was withdrawn before the tunnel could open.",
                VpnStateBus.FAILURE_PERMISSION_DENIED
            )
            return
        }
        tunnel = descriptor

        VpnStateBus.publish(
            VpnStateBus.STATE_CONNECTING,
            detail = if (hopCount > 1) {
                "Dialling $hopCount hops, each through the last"
            } else {
                "Dialling the first hop"
            },
            progress = 0.6
        )

        // Must be in place before the engine opens its first socket.
        XrayBridge.protector = { fd -> protect(fd) }
        XrayBridge.ensureControllerRegistered()
        XrayBridge.uidLookup = ::ownerUidOf
        XrayBridge.ensureProcessFinderRegistered(Build.VERSION.SDK_INT)
        XrayBridge.setProcessDns(PROCESS_DNS)

        val runtimeConfig = try {
            withTunFd(configJson, descriptor.fd)
        } catch (t: Throwable) {
            Log.e(TAG, "config rewrite failed", t)
            teardown(
                VpnStateBus.STATE_ERROR,
                "The tunnel configuration could not be read.",
                VpnStateBus.FAILURE_UNKNOWN
            )
            return
        }

        val error = XrayBridge.runXray(runtimeConfig)
        if (error != null) {
            // The message may name a host but never a credential; libXray
            // reports parser and dial errors, not config contents.
            Log.e(TAG, "engine refused to start: $error")
            teardown(
                VpnStateBus.STATE_ERROR,
                engineErrorDetail(error),
                XrayBridge.classifyFailure(error)
            )
            return
        }

        engineAlive = true
        connectedAtMillis = System.currentTimeMillis()
        resetThroughputBaseline()

        registerNetworkCallback()
        handler.postDelayed(watchdog, WATCHDOG_INTERVAL_MS)
        handler.postDelayed(statsTicker, STATS_INTERVAL_MS)

        updateNotification(buildNotification(VpnStateBus.STATE_CONNECTED))
        VpnStateBus.publish(
            VpnStateBus.STATE_CONNECTED,
            detail = null,
            connectedAtMillis = connectedAtMillis
        )
    }

    private fun establishTunnel(intent: Intent): ParcelFileDescriptor? {
        val mtu = intent.getIntExtra(EXTRA_MTU, 1500)
        val ipv4 = intent.getStringExtra(EXTRA_IPV4_ADDRESS) ?: "10.215.173.1"
        val ipv4Prefix = intent.getIntExtra(EXTRA_IPV4_PREFIX, 32)
        val ipv6 = intent.getStringExtra(EXTRA_IPV6_ADDRESS)
        val ipv6Prefix = intent.getIntExtra(EXTRA_IPV6_PREFIX, 126)
        val routeIpv6 = intent.getBooleanExtra(EXTRA_ROUTE_IPV6, true)
        val dnsServers = intent.getStringArrayListExtra(EXTRA_DNS_SERVERS)
            ?: arrayListOf("1.1.1.1")

        val builder = Builder()
            .setSession(if (chainName.isEmpty()) "Proxy Crack" else chainName)
            // §9.1: this must equal the `mtu` inside the engine config, or
            // large transfers stall while small ones work.
            .setMtu(mtu)
            .addAddress(ipv4, ipv4Prefix)
            .addRoute("0.0.0.0", 0)
            .setBlocking(false)

        if (routeIpv6 && !ipv6.isNullOrEmpty()) {
            // §8.6 item 4: routing only v4 on a dual-stack network sends
            // every v6-capable destination around the tunnel. Claim ::/0
            // and let v6 fail closed inside the tunnel instead.
            try {
                builder.addAddress(ipv6, ipv6Prefix)
                builder.addRoute("::", 0)
            } catch (t: Throwable) {
                Log.w(TAG, "IPv6 route refused; v4 only", t)
            }
        }

        // §8 layer 1. Any address works because the routing rules hijack
        // port 53 regardless of destination — what matters is that the
        // packets enter the TUN.
        for (server in dnsServers) builder.addDnsServer(server)

        // allowBypass() is deliberately NOT called (§9.6): it lets apps
        // opt out of the VPN, which is the opposite of a kill switch.
        //
        // addDisallowedApplication(self) is deliberately NOT called either.
        // It is the cheap fix for the routing loop, but it is also what
        // breaks under always-on lockdown, and the engine's own sockets are
        // already handled properly by the protect() hook above.

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            builder.setMetered(false)
        }

        return builder.establish()
    }

    /**
     * Writes the descriptor into the config's root `env`.
     *
     * Verified in Xray-core at the pinned commit: `Config.Env` is a
     * `map[string]string` applied with `os.Setenv` during `Config.Build()`,
     * and `proxy/tun/tun_android.go` reads `platform.TunFdKey`
     * (`"xray.tun.fd"`) to find the descriptor. `SetTunFd` no longer exists.
     *
     * Parsed and re-serialised rather than string-replaced so a config that
     * did not come from our generator fails loudly here instead of starting
     * an engine with no interface.
     */
    private fun withTunFd(configJson: String, fd: Int): String {
        val root = JSONObject(configJson)
        val env = root.optJSONObject("env") ?: JSONObject().also {
            root.put("env", it)
        }
        val existing = env.optString(TUN_FD_ENV_KEY, "")
        if (existing != TUN_FD_PLACEHOLDER) {
            Log.w(TAG, "config did not carry the fd placeholder; substituting anyway")
        }
        env.put(TUN_FD_ENV_KEY, fd.toString())
        return root.toString()
    }

    /**
     * The engine stopped on its own while the interface is still up.
     *
     * The interface stays. Nothing drains it, so traffic stops rather than
     * reverting to the physical network.
     */
    private fun onEngineDied() {
        engineAlive = false
        handler.removeCallbacks(watchdog)
        handler.removeCallbacks(statsTicker)
        Log.e(TAG, "engine stopped unexpectedly; holding the interface closed")
        updateNotification(buildNotification(VpnStateBus.STATE_ERROR))
        VpnStateBus.publish(
            VpnStateBus.STATE_ERROR,
            detail = "The tunnel engine stopped. Traffic is blocked rather " +
                "than sent unprotected. Disconnect and try again.",
            failure = VpnStateBus.FAILURE_UNKNOWN
        )
    }

    /**
     * The user enabled a different VPN, or revoked consent. Android has
     * already taken the interface away, so there is nothing to hold.
     */
    override fun onRevoke() {
        Log.w(TAG, "onRevoke: another VPN took over")
        teardown(
            VpnStateBus.STATE_ERROR,
            "Another VPN took over the device, so this tunnel was stopped. " +
                "Your traffic is no longer going through the chain.",
            VpnStateBus.FAILURE_PERMISSION_DENIED
        )
        super.onRevoke()
    }

    override fun onDestroy() {
        teardown(VpnStateBus.STATE_DISCONNECTED, null, null)
        super.onDestroy()
    }

    /**
     * Brings the interface down. **Nothing in here can be blocked by the
     * network, the engine, or the backend.**
     *
     * §5.3 item 8 still holds — stop the engine, *then* close the
     * descriptor, *then* leave the foreground, because closing the
     * descriptor first leaves the engine reading a dead fd — but it now
     * holds *on a deadline*. The ordering is a courtesy to the engine; the
     * descriptor closing is not negotiable.
     *
     * What changed, and why:
     *
     * * `stopXray()` used to run **synchronously on the main thread**. It
     *   is a JNI call into the Go runtime, and a wedged engine does not
     *   return from it. Everything below it — including `tunnel.close()` —
     *   was therefore gated on an engine that might never answer. A user
     *   pressing Disconnect, or the notification's Disconnect action, got
     *   nothing, and the only remaining way out was
     *   `adb shell am force-stop`. That is the worst failure a VPN app can
     *   have, and it happened.
     * * The engine stop now runs on a worker thread. The descriptor is
     *   closed either when the engine confirms it has stopped, or when
     *   [ENGINE_STOP_DEADLINE_MS] expires — whichever comes first.
     * * The final half is idempotent and can be entered from either path,
     *   so neither can double-close or double-publish.
     */
    private fun teardown(state: String, detail: String?, failure: String?) {
        if (!tearingDown.compareAndSet(false, true)) return

        // Everything in this block is local, synchronous and incapable of
        // blocking: cancel timers, stop watching the network, and tell the
        // UI what is happening.
        handler.removeCallbacks(watchdog)
        handler.removeCallbacks(statsTicker)
        unregisterNetworkCallback()

        val hadTunnel = tunnel != null
        if (hadTunnel) {
            VpnStateBus.publish(
                VpnStateBus.STATE_DISCONNECTING,
                detail = "Closing the tunnel"
            )
        }

        val hadEngine = engineAlive
        engineAlive = false

        // Close the descriptor no later than the deadline, whatever the
        // engine does. Posted before the engine is even asked, so a worker
        // thread that never starts cannot defeat it either.
        val finish = Runnable { finishTeardown(state, detail, failure) }
        handler.postDelayed(finish, ENGINE_STOP_DEADLINE_MS)

        if (!hadEngine) {
            handler.removeCallbacks(finish)
            finishTeardown(state, detail, failure)
            return
        }

        Thread({
            try {
                XrayBridge.stopXray()
                XrayBridge.resetProcessDns()
            } catch (t: Throwable) {
                Log.w(TAG, "stopping the engine failed; closing anyway", t)
            }
            handler.post {
                handler.removeCallbacks(finish)
                finishTeardown(state, detail, failure)
            }
        }, "proxy-crack-engine-stop").start()
    }

    /**
     * Closes the TUN and stops the service. Runs exactly once per teardown,
     * from whichever of the engine's completion or the deadline arrives
     * first.
     */
    private fun finishTeardown(state: String, detail: String?, failure: String?) {
        if (!finishing.compareAndSet(false, true)) return

        XrayBridge.protector = null
        XrayBridge.uidLookup = null

        // The one line that actually frees the device. It is reached on
        // every path, including the one where the engine never answered.
        try {
            tunnel?.close()
        } catch (t: Throwable) {
            Log.w(TAG, "closing the tunnel descriptor failed", t)
        }
        tunnel = null
        connectedAtMillis = 0

        VpnStateBus.resetStats()
        VpnStateBus.publish(state, detail = detail, failure = failure)

        stopForegroundCompat()
        stopSelf()
    }

    // --- per-app routing ---------------------------------------------------

    /**
     * Which UID owns the connection the engine is about to route.
     *
     * Feeds the `process` routing rule that keeps **this app's own API
     * traffic out of the chain**. Without it, the app's calls to our
     * backend are carried by the chain they are trying to build; when the
     * chain cannot carry traffic, the app cannot reach the backend, and the
     * only thing that can turn the tunnel off is the thing the tunnel has
     * cut off.
     *
     * `getConnectionOwnerUid` is API 29+ and is granted to the **active
     * `VpnService`**, which is why this lives on the service rather than in
     * `XrayBridge`.
     *
     * Returns `Process.INVALID_UID` (-1) when the platform will not say.
     * That matches no rule, so the connection is routed through the chain
     * like any other — an unanswerable lookup can never turn into a leak.
     *
     * Runs on a Go thread, once per new connection. It is a binder round
     * trip, so it is only paid at all because the generated config carries
     * a `process` rule; a config without one never calls this.
     */
    private fun ownerUidOf(
        network: String,
        srcIP: String,
        srcPort: Int,
        destIP: String,
        destPort: Int
    ): Int {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return -1
        val manager = connectivity
            ?: getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return -1
        val protocol = when (network) {
            "tcp" -> OsConstants.IPPROTO_TCP
            "udp" -> OsConstants.IPPROTO_UDP
            else -> return -1
        }
        return try {
            manager.getConnectionOwnerUid(
                protocol,
                InetSocketAddress(InetAddress.getByName(srcIP), srcPort),
                InetSocketAddress(InetAddress.getByName(destIP), destPort)
            )
        } catch (t: Throwable) {
            // Unparseable address, a platform that refuses, a revoked VPN.
            -1
        }
    }

    // --- network changes ---------------------------------------------------

    /**
     * §5.3 item 7. Tells Android which physical network the tunnel is riding
     * on, so data usage is attributed and billed correctly and the system
     * does not treat the VPN as its own network for metering decisions.
     */
    private fun registerNetworkCallback() {
        val manager = getSystemService(Context.CONNECTIVITY_SERVICE)
            as? ConnectivityManager ?: return
        connectivity = manager

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                setUnderlyingNetworks(arrayOf(network))
            }

            override fun onLost(network: Network) {
                setUnderlyingNetworks(null)
            }
        }
        networkCallback = callback
        try {
            manager.registerDefaultNetworkCallback(callback)
        } catch (t: Throwable) {
            Log.w(TAG, "could not watch the default network", t)
            networkCallback = null
        }
    }

    private fun unregisterNetworkCallback() {
        val manager = connectivity
        val callback = networkCallback
        if (manager != null && callback != null) {
            try {
                manager.unregisterNetworkCallback(callback)
            } catch (t: Throwable) {
                Log.w(TAG, "unregistering the network callback failed", t)
            }
        }
        networkCallback = null
        connectivity = null
    }

    // --- throughput --------------------------------------------------------

    /**
     * Counters come from `TrafficStats` for this UID, not from the engine.
     *
     * Xray's own statistics need the gRPC API service plus an extra inbound
     * and a policy block — a lot of surface for a number on a card. Because
     * the engine runs inside this process, its upstream sockets are this
     * UID's traffic, so the counters measure what actually crosses the
     * network.
     *
     * The honest caveat: anything else this process sends (the app's own
     * API calls) is counted too. Those are small next to tunnelled traffic,
     * but the number is "this app's network use", not "bytes through the
     * chain", and it is not sold as the latter.
     */
    private fun resetThroughputBaseline() {
        baselineRx = currentRx()
        baselineTx = currentTx()
        lastRx = baselineRx
        lastTx = baselineTx
        lastSampleAt = System.currentTimeMillis()
        VpnStateBus.resetStats()
    }

    private fun sampleThroughput() {
        val now = System.currentTimeMillis()
        val rx = currentRx()
        val tx = currentTx()
        val elapsed = (now - lastSampleAt).coerceAtLeast(1L)

        val downRate = ((rx - lastRx).coerceAtLeast(0L) * 1000.0) / elapsed
        val upRate = ((tx - lastTx).coerceAtLeast(0L) * 1000.0) / elapsed

        lastRx = rx
        lastTx = tx
        lastSampleAt = now

        VpnStateBus.publishStats(
            bytesDown = (rx - baselineRx).coerceAtLeast(0L),
            bytesUp = (tx - baselineTx).coerceAtLeast(0L),
            downBytesPerSecond = downRate,
            upBytesPerSecond = upRate,
            sessionSeconds = if (connectedAtMillis == 0L) {
                0L
            } else {
                (now - connectedAtMillis) / 1000L
            }
        )
    }

    private fun currentRx(): Long =
        TrafficStats.getUidRxBytes(Process.myUid()).coerceAtLeast(0L)

    private fun currentTx(): Long =
        TrafficStats.getUidTxBytes(Process.myUid()).coerceAtLeast(0L)

    // --- notification ------------------------------------------------------

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            // Android 14+ requires a type. `specialUse` rather than
            // `systemExempted`: the latter is documented for "VPN apps
            // configured using Settings > Network & Internet > VPN", which
            // is the always-on case, not an app starting its own tunnel
            // from its own UI. Declaring it without qualifying throws
            // ForegroundServiceTypeNotAllowedException.
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    @Suppress("DEPRECATION")
    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            stopForeground(true)
        }
    }

    private fun updateNotification(notification: Notification) {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE)
            as? NotificationManager ?: return
        manager.notify(NOTIFICATION_ID, notification)
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(Context.NOTIFICATION_SERVICE)
            as? NotificationManager ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Tunnel status",
            // No sound, no heads-up: this notification is a permanent
            // fixture, not an alert.
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Shows the active proxy chain while the tunnel is up."
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    @Suppress("DEPRECATION")
    private fun buildNotification(state: String): Notification {
        ensureChannel()

        val title = when (state) {
            VpnStateBus.STATE_CONNECTED ->
                if (hopCount > 0) "Connected - $hopCount hop${if (hopCount == 1) "" else "s"}"
                else "Connected"
            VpnStateBus.STATE_ERROR -> "Traffic blocked"
            VpnStateBus.STATE_DISCONNECTING -> "Disconnecting"
            else -> "Connecting"
        }

        val body = when (state) {
            VpnStateBus.STATE_CONNECTED -> buildString {
                append(if (routeSummary.isEmpty()) chainName else routeSummary)
                if (!supportsUdp) append("  -  TCP only")
            }
            VpnStateBus.STATE_ERROR ->
                "The engine stopped. Nothing is leaving the device unprotected."
            VpnStateBus.STATE_DISCONNECTING -> "Closing the tunnel"
            else -> "Building the chain"
        }

        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or
                    Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
            },
            pendingIntentFlags()
        )

        // Users expect to kill a VPN from the shade. Not offering it reads
        // as malware.
        val disconnectIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, VpnConnectionService::class.java).apply {
                action = ACTION_DISCONNECT
            },
            pendingIntentFlags()
        )

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            Notification.Builder(this)
        }

        return builder
            .setContentTitle(title)
            .setContentText(body)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentIntent(contentIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .addAction(
                Notification.Action.Builder(
                    null,
                    "Disconnect",
                    disconnectIntent
                ).build()
            )
            .build()
    }

    private fun pendingIntentFlags(): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        } else {
            PendingIntent.FLAG_UPDATE_CURRENT
        }

    private fun engineErrorDetail(error: String): String = when (
        XrayBridge.classifyFailure(error)
    ) {
        VpnStateBus.FAILURE_AUTH ->
            "A hop rejected the credentials for this chain."
        VpnStateBus.FAILURE_HOP_UNREACHABLE ->
            "A hop in the chain did not answer. Replace it and try again."
        VpnStateBus.FAILURE_NETWORK ->
            "This device has no usable network connection."
        else ->
            "The tunnel engine refused this chain."
    }
}
