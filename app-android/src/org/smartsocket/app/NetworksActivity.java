package org.smartsocket.app;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.HashSet;
import java.util.Set;

/**
 * The Wi-Fi networks a socket knows (up to 8: home, school, hotspot...), over Wi-Fi or Bluetooth.
 * Add one (the socket joins it now and remembers it once joined), or forget one. Program 1.6.
 */
public class NetworksActivity extends Activity {
    private Ui ui;
    private Store.Socket sock;
    private Link link;
    private TextView via, status;
    private LinearLayout known, seen;
    private int joinTry;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        ui = new Ui(this);
        sock = new Store(this).get(getIntent().getStringExtra("id"));
        if (sock == null) {
            finish();
            return;
        }
        link = Link.get(this, sock.id);
        LinearLayout col = ui.page(this, "Wi-Fi networks");
        col.addView(ui.text(sock.label(), 15, ui.dim, false));
        via = ui.text("", 12, ui.accent, true);
        col.addView(via);
        status = ui.text("Asking the socket…", 14, ui.dim, false);
        status.setPadding(0, ui.dp(8), 0, ui.dp(8));
        col.addView(status);

        LinearLayout c = ui.card(col);
        c.addView(ui.label("Networks it knows"));
        known = new LinearLayout(this);
        known.setOrientation(LinearLayout.VERTICAL);
        c.addView(known);
        c.addView(ui.text("It joins the strongest one it knows and moves between them by itself.", 13, ui.dim, false));

