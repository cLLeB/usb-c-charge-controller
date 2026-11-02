package org.smartsocket.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Home screen: every socket this app knows, with its state, and ways to add more. Sockets are found on
 * the Wi-Fi (mDNS or a full search) and over Bluetooth (they advertise "SmartSocket-<id>"), so a socket
 * shows up even on a Wi-Fi that keeps devices apart. Each one is reached over Wi-Fi if it answers there,
 * otherwise over Bluetooth (see Link).
 */
public class MainActivity extends Activity {
    private Ui ui;
    private Store store;
    private Discovery discovery;
    private LinearLayout list;
    private TextView hint, bleNote;
    private final Map<String, JSONObject> lastStatus = new HashMap<>();
    private final Map<String, String> lastVia = new HashMap<>();
    private final Set<String> asking = new HashSet<>(); // sockets with a request on its way
    private final Ble.Found bleFound = this::onBleFound;
    private final Runnable refresher = new Runnable() {
        public void run() {
            refreshAll();
            Api.MAIN.postDelayed(this, 3000);
        }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        ui = new Ui(this);
        store = new Store(this);
        LinearLayout col = ui.page(this, "Smart Sockets");

        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        col.addView(list);
        hint = ui.text("Looking for sockets on this Wi-Fi…", 14, ui.dim, false);
        hint.setPadding(0, 0, 0, ui.dp(12));
        col.addView(hint);
        bleNote = ui.text("", 13, ui.dim, false);
        bleNote.setPadding(0, 0, 0, ui.dp(12));
        bleNote.setVisibility(View.GONE);
        bleNote.setOnClickListener(v -> askPermissions());
        col.addView(bleNote);

        col.addView(ui.button("Set up a new socket", true, v -> startActivity(new Intent(this, SetupActivity.class))), ui.wide());
        LinearLayout r = ui.row();
        r.addView(ui.button("Search this Wi-Fi", false, v -> searchNetwork()), ui.weight());
        r.addView(ui.button("Add by address", false, v -> addByAddress()), ui.weight());
        col.addView(r, ui.wide());

        TextView foot = ui.text("Each socket has its own name and PIN, so several can share one Wi-Fi. "
                + "Tap “Find” on a socket's page to flash its light. Where the Wi-Fi keeps devices apart "
                + "(schools, hotels), the app reaches sockets nearby over Bluetooth instead.", 13, ui.dim, false);
        foot.setPadding(0, ui.dp(16), 0, 0);
        col.addView(foot);

        discovery = new Discovery(this, this::onFound);
        askPermissions();
        if (store.reporting()) ReporterService.start(this);
    }

    @Override
    protected void onResume() {
        super.onResume();
        discovery.start();
        startBle();
        rebuild();
        Api.MAIN.post(refresher);
    }

    /** Looks for sockets over Bluetooth while this screen is open. */
    private void startBle() {
        String why = Ble.problem(this);
        if (why != null) {
            bleNote.setText("Bluetooth: " + why + ". Sockets are reached over Wi-Fi only"
                    + (Ble.permitted(this) ? "." : ". Tap here to allow it."));
            bleNote.setVisibility(View.VISIBLE);
            return;
        }
        bleNote.setVisibility(View.GONE);
        Ble.addListener(bleFound);
        Ble.want(this, this, 24 * 3600 * 1000L);
    }

    private void onBleFound(Ble.Seen s) {
        if (s.id == null || store.get(s.id) != null) return;
        store.put(s.id, null, null, null); // name and address come with its first answer
        rebuild();
    }

    @Override
    protected void onPause() {
        super.onPause();
        discovery.stop();
        Ble.removeListener(bleFound);
        Ble.release(this);
        Api.MAIN.removeCallbacks(refresher);
    }

    private void onFound(JSONObject info, String base) {
        String id = info.optString("id");
        boolean isNew = store.get(id) == null;
        store.put(id, info.optString("name"), base, info.optString("host"));
        if (isNew) rebuild();
    }

