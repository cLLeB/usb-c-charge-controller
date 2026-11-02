package org.smartsocket.app;

import android.app.Activity;
import android.app.TimePickerDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.format.DateFormat;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.SeekBar;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Map;

/**
 * Everything about one socket: state, limits, timer, charging hours, this phone's reports, history, PIN, Wi-Fi,
 * and loading a new program into it.
 * It talks to the socket through Link: over Wi-Fi when the socket answers there, otherwise over Bluetooth.
 */
public class DeviceActivity extends Activity {
    private static final int PICK_PROGRAM = 7;

    private Ui ui;
    private Store store;
    private Store.Socket sock;
    private Link link;
    private boolean refreshing; // a status request is on its way: do not stack more (Bluetooth sends one at a time)
    private JSONObject st;
    private long editingUntil;
    private boolean askingPin;
    private boolean filling; // true while the screen is filled from the socket's status, so no command is sent
    private boolean loading; // a new program is on its way to the socket

    private TextView title, headline, detail, viaText, levelText, upperVal, lowerVal, timerVal, ledVal, foot, updateNote;
    private View dot;
    private ProgressBar bar;
    private Button boost, schedStart, schedEnd, update;
    private final List<Button> modeButtons = new ArrayList<>(), maButtons = new ArrayList<>(), safetyButtons = new ArrayList<>();
    private SeekBar upper, lower, timer, led;
    private Switch timerOn, schedOn, reportHere, autoPick;

    private final Runnable poll = new Runnable() {
        public void run() {
            refresh();
            Api.MAIN.postDelayed(this, 2000);
        }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        ui = new Ui(this);
        store = new Store(this);
        sock = store.get(getIntent().getStringExtra("id"));
        if (sock == null) {
            finish();
            return;
        }
        link = Link.get(this, sock.id);
        build();
        // Bluetooth is the way in where the Wi-Fi keeps devices apart; ask once, the screen works without it.
        if (!Ble.missing(this).isEmpty()) requestPermissions(Ble.missing(this).toArray(new String[0]), 3);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        refresh();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (sock != null) Api.MAIN.post(poll);
    }

    @Override
    protected void onPause() {
        super.onPause();
        Api.MAIN.removeCallbacks(poll);
    }