        c = ui.card(col);
        c.addView(ui.label("In reach of the socket"));
        seen = new LinearLayout(this);
        seen.setOrientation(LinearLayout.VERTICAL);
        c.addView(seen);
        LinearLayout r = ui.row();
        r.addView(ui.button("Look again", false, v -> lookAgain()), ui.weight());
        r.addView(ui.button("Add a network", true, v -> add("", false)), ui.weight());
        c.addView(r, ui.wide());
        c.addView(ui.text("Adding a network makes the socket join it now. Over Wi-Fi this device loses it for a moment "
                + "while it switches; over Bluetooth it stays in touch.", 13, ui.dim, false));
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (sock != null) load();
    }

    private void load() {
        link.call("nets", null, this::show);
    }

    private void showVia(Api.Result r) {
        if (r.via != null) via.setText(Link.label(r.via));
    }

    private boolean failed(Api.Result r) {
        showVia(r);
        if (r.code == -1) {
            status.setText("Not answering over Wi-Fi" + (r.hint == null ? "." : " or Bluetooth (" + r.hint + ")."));
            return true;
        }
        if (r.needsPin()) {
            status.setText("This socket has a PIN: open its page and enter it first.");
            return true;
        }
        if (!r.ok()) {
            status.setText("Not done: " + r.error);
            return true;
        }
        return false;
    }

    /** Shows the answer of "nets" (or of "forget", which is the same). */
    private void show(Api.Result r) {
        if (failed(r) || r.json == null) return;
        JSONObject j = r.json;
        String current = j.optString("current");
        status.setText(current.isEmpty() ? "Not on Wi-Fi right now." : "On " + current + " now.");
        known.removeAllViews();
        JSONArray saved = j.optJSONArray("saved");
        Set<String> knownNames = new HashSet<>();
        for (int i = 0; saved != null && i < saved.length(); i++) {
            JSONObject n = saved.optJSONObject(i);
            if (n == null) continue;
            String ssid = n.optString("ssid");
            knownNames.add(ssid);
            LinearLayout row = ui.row();
            LinearLayout texts = new LinearLayout(this);
            texts.setOrientation(LinearLayout.VERTICAL);
            texts.addView(ui.text(ssid, 16, ui.ink, true));
            String sub = (ssid.equals(current) ? "In use" : "Known")
                    + (n.optString("user").isEmpty() ? "" : " · username " + n.optString("user"));
            texts.addView(ui.text(sub, 13, ui.dim, false));
            row.addView(texts, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
            row.addView(ui.button("Forget", false, v -> forget(ssid, ssid.equals(current))));
            row.setPadding(0, ui.dp(4), 0, ui.dp(4));
            known.addView(row);
        }
        if (knownNames.isEmpty()) known.addView(ui.text("None yet.", 14, ui.dim, false));

        seen.removeAllViews();
        JSONArray near = j.optJSONArray("seen");
        int shown = 0;
        for (int i = 0; near != null && i < near.length(); i++) {
            JSONObject n = near.optJSONObject(i);
            if (n == null || n.optString("ssid").isEmpty()) continue;
            String ssid = n.optString("ssid");
            boolean user = n.optBoolean("user");
            String label = ssid + "  (" + n.optInt("rssi") + " dBm" + (n.optBoolean("open") ? ", open" : "")
                    + (user ? ", username" : "") + (knownNames.contains(ssid) ? ", known" : "") + ")";
            seen.addView(ui.button(label, false, v -> add(ssid, user)), ui.wide());
            shown++;
        }
        if (shown == 0) seen.addView(ui.text("No list yet: tap “Look again”.", 14, ui.dim, false));
    }

    private void lookAgain() {
        status.setText("The socket is looking around… (a few seconds)");
        link.call("scan", null, r -> {
            if (failed(r)) return;
            Api.MAIN.postDelayed(this::load, 5000);
        });
    }

    private void forget(String ssid, boolean inUse) {
        ui.dialog("Forget " + ssid + "?")
                .setMessage(inUse ? "The socket is on this network now: it leaves it and joins another one it knows. "
                        + "Over Wi-Fi this device may lose it; over Bluetooth it stays in touch." : "It will not join it again unless it is added back.")
                .setPositiveButton("Forget", (d, w) -> link.call("forget", Api.args("ssid", ssid), r -> {
                    if (r.ok()) ui.toast("Forgotten: " + ssid);
                    show(r);
                }))
                .setNegativeButton("Cancel", null).show();
    }

    /** Asks for the network's details and sends them; `ssid` empty = type the name. */
    private void add(String ssid, boolean user) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(ui.dp(20), ui.dp(8), ui.dp(20), 0);
        EditText name = ui.field("Network name", false, false);
        name.setText(ssid);
        if (ssid.isEmpty()) box.addView(name);
        EditText userField = ui.field("Username (only for school or work networks)", false, false);
        if (user || ssid.isEmpty()) box.addView(userField);
        EditText pass = ui.field("Password (empty for an open network)", true, false);
        box.addView(pass);
        ui.dialog(ssid.isEmpty() ? "Add a network" : ssid).setView(box).setPositiveButton("Join", (d, w) -> {
            String n = name.getText().toString().trim();
            if (n.isEmpty()) {
                ui.toast("Type the network's name");
                return;
            }
            link.call("wifi", Api.args("ssid", n, "pass", pass.getText().toString(), "user", userField.getText().toString().trim()), r -> {
                if (failed(r)) return;
                status.setText("Joining " + n + "… (up to 30 s)");
                int t = ++joinTry;
                Api.MAIN.postDelayed(() -> watchJoin(n, t, 0), 4000);
            });
        }).setNegativeButton("Cancel", null).show();
    }

    private void watchJoin(String ssid, int t, int round) {
        if (t != joinTry || isFinishing()) return;
        link.call("join", null, r -> {
            if (t != joinTry) return;
            showVia(r);
            JSONObject j = r.ok() ? r.json : null;
            String state = j == null ? "" : j.optString("state");
            if ("ok".equals(state) && ssid.equals(j.optString("ssid"))) {
                status.setText("Joined " + ssid + (j.optString("ip").isEmpty() ? "." : " at " + j.optString("ip") + "."));
                if (!j.optString("ip").isEmpty()) new Store(this).put(sock.id, null, "http://" + j.optString("ip"), j.optString("host"));
                load();
            } else if ("fail".equals(state)) {
                status.setText("It could not join " + ssid + " (reason " + j.optInt("reason") + "). Check the password and try again.");
                load();
            } else if (round < 10) {
                Api.MAIN.postDelayed(() -> watchJoin(ssid, t, round + 1), 3000);
            } else {
                status.setText("No result yet. It keeps trying; look again in a minute.");
                load();
            }
        });
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        joinTry++;
    }
}
