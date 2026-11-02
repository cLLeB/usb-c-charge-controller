package org.smartsocket.app;

import android.content.Context;
import android.os.Looper;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URLEncoder;
import java.util.HashMap;
import java.util.Map;

/**
 * How the app reaches one socket: over Wi-Fi (its HTTP API) when the socket answers there, otherwise
 * over Bluetooth (its command channel, program 1.6). The screens ask for an operation by name and get
 * the same Api.Result either way; Result.via says which link carried it.
 *
 * Operations and what they become:
 *   op        HTTP                          Bluetooth command
 *   status    GET  /api/status              full
 *   info      GET  /api/info                info
 *   config    POST /api/config?k=v&..       config k=v&..
 *   boost     POST /api/boost?on=1|0        boost 1|0
 *   name      POST /api/name?name=          name <text>
 *   pin       POST /api/pin?new=            pin <new>  (after "auth <stored PIN>")
 *   identify  POST /api/identify            identify
 *   history   GET  /api/history             history
 *   nets      GET  /api/nets                nets
 *   scan      GET  /api/scan                scan
 *   forget    POST /api/nets/forget?ssid=   forget <ssid>
 *   wifi      POST /api/wifi?ssid=&pass=&user=   setup {"ssid":..,"pass":..,"user":..}
 *   join      GET  /api/setup_status        join
 *   battery   GET  /api/battery?level=      battery <level>   (NOT in program 1.6, see ReporterService)
 * Program updates are not operations here: DeviceActivity sends them with Api.upload (Wi-Fi) or
 * Ble.upload (Bluetooth, program 1.7), whichever link the socket answers on.
 */
final class Link {
    static final String WIFI = "wifi", BLE = "ble";

    private static final Map<String, Link> all = new HashMap<>();
    private static final int WIFI_TIMEOUT_MS = 2000;      // a socket on the same network answers in well under a second
    private static final long WIFI_RECHECK_MS = 20000;    // while on Bluetooth, look whether Wi-Fi works again this often

    static Link get(Context c, String id) {
        synchronized (all) {
            Link l = all.get(id);
            if (l == null) {
                l = new Link(c.getApplicationContext(), id);
                all.put(id, l);
            }
            return l;
        }
    }

    static String label(String via) {
        return WIFI.equals(via) ? "via Wi-Fi" : BLE.equals(via) ? "via Bluetooth" : "";
    }

    private final Context ctx;
    final String id;
    private volatile String via;
    private long lastWifiCheck = -WIFI_RECHECK_MS;
    private boolean checkingWifi;

    private Link(Context c, String id) {
        ctx = c;
        this.id = id;
    }

    /** The link that carried the last answer: WIFI, BLE, or null. */
    String via() { return via; }