    private void rebuild() {
        list.removeAllViews();
        List<Store.Socket> all = store.list();
        hint.setText(all.isEmpty() ? "No sockets yet. Set one up, search this Wi-Fi, or bring this device close to one." : "");
        hint.setVisibility(all.isEmpty() ? View.VISIBLE : View.GONE);
        for (Store.Socket s : all) {
            LinearLayout cardView = ui.card(list);
            LinearLayout top = ui.row();
            View dot = ui.dot(Ui.stateColor(ui, lastStatus.get(s.id)));
            dot.setTag("dot");
            top.addView(dot);
            LinearLayout texts = new LinearLayout(this);
            texts.setOrientation(LinearLayout.VERTICAL);
            TextView name = ui.text(s.label(), 18, ui.ink, true);
            TextView line = ui.text("…", 14, ui.dim, false);
            line.setTag("line");
            texts.addView(name);
            texts.addView(line);
            top.addView(texts, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
            TextView arrow = ui.text("›", 26, ui.dim, false);
            arrow.setGravity(Gravity.CENTER);
            top.addView(arrow);
            cardView.addView(top);
            cardView.setTag(s.id);
            cardView.setOnClickListener(v -> startActivity(new Intent(this, DeviceActivity.class).putExtra("id", s.id)));
            show(cardView, lastStatus.get(s.id), s);
        }
    }

    private void refreshAll() {
        for (Store.Socket s : store.list()) {
            if (!asking.add(s.id)) continue; // still waiting for the last answer (Bluetooth can take a few seconds)
            Link link = Link.get(this, s.id);
            String op = s.pin == null || s.pin.isEmpty() ? "info" : "status";
            link.call(op, null, r -> {
                lastVia.put(s.id, r.via);
                JSONObject st = r.ok() ? r.json : null;
                if (st != null && op.equals("info")) {
                    // Without a PIN: if the socket has none, its full status is open too.
                    if (!st.optBoolean("pin_set")) {
                        link.call("status", null, r2 -> {
                            asking.remove(s.id);
                            lastVia.put(s.id, r2.via);
                            update(s, r2.ok() ? r2.json : null, false);
                        });
                        return;
                    }
                    asking.remove(s.id);
                    update(s, null, true);
                    return;
                }
                asking.remove(s.id);
                if (r.needsPin()) {
                    update(s, null, true);
                    return;
                }
                update(s, st, false);
            });
        }
    }

    private void update(Store.Socket s, JSONObject st, boolean locked) {
        if (st != null) {
            lastStatus.put(s.id, st);
            if (!st.optString("name").isEmpty() && !st.optString("name").equals(s.name)) {
                store.put(s.id, st.optString("name"), null, null);
                rebuild();
                return;
            }
        } else {
            lastStatus.remove(s.id);
        }
        View cardView = list.findViewWithTag(s.id);
        if (cardView == null) return;
        if (locked) {
            String via = Link.label(lastVia.get(s.id));
            ((TextView) cardView.findViewWithTag("line")).setText("Has a PIN: tap to enter it" + (via.isEmpty() ? "" : " · " + via));
            ui.recolor(cardView.findViewWithTag("dot"), ui.line);
            return;
        }
        show(cardView, st, s);
    }

    private void show(View cardView, JSONObject st, Store.Socket s) {
        TextView line = cardView.findViewWithTag("line");
        if (st == null) {
            line.setText("Not answering");
        } else {
            String l = Ui.headline(st);
            if (st.optInt("level", -1) >= 0) l += " · " + st.optInt("level") + " %";
            String via = Link.label(lastVia.get(s.id));
            if (!via.isEmpty()) l += " · " + via;
            line.setText(l);
        }
        ui.recolor(cardView.findViewWithTag("dot"), Ui.stateColor(ui, st));
    }

    private void searchNetwork() {
        hint.setVisibility(View.VISIBLE);
        hint.setText("Searching every address on this Wi-Fi…");
        discovery.searchNetwork(() -> {
            hint.setText(store.list().isEmpty() ? "No socket answered. Is this phone on the same Wi-Fi, or close enough for Bluetooth?" : "");
            hint.setVisibility(store.list().isEmpty() ? View.VISIBLE : View.GONE);
        });
    }

    private void addByAddress() {
        EditText e = ui.field("192.168.1.69 or smartsocket-a1b2c3.local", false, false);
        ui.dialog("Add by address").setView(e).setPositiveButton("Add", (d, w) -> {
            String a = e.getText().toString().trim();
            if (a.isEmpty()) return;
            if (!a.startsWith("http")) a = "http://" + a;
            discovery.check(a);
            ui.toast("Asking " + a);
        }).setNegativeButton("Cancel", null).show();
    }

    /** Notifications (battery reports) and Bluetooth (sockets on Wi-Fi that keeps devices apart), in one question. */
    private void askPermissions() {
        List<String> need = new ArrayList<>(Ble.missing(this));
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            need.add(Manifest.permission.POST_NOTIFICATIONS);
        if (!need.isEmpty()) requestPermissions(need.toArray(new String[0]), 1);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        // Refused: everything still works over Wi-Fi; the note says how to allow it later.
        if (Ble.permitted(this)) startBle();
        else {
            bleNote.setText("Bluetooth is not allowed for this app, so sockets are reached over Wi-Fi only. "
                    + "To allow it: Settings › Apps › Smart Socket › Permissions › Nearby devices"
                    + (Build.VERSION.SDK_INT < 31 ? " (Location on this Android version)." : "."));
            bleNote.setVisibility(View.VISIBLE);
        }
    }
}
