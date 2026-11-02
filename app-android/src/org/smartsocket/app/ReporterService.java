package org.smartsocket.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ServiceInfo;
import android.os.BatteryManager;
import android.os.Build;
import android.os.IBinder;

import org.json.JSONObject;

import java.util.List;

/**
 * Runs in the background with a small notification. It sends this device's battery level to the
 * socket it is plugged into whenever the level changes, finds that socket by itself when the device
 * is plugged in (if allowed), and tells the user when charging stops or starts.
 *
 * The socket is reached through Link: Wi-Fi when it answers there, otherwise Bluetooth. Status checks and
 * finding the socket work over both. Battery reports, however, are an HTTP call (/api/battery?level=N)
 * and program 1.6 has no Bluetooth command for them: over Bluetooth the app sends "battery <level>",
 * which 1.6 answers with {"error":"unknown command"}. The notification then says so. A program that adds
 * the command (see the note in BATTERY_OVER_BLE below) makes the reports work over Bluetooth with no
 * change here.
 */
public class ReporterService extends Service {
    private static final String CH_RUN = "reporting", CH_EVENT = "events";
    private Store store;
    private int lastLevel = -1;
    private String lastHeadline = "";
    private BroadcastReceiver receiver;
    private boolean bleNoBattery; // the target is only reachable over Bluetooth and its program cannot take reports there

    /*
     * BATTERY_OVER_BLE: the firmware change this needs (smart_socket.ino, runCommand, after the
     * "if (!ok) return fail("pin");" line, i.e. it needs the PIN like the other commands):
     *
     *   if (cmd == "battery") {   // battery <0..100>: the phone's level, same as /api/battery?level=N
     *     if (arg.isEmpty() || arg.toInt() < 0 || arg.toInt() > 100) return fail("level is missing");
     *     reportBattery(arg.toInt(), nowMs());
     *     saveAllowIfChanged();
     *     return answer(statusJson());
     *   }
     *
     * Answer: "#battery <status json>", like "#full". Also list it in the commands comment.
     */

    private final Runnable watch = new Runnable() {
        public void run() {
            checkTarget();
            Api.MAIN.postDelayed(this, 60000);
        }
    };

    static void start(Context c) {
        Intent i = new Intent(c, ReporterService.class);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
        else c.startService(i);
    }

    static void stop(Context c) { c.stopService(new Intent(c, ReporterService.class)); }

    @Override
    public void onCreate() {
        super.onCreate();
        store = new Store(this);
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CH_RUN, "Battery reports", NotificationManager.IMPORTANCE_MIN));
        nm.createNotificationChannel(new NotificationChannel(CH_EVENT, "Charging started or stopped", NotificationManager.IMPORTANCE_DEFAULT));
        Notification n = running("Ready to report this device's battery");
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        else startForeground(1, n);

        receiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent i) {
                String a = i.getAction();
                if (Intent.ACTION_BATTERY_CHANGED.equals(a)) {
                    int level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) * 100 / Math.max(1, i.getIntExtra(BatteryManager.EXTRA_SCALE, 100));
                    if (level != lastLevel) {
                        lastLevel = level;
                        report();
                    }
                } else if (Intent.ACTION_POWER_CONNECTED.equals(a) && store.autoPick()) {
                    // Give the socket a few seconds to see the device and switch on.
                    Api.MAIN.postDelayed(ReporterService.this::pickSocket, 4000);
                }
            }
        };
        IntentFilter f = new IntentFilter(Intent.ACTION_BATTERY_CHANGED);
        f.addAction(Intent.ACTION_POWER_CONNECTED);
        registerReceiver(receiver, f);
        Api.MAIN.postDelayed(watch, 5000);
    }

    @Override
    public int onStartCommand(Intent i, int flags, int id) { return START_STICKY; }

    @Override
    public void onDestroy() {
        super.onDestroy();
        if (receiver != null) unregisterReceiver(receiver);
        Api.MAIN.removeCallbacks(watch);
    }

    @Override
    public IBinder onBind(Intent i) { return null; }

    private void report() {
        Store.Socket s = store.get(store.reportTarget());
        if (s == null || lastLevel < 0) {
            update(null);
            return;
        }
        Link.get(this, s.id).call("battery", Api.args("level", "" + lastLevel), r -> {
            bleNoBattery = Link.BLE.equals(r.via) && "unknown command".equals(r.error);
            if (bleNoBattery) { // program 1.6 over Bluetooth: show the state at least
                Link.get(this, s.id).call("status", null, r2 -> update(r2.ok() ? r2.json : null));
                return;
            }
            update(r.ok() ? r.json : null);
        });
    }

    /** Which socket is this device plugged into? The one that has just seen a device and started charging. */
    private void pickSocket() {
        List<Store.Socket> all = store.list();
        for (Store.Socket s : all) {
            Link.get(this, s.id).call("status", null, r -> {
                if (!r.ok() || r.json == null) return;
                JSONObject st = r.json;
                long charging = st.optLong("charge_s", -1);
                if (st.optBoolean("phone") && charging >= 0 && charging < 30 && !s.id.equals(store.reportTarget())) {
                    store.setReportTarget(s.id);
                    report();
                    event(s.label() + ": found", "This device is plugged into " + s.label() + ". Its battery level is sent there now.");
                }
            });
        }
    }

    private void checkTarget() {
        Store.Socket s = store.get(store.reportTarget());
        if (s == null) return;
        // Every minute the level goes again, not only when it changes: a socket that restarted (or
        // whose limits just changed) knows where the battery is within a minute.
        if (lastLevel >= 0) {
            report();
            return;
        }
        Link.get(this, s.id).call("status", null, r -> update(r.ok() ? r.json : null));
    }

    private void update(JSONObject st) {
        Store.Socket s = store.get(store.reportTarget());
        String text;
        if (s == null) text = store.autoPick() ? "Will find the socket when this device is plugged in" : "No socket chosen";
        else if (st == null) text = s.label() + ": not answering";
        else {
            String h = Ui.headline(st);
            text = s.label() + ": " + h + (lastLevel >= 0 ? " · " + lastLevel + " %" : "");
            if (bleNoBattery) text += " · reached over Bluetooth only: its program (1.6) cannot take battery reports that way";
            if (!lastHeadline.isEmpty() && !h.equals(lastHeadline)) event(s.label() + ": " + h, Ui.detail(st));
            lastHeadline = h;
        }
        getSystemService(NotificationManager.class).notify(1, running(text));
    }

    private Notification running(String text) {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CH_RUN).setSmallIcon(android.R.drawable.ic_lock_idle_charging)
                .setContentTitle("Smart Socket").setContentText(text).setContentIntent(open).setOngoing(true).build();
    }

    private void event(String title, String text) {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CH_EVENT).setSmallIcon(android.R.drawable.ic_lock_idle_charging)
                .setContentTitle(title).setContentText(text).setContentIntent(open).setAutoCancel(true).build();
        getSystemService(NotificationManager.class).notify(2, n);
    }
}