    void call(String op, Map<String, String> args, Api.Callback cb) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            Api.MAIN.post(() -> call(op, args, cb));
            return;
        }
        Store.Socket s = new Store(ctx).get(id);
        if (s == null) {
            cb.done(offline("this socket was removed from the app"));
            return;
        }
        boolean hasWifi = s.base != null && !s.base.isEmpty();
        if (!hasWifi || BLE.equals(via)) {
            if (hasWifi) recheckWifi(s);
            overBle(s, op, args, cb, hasWifi);
            return;
        }
        overWifi(s, op, args, WIFI_TIMEOUT_MS, r -> {
            if (r.code != -1) { // any HTTP answer, even "wrong PIN", means Wi-Fi works
                cb.done(r);
                return;
            }
            overBle(s, op, args, cb, false);
        });
    }

    /** While Bluetooth is in use, ask now and then whether the socket answers on Wi-Fi again. */
    private void recheckWifi(Store.Socket s) {
        long now = SystemClock.elapsedRealtime();
        if (checkingWifi || now - lastWifiCheck < WIFI_RECHECK_MS) return;
        checkingWifi = true;
        lastWifiCheck = now;
        String base = s.base;
        Api.POOL.execute(() -> {
            Api.Result r = Api.call(base, "GET", "/api/info", null, null, 1500);
            Api.MAIN.post(() -> {
                checkingWifi = false;
                if (r.ok() && r.json != null && id.equalsIgnoreCase(r.json.optString("id"))) {
                    via = WIFI; // the Bluetooth link closes by itself once unused
                }
            });
        });
    }

    // ------------------------------------------------------------------ Wi-Fi
    private void overWifi(Store.Socket s, String op, Map<String, String> args, int timeoutMs, Api.Callback cb) {
        String method, path;
        Map<String, String> a = args;
        String pin = s.pin;
        switch (op) {
            case "status": method = "GET"; path = "/api/status"; break;
            case "info": method = "GET"; path = "/api/info"; pin = null; break;
            case "config": method = "POST"; path = "/api/config"; break;
            case "boost": method = "POST"; path = "/api/boost"; break;
            case "name": method = "POST"; path = "/api/name"; break;
            case "pin": method = "POST"; path = "/api/pin"; break;
            case "identify": method = "POST"; path = "/api/identify"; pin = null; break;
            case "history": method = "GET"; path = "/api/history"; break;
            case "nets": method = "GET"; path = "/api/nets"; break;
            case "scan": method = "GET"; path = "/api/scan"; break;
            case "forget": method = "POST"; path = "/api/nets/forget"; break;
            case "wifi": method = "POST"; path = "/api/wifi"; break;
            case "join": method = "GET"; path = "/api/setup_status"; pin = null; break;
            case "battery": method = "GET"; path = "/api/battery"; break;
            default:
                cb.done(offline("unknown operation " + op));
                return;
        }
        String m = method, p = path, pn = pin;
        Api.POOL.execute(() -> {
            Api.Result r = Api.call(s.base, m, p, a, pn, timeoutMs);
            Api.MAIN.post(() -> {
                if (r.code != -1) {
                    r.via = WIFI;
                    via = WIFI;
                }
                cb.done(r);
            });
        });
    }

    // ------------------------------------------------------------------ Bluetooth
    private void overBle(Store.Socket s, String op, Map<String, String> args, Api.Callback cb, boolean wifiAfter) {
        String line = bleLine(op, args);
        if (line == null) {
            cb.done(offline("unknown operation " + op));
            return;
        }
        String why = Ble.problem(ctx);
        if (why != null) {
            if (wifiAfter) {
                via = null;
                overWifi(s, op, args, WIFI_TIMEOUT_MS, r -> cb.done(r.code == -1 ? offline(why) : r));
            } else cb.done(offline(why));
            return;
        }
        int timeout = "wifi".equals(op) || "history".equals(op) ? 8000 : 6000;
        Ble.get(ctx, s.id).command(line, timeout, (json, problem) -> {
            if (json == null) {
                if (wifiAfter) { // Bluetooth was in use but the socket is gone from it: try Wi-Fi again
                    via = null;
                    overWifi(s, op, args, WIFI_TIMEOUT_MS, r -> cb.done(r.code == -1 ? offline(problem) : r));
                    return;
                }
                if (BLE.equals(via)) via = null;
                cb.done(offline(problem));
                return;
            }
            via = BLE;
            Api.Result r = parse(json);
            r.via = BLE;
            learnAddress(s, r.json);
            cb.done(r);
        });
    }

    static String bleLine(String op, Map<String, String> a) {
        switch (op) {
            case "status": return "full";
            case "info": return "info";
            case "config": return "config " + query(a);
            case "boost": return "boost " + ("1".equals(arg(a, "on")) ? "1" : "0");
            case "name": return "name " + oneLine(arg(a, "name"));
            case "pin": return "pin " + oneLine(arg(a, "new"));
            case "identify": return "identify";
            case "history": return "history";
            case "nets": return "nets";
            case "scan": return "scan";
            case "forget": return "forget " + oneLine(arg(a, "ssid"));
            case "join": return "join";
            case "battery": return "battery " + arg(a, "level");
            case "wifi":
                try {
                    JSONObject j = new JSONObject();
                    j.put("ssid", arg(a, "ssid"));
                    j.put("pass", arg(a, "pass"));
                    j.put("user", arg(a, "user"));
                    return "setup " + j.toString();
                } catch (Exception e) {
                    return null;
                }
            default: return null;
        }
    }

    private static String arg(Map<String, String> a, String k) {
        String v = a == null ? null : a.get(k);
        return v == null ? "" : v;
    }

    /** A command is one line: no line breaks inside a name or a network name. */
    private static String oneLine(String s) { return s.replace('\r', ' ').replace('\n', ' ').trim(); }

    /** upper=80&mode=on, URL-encoded the way the socket decodes it. */
    private static String query(Map<String, String> a) {
        StringBuilder b = new StringBuilder();
        if (a != null) {
            for (Map.Entry<String, String> e : a.entrySet()) {
                if (b.length() > 0) b.append('&');
                try {
                    b.append(URLEncoder.encode(e.getKey(), "UTF-8")).append('=').append(URLEncoder.encode(e.getValue(), "UTF-8"));
                } catch (Exception ignored) {
                }
            }
        }
        return b.toString();
    }

    /** The socket's JSON answer as an Api.Result: {"error":"pin"} becomes 401 like on HTTP, other errors 400. */
    static Api.Result parse(String json) {
        Api.Result r = new Api.Result();
        try {
            if (json.startsWith("[")) r.arr = new JSONArray(json);
            else r.json = new JSONObject(json);
        } catch (Exception e) {
            r.code = 500;
            r.error = "unreadable answer";
            return r;
        }
        String err = r.json == null ? "" : r.json.optString("error");
        if (err.isEmpty()) r.code = 200;
        else {
            r.code = "pin".equals(err) ? 401 : 400;
            r.error = err;
        }
        return r;
    }

    /** A socket first found over Bluetooth tells its Wi-Fi address: keep it, so Wi-Fi is tried when it works. */
    private void learnAddress(Store.Socket s, JSONObject j) {
        if (j == null) return;
        String ip = j.optString("ip");
        // "info" says "wifi"; the status gives its own setup network's address when not on Wi-Fi (rssi 0)
        boolean onWifi = j.has("wifi") ? j.optBoolean("wifi") : j.has("rssi") && j.optInt("rssi") != 0;
        if (ip.isEmpty() || !onWifi) return;
        String base = "http://" + ip;
        if (!base.equals(s.base)) new Store(ctx).put(s.id, null, base, j.optString("host"));
    }

    static Api.Result offline(String why) {
        Api.Result r = new Api.Result();
        r.code = -1;
        r.error = "offline";
        r.hint = why;
        return r;
    }
}