    // ------------------------------------------------------------------ layout
    private void build() {
        LinearLayout col = ui.page(this, sock.label());
        title = (TextView) col.getChildAt(0);
        title.setOnClickListener(v -> rename());

        LinearLayout c = ui.card(col);
        LinearLayout top = ui.row();
        dot = ui.dot(ui.line);
        top.addView(dot);
        LinearLayout t = new LinearLayout(this);
        t.setOrientation(LinearLayout.VERTICAL);
        headline = ui.text("Connecting…", 20, ui.ink, true);
        detail = ui.text("", 14, ui.dim, false);
        viaText = ui.text("", 12, ui.accent, true);
        t.addView(headline);
        t.addView(detail);
        t.addView(viaText);
        top.addView(t);
        c.addView(top);
        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        bar.setProgressTintList(android.content.res.ColorStateList.valueOf(ui.go));
        c.addView(bar, ui.wide());
        levelText = ui.text("No battery report yet", 13, ui.dim, false);
        c.addView(levelText);
        LinearLayout r = ui.row();
        boost = ui.button("Charge to 100 % once", false, v -> send("boost", Api.args("on", st != null && st.optBoolean("boost") ? "0" : "1")));
        r.addView(boost, ui.weight());
        r.addView(ui.button("Find", false, v -> {
            link.call("identify", null, x -> ui.toast(x.ok() ? "Its light is flashing" : "Not answering"));
        }));
        c.addView(r, ui.wide());

        c = ui.card(col);
        c.addView(ui.label("Mode"));
        LinearLayout modes = ui.row();
        String[][] m = {{"auto", "Use limits"}, {"on", "Always"}, {"off", "Never"}};
        for (String[] x : m) {
            Button bt = ui.button(x[1], false, v -> send("config", Api.args("mode", x[0])));
            bt.setTag(x[0]);
            modeButtons.add(bt);
            modes.addView(bt, ui.weight());
        }
        c.addView(modes);
        upperVal = ui.text("", 15, ui.ink, false);
        upper = slider(c, "Stop charging at", upperVal, 20, 100, 5, " %", v -> send("config", Api.args("upper", "" + v)));
        lowerVal = ui.text("", 15, ui.ink, false);
        lower = slider(c, "Start charging at", lowerVal, 5, 95, 5, " %", v -> send("config", Api.args("lower", "" + v)));

        c = ui.card(col);
        c.addView(ui.label("Charge timer"));
        c.addView(ui.text("For earbuds, power banks and anything that cannot report its battery: stop after a set time each time it is plugged in.", 13, ui.dim, false));
        timerOn = sw(c, "Use the timer", on -> send("config", Api.args("timer_min", on ? "" + Math.max(15, timer.getProgress() * 15 + 15) : "0")));
        timerVal = ui.text("", 15, ui.ink, false);
        timer = slider(c, "Stop after", timerVal, 15, 600, 15, " min", v -> {
            if (timerOn.isChecked()) send("config", Api.args("timer_min", "" + v));
        });

        c = ui.card(col);
        c.addView(ui.label("Charging hours"));
        c.addView(ui.text("Charge only between two times, for example off-peak hours. “100 % once” still works outside them.", 13, ui.dim, false));
        schedOn = sw(c, "Only charge between", on -> send("config", Api.args("sched_on", on ? "1" : "0")));
        LinearLayout hrs = ui.row();
        schedStart = ui.button("22:00", false, v -> pickTime("sched_start"));
        schedEnd = ui.button("06:00", false, v -> pickTime("sched_end"));
        hrs.addView(schedStart, ui.weight());
        hrs.addView(ui.text(" to ", 15, ui.dim, false));
        hrs.addView(schedEnd, ui.weight());
        c.addView(hrs, ui.wide());

        c = ui.card(col);
        c.addView(ui.label("This device"));
        reportHere = sw(c, "Send this device's battery level to this socket", on -> {
            store.setReportTarget(on ? sock.id : "");
            store.setReporting(on || store.autoPick());
            if (on) ReporterService.start(this);
            else if (!store.autoPick()) ReporterService.stop(this);
        });
        autoPick = sw(c, "Find the socket I'm plugged into by myself (any socket)", on -> {
            store.setAutoPick(on);
            store.setReporting(on || !store.reportTarget().isEmpty());
            if (on) ReporterService.start(this);
        });
        c.addView(ui.text("Reports run in the background with a small notification, so limits work without opening the app.", 13, ui.dim, false));

        c = ui.card(col);
        c.addView(ui.label("More settings"));
        c.addView(ui.text("Most current to offer", 15, ui.ink, false));
        LinearLayout mas = ui.row();
        String[][] ma = {{"500", "Basic"}, {"1500", "1.5 A"}, {"3000", "3 A"}};
        for (String[] x : ma) {
            Button bt = ui.button(x[1], false, v -> send("config", Api.args("max_ma", x[0])));
            bt.setTag(x[0]);
            maButtons.add(bt);
            mas.addView(bt, ui.weight());
        }
        c.addView(mas);
        c.addView(ui.text("If this device stops reporting, charge again after", 15, ui.ink, false), ui.wide());
        LinearLayout sf = ui.row();
        String[][] sv = {{"0", "Never"}, {"6", "6 h"}, {"12", "12 h"}, {"24", "24 h"}};
        for (String[] x : sv) {
            Button bt = ui.button(x[1], false, v -> send("config", Api.args("safety_h", x[0])));
            bt.setTag(x[0]);
            safetyButtons.add(bt);
            sf.addView(bt, ui.weight());
        }
        c.addView(sf);
        ledVal = ui.text("", 15, ui.ink, false);
        led = slider(c, "Light brightness", ledVal, 0, 100, 10, " %", v -> send("config", Api.args("led", "" + v)));

        c = ui.card(col);
        c.addView(ui.label("History"));
        c.addView(ui.button("Show the last charging runs", false, v -> history()), ui.wide());

        c = ui.card(col);
        c.addView(ui.label("Socket"));
        c.addView(ui.button("Rename", false, v -> rename()), ui.wide());
        c.addView(ui.button("Set or change the PIN", false, v -> changePin()), ui.wide());
        c.addView(ui.button("Wi-Fi networks it knows", false, v -> startActivity(new Intent(this, NetworksActivity.class).putExtra("id", sock.id))), ui.wide());
        update = ui.button("Load a new program…", false, v -> pickProgram());
        c.addView(update, ui.wide());
        updateNote = ui.text("", 13, ui.dim, false); // how the loading goes, and how it ended
        updateNote.setVisibility(View.GONE);
        c.addView(updateNote);
        c.addView(ui.button("Remove from this app", false, v -> {
            store.remove(sock.id);
            finish();
        }), ui.wide());
        foot = ui.text("", 12, ui.dim, false);
        foot.setPadding(0, ui.dp(10), 0, 0);
        c.addView(foot);
    }

    interface IntAction { void run(int v); }
    interface BoolAction { void run(boolean on); }

