package org.smartsocket.app;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.net.wifi.WifiManager;

import org.json.JSONObject;

import java.net.Inet4Address;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Finds sockets on the home Wi-Fi in two ways:
 * - they announce themselves as "_smartsocket._tcp" (mDNS), which works on most networks;
 * - a full search asks every address of the home network for /api/info, which works everywhere.
 */
final class Discovery {
    interface Found { void found(JSONObject info, String base); }

    private final Context c;
    private final Found found;
    private NsdManager nsd;
    private NsdManager.DiscoveryListener listener;
    private WifiManager.MulticastLock lock;
    private final Deque<NsdServiceInfo> toResolve = new ArrayDeque<>();
    private boolean resolving;

    Discovery(Context c, Found found) {
        this.c = c.getApplicationContext();
        this.found = found;
    }

    void start() {
        try {
            WifiManager wm = (WifiManager) c.getSystemService(Context.WIFI_SERVICE);
            lock = wm.createMulticastLock("smartsocket");
            lock.setReferenceCounted(false);
            lock.acquire();
            nsd = (NsdManager) c.getSystemService(Context.NSD_SERVICE);
            listener = new NsdManager.DiscoveryListener() {
                public void onStartDiscoveryFailed(String t, int e) {}
                public void onStopDiscoveryFailed(String t, int e) {}
                public void onDiscoveryStarted(String t) {}
                public void onDiscoveryStopped(String t) {}
                public void onServiceLost(NsdServiceInfo s) {}
                public void onServiceFound(NsdServiceInfo s) {
                    synchronized (toResolve) { toResolve.add(s); }
                    resolveNext();
                }
            };
            nsd.discoverServices("_smartsocket._tcp", NsdManager.PROTOCOL_DNS_SD, listener);
        } catch (Exception ignored) {
        }
    }

    void stop() {
        try {
            if (nsd != null && listener != null) nsd.stopServiceDiscovery(listener);
        } catch (Exception ignored) {
        }
        listener = null;
        if (lock != null && lock.isHeld()) lock.release();
    }

    // Android resolves one service at a time.
    @SuppressWarnings("deprecation")
    private void resolveNext() {
        NsdServiceInfo next;
        synchronized (toResolve) {
            if (resolving || toResolve.isEmpty()) return;
            resolving = true;
            next = toResolve.poll();
        }
        nsd.resolveService(next, new NsdManager.ResolveListener() {
            public void onResolveFailed(NsdServiceInfo s, int e) { done(); }
            public void onServiceResolved(NsdServiceInfo s) {
                if (s.getHost() instanceof Inet4Address) {
                    String base = "http://" + s.getHost().getHostAddress() + (s.getPort() == 80 ? "" : ":" + s.getPort());
                    check(base);
                }
                done();
            }
            void done() {
                synchronized (toResolve) { resolving = false; }
                resolveNext();
            }
        });
    }

    /** Asks one address whether it is a socket. */
    void check(String base) {
        Api.POOL.execute(() -> {
            Api.Result r = Api.call(base, "GET", "/api/info", null, null, 1500);
            if (r.ok() && r.json != null && r.json.has("id")) Api.MAIN.post(() -> found.found(r.json, base));
        });
    }

    /** Asks every address of the home network (a /24) for /api/info. About 5 s. */
    void searchNetwork(Runnable finished) {
        String prefix = localPrefix();
        if (prefix == null) {
            Api.MAIN.post(finished);
            return;
        }
        ExecutorService pool = Executors.newFixedThreadPool(32);
        AtomicInteger left = new AtomicInteger(254);
        for (int i = 1; i <= 254; i++) {
            String base = "http://" + prefix + i;
            pool.execute(() -> {
                Api.Result r = Api.call(base, "GET", "/api/info", null, null, 700);
                if (r.ok() && r.json != null && r.json.has("id")) Api.MAIN.post(() -> found.found(r.json, base));
                if (left.decrementAndGet() == 0) Api.MAIN.post(finished);
            });
        }
        pool.shutdown();
    }

    /** "192.168.1." for a phone at 192.168.1.23, or null without Wi-Fi. */
    private String localPrefix() {
        try {
            ConnectivityManager cm = (ConnectivityManager) c.getSystemService(Context.CONNECTIVITY_SERVICE);
            Network n = cm.getActiveNetwork();
            LinkProperties lp = cm.getLinkProperties(n);
            if (lp == null) return null;
            for (LinkAddress la : lp.getLinkAddresses()) {
                if (la.getAddress() instanceof Inet4Address) {
                    String ip = la.getAddress().getHostAddress();
                    return ip.substring(0, ip.lastIndexOf('.') + 1);
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }
}
