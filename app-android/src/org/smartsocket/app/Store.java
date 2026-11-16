package org.smartsocket.app;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;

/** The sockets this app knows, kept on the phone: id -> name, address, PIN. */
final class Store {
    static final class Socket {
        String id, name, base, host, pin;

        String label() { return name == null || name.isEmpty() ? "Socket " + id : name; }
    }

    private final SharedPreferences prefs;

    Store(Context c) { prefs = c.getSharedPreferences("sockets", Context.MODE_PRIVATE); }

    private JSONObject all() {
        try {
            return new JSONObject(prefs.getString("all", "{}"));
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    private void save(JSONObject o) { prefs.edit().putString("all", o.toString()).apply(); }

    List<Socket> list() {
        List<Socket> out = new ArrayList<>();
        JSONObject o = all();
        for (Iterator<String> it = o.keys(); it.hasNext(); ) {
            Socket s = get(it.next());
            if (s != null) out.add(s);
        }
        out.sort((a, b) -> a.label().compareToIgnoreCase(b.label()));
        return out;
    }

    Socket get(String id) {
        JSONObject j = all().optJSONObject(id);
        if (j == null) return null;
        Socket s = new Socket();
        s.id = id;
        s.name = j.optString("name");
        s.base = j.optString("base");
        s.host = j.optString("host");
        s.pin = j.optString("pin");
        return s;
    }

    /** Adds a socket or updates its name and address; the PIN is kept. */
    void put(String id, String name, String base, String host) {
        JSONObject o = all();
        JSONObject j = o.optJSONObject(id);
        try {
            if (j == null) j = new JSONObject();
            if (name != null && !name.isEmpty()) j.put("name", name);
            if (base != null && !base.isEmpty()) j.put("base", base);
            if (host != null && !host.isEmpty()) j.put("host", host);
            o.put(id, j);
        } catch (Exception ignored) {
        }
        save(o);
    }

    void setPin(String id, String pin) {
        JSONObject o = all();
        JSONObject j = o.optJSONObject(id);
        if (j == null) return;
        try {
            j.put("pin", pin == null ? "" : pin);
        } catch (Exception ignored) {
        }
        save(o);
    }

    void remove(String id) {
        JSONObject o = all();
        o.remove(id);
        save(o);
        if (id.equals(reportTarget())) setReportTarget("");
    }

    /** The socket this device's own battery level is sent to ("" = none). */
    String reportTarget() { return prefs.getString("report_to", ""); }

    void setReportTarget(String id) { prefs.edit().putString("report_to", id).apply(); }

    /** Find the socket this device is plugged into by itself, each time it is plugged in. */
    boolean autoPick() { return prefs.getBoolean("auto_pick", true); }

    void setAutoPick(boolean on) { prefs.edit().putBoolean("auto_pick", on).apply(); }

    /** On by default (with auto-pick): a fresh install reports this device's battery without any setting. */
    boolean reporting() { return prefs.getBoolean("reporting", autoPick()); }

    void setReporting(boolean on) { prefs.edit().putBoolean("reporting", on).apply(); }
}