    private SeekBar slider(LinearLayout parent, String name, TextView value, int min, int max, int step, String unit, IntAction done) {
        LinearLayout r = ui.row();
        r.addView(ui.text(name, 15, ui.ink, false), new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        r.addView(value);
        parent.addView(r, ui.wide());
        SeekBar s = new SeekBar(this);
        s.setMax((max - min) / step);
        s.setProgressTintList(android.content.res.ColorStateList.valueOf(ui.accent));
        s.setThumbTintList(android.content.res.ColorStateList.valueOf(ui.accent));
        s.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            public void onProgressChanged(SeekBar sb, int p, boolean user) {
                value.setText((min + p * step) + unit);
                if (user) editingUntil = SystemClock.uptimeMillis() + 3000;
            }
            public void onStartTrackingTouch(SeekBar sb) { editingUntil = SystemClock.uptimeMillis() + 60000; }
            public void onStopTrackingTouch(SeekBar sb) {
                editingUntil = SystemClock.uptimeMillis() + 3000;
                done.run(min + sb.getProgress() * step);
            }
        });
        parent.addView(s);
        value.setText(min + unit);
        return s;
    }

    private Switch sw(LinearLayout parent, String text, BoolAction act) {
        Switch s = new Switch(this);
        s.setText(text);
        s.setTextColor(ui.ink);
        s.setTextSize(15);
        s.setPadding(0, ui.dp(8), 0, ui.dp(4));
        s.setOnCheckedChangeListener((v, on) -> {
            if (!filling) {
                editingUntil = SystemClock.uptimeMillis() + 3000;
                act.run(on);
            }
        });
        parent.addView(s);
        return s;
    }

    // ------------------------------------------------------------------ talking to the socket
    private void refresh() {
        if (refreshing || loading) return; // while a program loads, the link is busy with it
        refreshing = true;
        link.call("status", null, r -> {
            refreshing = false;
            onResult(r);
        });
    }

    private void send(String op, Map<String, String> args) {
        editingUntil = 0;
        link.call(op, args, this::onResult);
    }

    private void showVia(String via) {
        viaText.setText(Link.label(via));
    }

    private void onResult(Api.Result r) {
        if (r.via != null) showVia(r.via);
        if (r.needsPin()) {
            askPin(false);
            return;
        }
        if (r.locked()) {
            headline.setText("Too many wrong PINs");
            detail.setText("Wait a minute, then try again.");
            return;
        }
        if (!r.ok() || r.json == null || !r.json.has("reason")) {
            if (r.code == -1) {
                headline.setText("Not answering");
                detail.setText("Not reachable over Wi-Fi" + (r.hint == null ? "." : ", and over Bluetooth: " + r.hint + ".")
                        + " Is the socket powered and close by?");
                viaText.setText("");
                ui.recolor(dot, ui.line);
            } else if (!r.ok()) {
                ui.toast("Not done: " + r.error);
            }
            return;
        }
        st = r.json;
        show();
    }

    private void show() {
        headline.setText(Ui.headline(st));
        detail.setText(Ui.detail(st));
        ui.recolor(dot, Ui.stateColor(ui, st));
        int level = st.optInt("level", -1);
        bar.setProgress(Math.max(level, 0));
        long age = st.optLong("level_age_s", -1);
        levelText.setText(level < 0 ? "No battery report yet" : level + " % · reported " + ago(age));
        boost.setText(st.optBoolean("boost") ? "Going to 100 % (tap to stop)" : "Charge to 100 % once");
        ui.style(boost, st.optBoolean("boost"));
        String name = st.optString("name");
        if (!name.isEmpty() && !name.equals(sock.name)) {
            sock.name = name;
            store.put(sock.id, name, null, null);
            title.setText(name);
        }
        String ma = st.optInt("out_ma") >= 3000 ? "3 A" : st.optInt("out_ma") >= 1500 ? "1.5 A" : "basic current";
        String time = st.optString("time");
        String mode = st.optString("mode");
        String modeName = "on".equals(mode) ? "always" : "off".equals(mode) ? "never" : "use limits";
        foot.setText("Program " + st.optString("fw") + " · ID " + st.optString("id") + " · mode: " + modeName
                + (Link.BLE.equals(link.via()) ? " · linked over Bluetooth" : "") + "\n" + st.optString("host") + " · " + st.optString("ip")
                + "\nWi-Fi " + st.optString("ssid") + " (" + st.optInt("rssi") + " dBm) · " + st.optDouble("temp_c") + " °C inside"
                + (time.isEmpty() ? "" : " · clock " + time) + "\nCharger offers " + (st.optInt("src_ma") >= 3000 ? "3 A" : st.optInt("src_ma") >= 1500 ? "1.5 A" : "basic")
                + " · offered to the device: " + ma);

        if (SystemClock.uptimeMillis() < editingUntil) return; // do not move a control under the user's finger
        filling = true;
        for (Button bt : modeButtons) ui.style(bt, bt.getTag().equals(st.optString("mode")));
        for (Button bt : maButtons) ui.style(bt, bt.getTag().equals("" + st.optInt("max_ma")));
        for (Button bt : safetyButtons) ui.style(bt, bt.getTag().equals("" + st.optInt("safety_h")));
        upper.setProgress((st.optInt("upper") - 20) / 5);
        lower.setProgress((st.optInt("lower") - 5) / 5);
        led.setProgress(st.optInt("led") / 10);
        int tm = st.optInt("timer_min");
        timerOn.setChecked(tm > 0);
        if (tm > 0) timer.setProgress((tm - 15) / 15);
        schedOn.setChecked(st.optBoolean("sched_on"));
        schedStart.setText(st.optString("sched_start"));
        schedEnd.setText(st.optString("sched_end"));
        reportHere.setChecked(sock.id.equals(store.reportTarget()));
        autoPick.setChecked(store.autoPick() && store.reporting());
        filling = false;
    }

    static String ago(long s) {
        if (s < 0) return "";
        if (s < 90) return s + " s ago";
        if (s < 5400) return (s / 60) + " min ago";
        return (s / 3600) + " h ago";
    }

    // ------------------------------------------------------------------ dialogs
    private void askPin(boolean wrong) {
        if (askingPin) return;
        askingPin = true;
        EditText e = ui.field("PIN", true, true);
        ui.dialog(wrong ? "Wrong PIN, try again" : "This socket has a PIN").setView(e).setCancelable(false)
                .setPositiveButton("OK", (d, w) -> {
                    askingPin = false;
                    sock.pin = e.getText().toString().trim();
                    store.setPin(sock.id, sock.pin);
                    refreshing = false;
                    refresh();
                })
                .setNegativeButton("Back", (d, w) -> {
                    askingPin = false;
                    finish();
                }).show();
    }

    private void rename() {
        EditText e = ui.field("Bedroom, Kitchen, …", false, false);
        e.setText(sock.label());
        e.setSelectAllOnFocus(true); // typing replaces the old name
        e.requestFocus();
        ui.dialog("Name this socket").setView(e).setPositiveButton("Save", (d, w) ->
                send("name", Api.args("name", e.getText().toString().trim()))).setNegativeButton("Cancel", null).show();
    }

    private void changePin() {
        EditText e = ui.field("New PIN, 4 to 12 digits (empty = no PIN)", true, true);
        ui.dialog("PIN for this socket").setView(e).setPositiveButton("Save", (d, w) -> {
            String p = e.getText().toString().trim();
            link.call("pin", Api.args("new", p), r -> {
                if (r.ok()) {
                    sock.pin = p;
                    store.setPin(sock.id, p);
                    ui.toast(p.isEmpty() ? "PIN removed" : "PIN saved on the socket and in this app");
                } else ui.toast("Not saved: " + r.error);
            });
        }).setNegativeButton("Cancel", null).show();
    }

    private void pickTime(String key) {
        String cur = key.equals("sched_start") ? schedStart.getText().toString() : schedEnd.getText().toString();
        int h = 22, m = 0;
        try {
            h = Integer.parseInt(cur.substring(0, 2));
            m = Integer.parseInt(cur.substring(3, 5));
        } catch (Exception ignored) {
        }
        new TimePickerDialog(this, (v, hh, mm) -> send("config", Api.args(key, String.format("%02d:%02d", hh, mm))),
                h, m, DateFormat.is24HourFormat(this)).show();
    }

    private void history() {
        link.call("history", null, r -> {
            if (!r.ok() || r.json == null) {
                ui.toast("Not answering");
                return;
            }
            JSONArray runs = r.json.optJSONArray("runs");
            StringBuilder s = new StringBuilder();
            long now = r.json.optLong("now");
            for (int i = 0; runs != null && i < runs.length(); i++) {
                JSONObject x = runs.optJSONObject(i);
                String when = x.optInt("clock") == 1 ? DateFormat.format("EEE d MMM, HH:mm", new Date(x.optLong("start") * 1000)).toString() : "before the clock was set";
                s.append(when).append("\n   ").append(x.optLong("dur_s") / 60).append(" min");
                if (x.optInt("lvl0") >= 0 || x.optInt("lvl1") >= 0)
                    s.append(", ").append(lvl(x.optInt("lvl0"))).append(" → ").append(lvl(x.optInt("lvl1")));
                s.append(", ended: ").append(why(x.optString("why"))).append("\n\n");
            }
            ui.dialog("Charging history").setMessage(s.length() == 0 ? "Nothing yet." : s.toString().trim())
                    .setPositiveButton("Close", null).show();
        });
    }

    static String lvl(int v) { return v < 0 ? "?" : v + " %"; }

    static String why(String w) {
        switch (w) {
            case "no_phone": return "unplugged";
            case "limit": return "battery limit";
            case "timer": return "timer";
            case "schedule": return "charging hours";
            case "hot": return "too warm";
            case "off": return "set to never";
            default: return w.isEmpty() ? "stopped" : w;
        }
    }

    // ------------------------------------------------------------------ loading a new program
    // The .bin file from "Export compiled binary" (smart_socket.ino.bin). Over Wi-Fi when the socket answers
    // there (/api/update, about half a minute), otherwise over Bluetooth (program 1.7, 2 to 4 minutes).
    private void pickProgram() {
        if (loading) return;
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
        startActivityForResult(i, PICK_PROGRAM);
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req != PICK_PROGRAM || res != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();
        Api.POOL.execute(() -> {
            byte[] bin;
            try (InputStream in = getContentResolver().openInputStream(uri)) {
                bin = readAll(in, 2000000);
            } catch (Exception e) {
                bin = null;
            }
            byte[] b = bin;
            Api.MAIN.post(() -> {
                if (b == null) note("Could not read that file (a program is at most 1.9 MB).");
                else if (b.length <= 100000 || (b[0] & 0xff) != 0xE9)
                    note("That does not look like a program for the socket (use smart_socket.ino.bin).");
                else confirmProgram(b);
            });
        });
    }

    private void confirmProgram(byte[] bin) {
        sock = store.get(sock.id); // its Wi-Fi address may have been learned over Bluetooth
        if (sock == null) return;
        boolean wifi = Link.WIFI.equals(link.via()) && sock.base != null && !sock.base.isEmpty();
        ui.dialog("New program").setMessage("Load this program (" + bin.length / 1024 + " kB) into " + sock.label() + "?\n\n"
                        + "Charging pauses while it loads, then the socket restarts.\n\n"
                        + (wifi ? "It goes over Wi-Fi and takes about half a minute."
                        : "It goes over Bluetooth and takes 2 to 4 minutes: keep this device close and the app open."))
                .setPositiveButton("Load", (d, w) -> loadProgram(bin, wifi))
                .setNegativeButton("Cancel", null).show();
    }

    private void loadProgram(byte[] bin, boolean wifi) {
        if (!wifi && Ble.problem(this) != null) {
            note("Not loaded: the socket does not answer over Wi-Fi now, and " + Ble.problem(this) + ".");
            return;
        }
        loading = true;
        update.setEnabled(false);
        update.setAlpha(0.5f);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON); // the phone must not sleep halfway
        String how = wifi ? "Loading over Wi-Fi… " : "Loading over Bluetooth… ";
        note(how + "0 %");
        String base = sock.base, pin = sock.pin, id = sock.id;
        Api.Progress p = (got, all) -> Api.MAIN.post(() -> {
            if (loading) note(how + (int) (got * 100L / all) + " %");
        });
        Api.POOL.execute(() -> {
            String msg;
            if (wifi) {
                Api.Result r = Api.upload(base, pin, bin, p);
                msg = r.ok() ? null : r.needsPin() ? "pin" : r.error;
            } else {
                msg = Ble.get(this, id).upload(bin, p);
            }
            String m = msg;
            Api.MAIN.post(() -> {
                loading = false;
                update.setEnabled(true);
                update.setAlpha(1f);
                getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                if (m == null) note("Loaded. The socket is restarting; it is back in about 15 s.");
                else if (Ble.OLD_PROGRAM.equals(m))
                    note("This socket's program is older than 1.7 and cannot take updates over Bluetooth; load it once over Wi-Fi or USB.");
                else if ("pin".equals(m)) note("Not loaded: the socket refused the PIN. Check the PIN saved in this app.");
                else note("Did not load (the old program keeps running): " + m);
            });
        });
    }

    private void note(String s) {
        updateNote.setText(s);
        updateNote.setVisibility(View.VISIBLE);
    }

    /** The whole file, or null if it is longer than `max` bytes. */
    private static byte[] readAll(InputStream in, int max) throws java.io.IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) > 0) {
            b.write(buf, 0, n);
            if (b.size() > max) return null;
        }
        return b.toByteArray();
    }
}
