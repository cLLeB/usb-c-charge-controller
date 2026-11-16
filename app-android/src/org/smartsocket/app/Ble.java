package org.smartsocket.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.BluetoothStatusCodes;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.location.LocationManager;
import android.os.Build;
import android.os.Looper;
import android.os.ParcelUuid;
import android.os.SystemClock;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.FutureTask;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Bluetooth (BLE) link to the sockets, for networks that do not let devices on the same Wi-Fi see each
 * other (schools, hotels, guest networks). Program 1.6 and later.
 *
 * The socket keeps advertising "SmartSocket-<id>" with the setup service. Its command channel takes one
 * command line written to CMD (the same commands as its USB serial port) and answers on REPLY as
 * notifications, in pieces of (MTU - 3) bytes, ending with "\n". Answers are "#<command> <json>".
 * If the socket has a PIN, a connection must first send "auth <pin>".
 *
 * One scanner is shared by the whole app (Android refuses more than 5 scan starts in 30 s), and there
 * is one GATT connection per socket, kept while it is used and closed after 45 s without commands.
 * Commands to one socket go one at a time. Everything here runs on the main thread, except upload(),
 * which blocks a worker thread while it writes the new program piece by piece (program 1.7).
 */
@SuppressLint("MissingPermission")
final class Ble {
    static final UUID SERVICE = SetupActivity.SERVICE;
    static final UUID CMD = UUID.fromString("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e55");
    static final UUID REPLY = UUID.fromString("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e56");
    static final UUID OTA = UUID.fromString("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e57"); // program 1.7: new program in pieces
    static final String PREFIX = "SmartSocket-";
    static final String OLD_PROGRAM = "old program"; // what upload() says when the socket has no OTA characteristic

    private static final int CONNECT_TIMEOUT_MS = 15000, FIND_TIMEOUT_MS = 10000, IDLE_CLOSE_MS = 45000;

    /** The answer's JSON (what follows "#<command> "), or null and a reason in plain words. */
    interface Reply { void done(String json, String problem); }

    // ================================================================== permissions and the adapter
    static String[] permissions() {
        if (Build.VERSION.SDK_INT >= 31)
            return new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT};
        return new String[]{Manifest.permission.ACCESS_FINE_LOCATION};
    }

    static List<String> missing(Context c) {
        List<String> out = new ArrayList<>();
        for (String p : permissions()) if (c.checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) out.add(p);
        return out;
    }

    static boolean permitted(Context c) { return missing(c).isEmpty(); }

    static BluetoothAdapter adapter(Context c) {
        BluetoothManager bm = (BluetoothManager) c.getSystemService(Context.BLUETOOTH_SERVICE);
        return bm == null ? null : bm.getAdapter();
    }

    /** Null if Bluetooth can be used now, otherwise why not, in plain words. */
    static String problem(Context c) {
        BluetoothAdapter a = adapter(c);
        if (a == null || !c.getPackageManager().hasSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE)) return "this device has no Bluetooth";
        if (!permitted(c)) return "Bluetooth is not allowed for this app";
        if (!a.isEnabled()) return "Bluetooth is off";
        if (Build.VERSION.SDK_INT < 31 && !locationOn(c)) return "Location is off (this Android version needs it to find Bluetooth devices)";
        return null;
    }

    static boolean locationOn(Context c) {
        if (Build.VERSION.SDK_INT >= 28) {
            LocationManager lm = (LocationManager) c.getSystemService(Context.LOCATION_SERVICE);
            return lm == null || lm.isLocationEnabled();
        }
        return android.provider.Settings.Secure.getInt(c.getContentResolver(),
                android.provider.Settings.Secure.LOCATION_MODE, 0) != 0;
    }

    // ================================================================== the shared scanner
    static final class Seen {
        BluetoothDevice dev;
        String id;   // from the advertised name, or learned from "info"; null if not known yet
        String name; // advertised name
        long at;     // SystemClock.elapsedRealtime() when last seen
        int rssi;
    }

    interface Found { void found(Seen s); }

    private static Context app;
    private static final Map<String, Seen> seen = new HashMap<>(); // by Bluetooth address
    private static final List<Found> listeners = new ArrayList<>();
    private static final Map<Object, Long> wants = new HashMap<>(); // who wants a scan, until when
    private static final ArrayDeque<Long> starts = new ArrayDeque<>();
    private static boolean scanning, startPosted;

    private static void init(Context c) {
        if (app == null) app = c.getApplicationContext();
    }

    private static SharedPreferences prefs() { return app.getSharedPreferences("ble", Context.MODE_PRIVATE); }

    /** Bluetooth address -> socket id, remembered from earlier connections (for names cut short in the air). */
    private static String knownId(String addr) { return prefs().getString("id_" + addr, null); }

    private static String knownAddr(String id) { return prefs().getString("addr_" + id.toUpperCase(), null); }

    private static void learned(String addr, String id) {
        if (addr == null || id == null || id.isEmpty()) return;
        id = id.toUpperCase();
        prefs().edit().putString("id_" + addr, id).putString("addr_" + id, addr).apply();
        Seen s = seen.get(addr);
        if (s != null && s.id == null) {
            s.id = id;
            for (Found f : new ArrayList<>(listeners)) f.found(s);
        }
    }

    static void addListener(Found f) {
        if (!listeners.contains(f)) listeners.add(f);
    }

    static void removeListener(Found f) { listeners.remove(f); }

    /** Keeps scanning for at least `ms` on behalf of `owner` (asking again renews it). */
    static void want(Context c, Object owner, long ms) {
        init(c);
        wants.put(owner, SystemClock.elapsedRealtime() + ms);
        startScan();
    }

    static void release(Object owner) { wants.remove(owner); }

    /** The socket with this id, if it was seen in the last `maxAgeMs`. */
    static Seen seenId(String id, long maxAgeMs) {
        Seen best = null;
        long now = SystemClock.elapsedRealtime();
        for (Seen s : seen.values())
            if (id.equalsIgnoreCase(s.id) && now - s.at <= maxAgeMs && (best == null || s.at > best.at)) best = s;
        return best;
    }

    private static List<Seen> unknownNearby() {
        List<Seen> out = new ArrayList<>();
        long now = SystemClock.elapsedRealtime();
        for (Seen s : seen.values()) if (s.id == null && now - s.at < 60000) out.add(s);
        return out;
    }

    private static void startScan() {
        if (scanning || wants.isEmpty() || problem(app) != null) return;
        // Android quietly ignores a sixth start within 30 s: wait for a free slot instead.
        long now = SystemClock.elapsedRealtime();
        while (!starts.isEmpty() && now - starts.peekFirst() > 30000) starts.pollFirst();
        if (starts.size() >= 4) {
            if (!startPosted) {
                startPosted = true;
                Api.MAIN.postDelayed(() -> {
                    startPosted = false;
                    startScan();
                }, 30000 - (now - starts.peekFirst()) + 200);
            }
            return;
        }
        BluetoothAdapter a = adapter(app);
        BluetoothLeScanner sc = a == null ? null : a.getBluetoothLeScanner();
        if (sc == null) return;
        try {
            List<ScanFilter> filters = Collections.singletonList(new ScanFilter.Builder().setServiceUuid(new ParcelUuid(SERVICE)).build());
            ScanSettings set = new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build();
            sc.startScan(filters, set, scanCb);
            scanning = true;
            starts.add(now);
            Api.MAIN.postDelayed(tick, 1000);
        } catch (Exception ignored) {
        }
    }

    private static void stopScan() {
        if (!scanning) return;
        scanning = false;
        try {
            BluetoothAdapter a = adapter(app);
            if (a != null && a.isEnabled() && a.getBluetoothLeScanner() != null) a.getBluetoothLeScanner().stopScan(scanCb);
        } catch (Exception ignored) {
        }
    }

    private static final Runnable tick = new Runnable() {
        public void run() {
            long now = SystemClock.elapsedRealtime();
            for (Iterator<Map.Entry<Object, Long>> it = wants.entrySet().iterator(); it.hasNext(); )
                if (it.next().getValue() < now) it.remove();
            if (wants.isEmpty() || problem(app) != null) {
                stopScan();
                return;
            }
            Api.MAIN.postDelayed(this, 1000);
        }
    };

    private static final ScanCallback scanCb = new ScanCallback() {
        @Override
        public void onScanResult(int type, ScanResult r) {
            BluetoothDevice d = r.getDevice();
            String n = r.getScanRecord() != null ? r.getScanRecord().getDeviceName() : null;
            if (n == null) {
                try {
                    n = d.getName();
                } catch (Exception ignored) {
                }
            }
            String name = n;
            int rssi = r.getRssi();
            Api.MAIN.post(() -> onSeen(d, name, rssi));
        }

        @Override
        public void onBatchScanResults(List<ScanResult> rs) {
            for (ScanResult r : rs) onScanResult(0, r);
        }

        @Override
        public void onScanFailed(int code) {
            Api.MAIN.post(() -> scanning = false);
        }
    };

    private static void onSeen(BluetoothDevice d, String name, int rssi) {
        String addr = d.getAddress();
        Seen s = seen.get(addr);
        boolean isNew = s == null;
        if (isNew) {
            s = new Seen();
            seen.put(addr, s);
        }
        long before = s.at;
        s.dev = d;
        s.at = SystemClock.elapsedRealtime();
        s.rssi = rssi;
        if (name != null) s.name = name;
        String id = name != null && name.startsWith(PREFIX) && name.length() > PREFIX.length() ? name.substring(PREFIX.length()).toUpperCase() : null;
        if (id == null) id = knownId(addr);
        boolean newId = id != null && !id.equals(s.id);
        if (id != null) s.id = id;
        if (isNew || newId || s.at - before > 3000) // not for every advertisement: they come several times a second
            for (Found f : new ArrayList<>(listeners)) f.found(s);
    }

    // ================================================================== one connection per socket
    private static final Map<String, Ble> conns = new HashMap<>();

    static Ble get(Context c, String id) {
        init(c);
        String key = id.toUpperCase();
        Ble b = conns.get(key);
        if (b == null) {
            b = new Ble(key);
            conns.put(key, b);
        }
        return b;
    }

    private static final int IDLE = 0, FINDING = 1, CONNECTING = 2, READY = 3;

    private static final class Job {
        String line, cmd, arg;
        int timeoutMs, tries;
        Reply cb;     // null for the link's own commands (info, auth)
        Runnable timeout;
    }

    private final String id;
    private int state = IDLE;
    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic cmdCh, otaCh; // otaCh is null before program 1.7
    private int mtu = 23;
    private boolean uploading; // the OTA pieces own the link: no command goes out meanwhile
    private final LinkedBlockingQueue<Integer> otaAck = new LinkedBlockingQueue<>(); // write results for upload()
    private String addr;
    private final ArrayDeque<Job> queue = new ArrayDeque<>();
    private Job inFlight;
    private final ByteArrayOutputStream buf = new ByteArrayOutputStream();
    private boolean pinSet;
    private String authedPin, badPin;
    private long lastUse, failedAt = -100000;
    private int connectFails;
    private String lastProblem;
    private List<Seen> candidates; // sockets whose name was not readable, to ask "info" in turn
    private boolean probing, usedSavedAddr;
    private Found finder;
    private final Runnable connectTimeout = () -> linkFailed("no answer over Bluetooth");
    private final Runnable findTimeout = this::notFound;
    private final Runnable idleClose = () -> {
        if (!uploading && inFlight == null && queue.isEmpty() && SystemClock.elapsedRealtime() - lastUse >= IDLE_CLOSE_MS - 1000) close();
    };

    private Ble(String id) { this.id = id; }

    boolean connected() { return state == READY; }

    String lastProblem() { return lastProblem; }

    /** Sends one command line (e.g. "config mode=on"); the answer comes back on the main thread. */
    void command(String line, int timeoutMs, Reply cb) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            Api.MAIN.post(() -> command(line, timeoutMs, cb));
            return;
        }
        Job j = job(line, timeoutMs, cb);
        queue.add(j);
        pump();
    }

    private static Job job(String line, int timeoutMs, Reply cb) {
        Job j = new Job();
        j.line = line.trim();
        int sp = j.line.indexOf(' ');
        j.cmd = sp < 0 ? j.line : j.line.substring(0, sp);
        j.arg = sp < 0 ? "" : j.line.substring(sp + 1).trim();
        j.timeoutMs = timeoutMs;
        j.cb = cb;
        return j;
    }

    /** Commands the socket answers without the PIN. */
    private static boolean free(String cmd) {
        return cmd.equals("info") || cmd.equals("join") || cmd.equals("identify") || cmd.equals("setup") || cmd.equals("auth");
    }

    private String storedPin() {
        Store.Socket s = new Store(app).get(id);
        if (s == null) {
            for (Store.Socket x : new Store(app).list()) if (x.id.equalsIgnoreCase(id)) s = x;
        }
        return s == null || s.pin == null ? "" : s.pin;
    }

    private void pump() {
        lastUse = SystemClock.elapsedRealtime();
        if (inFlight != null || uploading) return;
        if (queue.isEmpty()) {
            Api.MAIN.removeCallbacks(idleClose);
            Api.MAIN.postDelayed(idleClose, IDLE_CLOSE_MS);
            return;
        }
        String p = problem(app);
        if (p != null) {
            close();
            failAll(p);
            return;
        }
        if (state == IDLE) {
            begin();
            return;
        }
        if (state != READY) return;
        Job next = queue.peek();
        if (pinSet && next.cb != null && !free(next.cmd)) {
            String pin = storedPin();
            if (!pin.isEmpty() && !pin.equals(authedPin) && !pin.equals(badPin)) {
                send(job("auth " + pin, 5000, null));
                return;
            }
        }
        send(queue.poll());
    }

    private void send(Job j) {
        inFlight = j;
        buf.reset();
        byte[] v = j.line.getBytes(StandardCharsets.UTF_8);
        boolean ok;
        try {
            if (Build.VERSION.SDK_INT >= 33) {
                ok = gatt.writeCharacteristic(cmdCh, v, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS;
            } else {
                cmdCh.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
                cmdCh.setValue(v);
                ok = gatt.writeCharacteristic(cmdCh);
            }
        } catch (Exception e) {
            ok = false;
        }
        if (!ok) {
            linkFailed("could not send over Bluetooth");
            return;
        }
        Job sent = j;
        j.timeout = () -> {
            if (inFlight == sent) linkFailed("no answer over Bluetooth");
        };
        Api.MAIN.postDelayed(j.timeout, j.timeoutMs);
    }

    // ------------------------------------------------------------------ finding and connecting
    private void begin() {
        long now = SystemClock.elapsedRealtime();
        Seen s = seenId(id, 5 * 60000);
        if (s == null && now - failedAt < 15000) { // just looked for it: do not wait again
            failAll(lastProblem != null ? lastProblem : "not in Bluetooth reach");
            return;
        }
        candidates = null;
        probing = false;
        usedSavedAddr = false;
        if (s != null) {
            connect(s.dev);
            return;
        }
        state = FINDING;
        finder = x -> {
            if (state == FINDING && id.equalsIgnoreCase(x.id)) {
                Api.MAIN.removeCallbacks(findTimeout);
                stopFinding();
                connect(x.dev);
            }
        };
        addListener(finder);
        want(app, this, FIND_TIMEOUT_MS + 2000);
        Api.MAIN.postDelayed(findTimeout, FIND_TIMEOUT_MS);
    }

    private void stopFinding() {
        if (finder != null) removeListener(finder);
        finder = null;
        release(this);
    }

    /** Not seen by name: ask the sockets whose name was cut short, then try the address it had last time. */
    private void notFound() {
        if (state != FINDING) return;
        stopFinding();
        candidates = unknownNearby();
        probing = true;
        nextCandidate();
    }

    private void nextCandidate() {
        if (candidates != null && !candidates.isEmpty()) {
            connect(candidates.remove(0).dev);
            return;
        }
        String saved = knownAddr(id);
        BluetoothAdapter a = adapter(app);
        if (!usedSavedAddr && saved != null && a != null && BluetoothAdapter.checkBluetoothAddress(saved)) {
            usedSavedAddr = true;
            connect(a.getRemoteDevice(saved));
            return;
        }
        state = IDLE;
        probing = false;
        failedAt = SystemClock.elapsedRealtime();
        lastProblem = "not in Bluetooth reach";
        failAll(lastProblem);
    }

    private void connect(BluetoothDevice d) {
        state = CONNECTING;
        addr = d.getAddress();
        buf.reset();
        Api.MAIN.removeCallbacks(connectTimeout);
        Api.MAIN.postDelayed(connectTimeout, CONNECT_TIMEOUT_MS);
        try {
            gatt = d.connectGatt(app, false, new Callback(), BluetoothDevice.TRANSPORT_LE);
        } catch (Exception e) {
            gatt = null;
        }
        if (gatt == null) linkFailed("could not connect over Bluetooth");
    }

    /** The link is up and its notifications are on: check it is the right socket. */
    private void hello() {
        send(job("info", 5000, null));
    }

    private void onReady() {
        Api.MAIN.removeCallbacks(connectTimeout);
        state = READY;
        probing = false;
        connectFails = 0;
        lastProblem = null;
        failedAt = -100000;
        pump();
    }

    // ------------------------------------------------------------------ answers
    private void onPiece(byte[] v) {
        if (v == null) return;
        buf.write(v, 0, v.length);
        byte[] all = buf.toByteArray();
        int start = 0;
        for (int i = 0; i < all.length; i++) {
            if (all[i] != '\n') continue;
            String line = new String(all, start, i - start, StandardCharsets.UTF_8).trim();
            start = i + 1;
            if (!line.isEmpty()) onLine(line);
        }
        buf.reset();
        if (start < all.length) buf.write(all, start, all.length - start);
    }

    private void onLine(String line) {
        Job j = inFlight;
        if (j == null || !line.startsWith("#")) return;
        int sp = line.indexOf(' ');
        String cmd = sp < 0 ? line.substring(1) : line.substring(1, sp);
        if (!cmd.equals(j.cmd)) return; // a late answer to an earlier command
        String json = sp < 0 ? "{}" : line.substring(sp + 1).trim();
        Api.MAIN.removeCallbacks(j.timeout);
        inFlight = null;
        if (j.cb == null) {
            internal(j, json);
            return;
        }
        if (!json.contains("\"error\"")) {
            if (j.cmd.equals("pin")) { // the connection stays signed in with the new PIN
                authedPin = j.arg;
                badPin = null;
                pinSet = !j.arg.isEmpty();
            }
        } else if (json.contains("\"pin\"")) {
            authedPin = null;
        }
        j.cb.done(json, null);
        pump();
    }

    private void internal(Job j, String json) {
        JSONObject o;
        try {
            o = new JSONObject(json);
        } catch (Exception e) {
            o = new JSONObject();
        }
        if (j.cmd.equals("info")) {
            String got = o.optString("id");
            learned(addr, got);
            if (!id.equalsIgnoreCase(got)) { // another socket: try the next one
                closeGatt();
                Api.MAIN.removeCallbacks(connectTimeout);
                nextCandidate();
                return;
            }
            pinSet = o.optBoolean("pin_set");
            authedPin = pinSet ? null : "";
            onReady();
        } else if (j.cmd.equals("auth")) {
            if (o.optBoolean("ok")) {
                authedPin = j.arg;
                badPin = null;
            } else {
                badPin = j.arg; // do not try it again; the next command reports "pin" to the screen
                authedPin = null;
            }
            pump();
        }
    }

    // ------------------------------------------------------------------ new program (program 1.7)
    /**
     * Loads a new program: "ota begin <size> <md5>", the file in pieces of (MTU - 3) bytes written with
     * response to OTA, each after the last one's answer, then "ota end", which checks it and restarts the
     * socket. Blocks for minutes: call it on a worker thread. Returns null when done, otherwise why not
     * (OLD_PROGRAM when the socket has no OTA characteristic). A failure leaves the old program running.
     */
    String upload(byte[] data, Api.Progress p) {
        if (Looper.myLooper() == Looper.getMainLooper()) throw new IllegalStateException("Ble.upload blocks: use a worker thread");
        try {
            String[] a = ask("info", 6000); // connects; the OTA characteristic is known after that
            if (a[0] == null) return a[1];
            if (onMain(() -> otaCh == null)) return OLD_PROGRAM;
            a = ask("ota begin " + data.length + " " + md5(data), 20000); // the PIN goes first if there is one
            if (a[0] == null) return a[1];
            if (!a[0].contains("\"active\":true")) return error(a[0]);
            String fail = pieces(data, p);
            if (fail != null) {
                ask("ota abort", 3000);
                return fail;
            }
            a = ask("ota end", 30000);
            if (a[0] == null) return a[1];
            if (!a[0].contains("\"ok\":true")) return error(a[0]);
            Api.MAIN.post(this::close); // it restarts now
            return null;
        } catch (Exception e) {
            return "stopped (" + e.getMessage() + ")";
        }
    }

    /** Writes the program, keeping the link to itself meanwhile. Null when all of it went, otherwise why not. */
    private String pieces(byte[] data, Api.Progress p) throws Exception {
        onMain(() -> uploading = true);
        try {
            for (int i = 0; ; i++) { // a status request may still be on its way: let it finish first
                int s = onMain(() -> inFlight != null ? 1 : state == READY && gatt != null && otaCh != null ? 0 : 2);
                if (s == 0) break;
                if (s == 2 || i >= 200) return "the Bluetooth link dropped";
                Thread.sleep(50);
            }
            BluetoothGatt g = onMain(() -> gatt);
            BluetoothGattCharacteristic ch = onMain(() -> otaCh);
            onMain(() -> g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)); // short intervals while it goes
            int chunk = Math.max(20, Math.min(509, onMain(() -> mtu) - 3));
            // Most pieces go without waiting for the socket's answer (program 1.7.2 and later); every 16th
            // and the last wait for it, which keeps the phone from running ahead of the socket's flash writes.
            boolean noResp = (ch.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE) != 0;
            for (int i = 0; i < data.length; i += chunk) {
                int n = Math.min(chunk, data.length - i);
                boolean sync = !noResp || (i / chunk) % 16 == 15 || i + n >= data.length;
                otaAck.clear();
                if (!writeOta(g, ch, Arrays.copyOfRange(data, i, i + n),
                        sync ? BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT : BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE))
                    return "could not send over Bluetooth";
                Integer st = otaAck.poll(10, TimeUnit.SECONDS);
                if (st == null) return "no answer over Bluetooth";
                if (st == -1) return "the Bluetooth link dropped";
                if (st != BluetoothGatt.GATT_SUCCESS) return "the socket did not take a piece (status " + st + ")";
                if (p != null && ((i / chunk) % 25 == 0 || i + n >= data.length)) p.at(i + n, data.length);
            }
            return null;
        } finally {
            Api.MAIN.post(() -> {
                uploading = false;
                pump();
            });
        }
    }

    private static boolean writeOta(BluetoothGatt g, BluetoothGattCharacteristic ch, byte[] v, int type) throws InterruptedException {
        for (int t = 0; t < 20; t++) {
            boolean ok;
            try {
                if (Build.VERSION.SDK_INT >= 33) {
                    ok = g.writeCharacteristic(ch, v, type) == BluetoothStatusCodes.SUCCESS;
                } else {
                    ch.setWriteType(type);
                    ch.setValue(v);
                    ok = g.writeCharacteristic(ch);
                }
            } catch (Exception e) {
                ok = false;
            }
            if (ok) return true;
            Thread.sleep(25); // the stack may still be finishing the last write
        }
        return false;
    }

    /** command() for a worker thread: waits for the answer. Returns {json, null} or {null, why}. */
    private String[] ask(String line, int timeoutMs) throws InterruptedException {
        String[] out = new String[2];
        CountDownLatch done = new CountDownLatch(1);
        command(line, timeoutMs, (json, problem) -> {
            out[0] = json;
            out[1] = problem;
            done.countDown();
        });
        // finding, connecting and one retry on a new link all fit in this
        if (!done.await(timeoutMs * 2L + 90000, TimeUnit.MILLISECONDS)) out[1] = "no answer over Bluetooth";
        return out;
    }

    /** Runs `c` on the main thread (where this object's state lives) and waits for its result. */
    private static <T> T onMain(Callable<T> c) throws Exception {
        FutureTask<T> f = new FutureTask<>(c);
        Api.MAIN.post(f);
        return f.get(10, TimeUnit.SECONDS);
    }

    private static String md5(byte[] data) throws Exception {
        StringBuilder b = new StringBuilder();
        for (byte x : MessageDigest.getInstance("MD5").digest(data)) b.append(String.format("%02x", x & 0xff));
        return b.toString();
    }

    /** {"error":"size"} -> "size"; anything else as it came. */
    private static String error(String json) {
        try {
            String e = new JSONObject(json).optString("error");
            return e.isEmpty() ? json : e;
        } catch (Exception ex) {
            return json;
        }
    }

    // ------------------------------------------------------------------ failures and closing
    private void linkFailed(String why) {
        Api.MAIN.removeCallbacks(connectTimeout);
        Job j = inFlight;
        inFlight = null;
        if (j != null) Api.MAIN.removeCallbacks(j.timeout);
        closeGatt();
        state = IDLE;
        lastProblem = why;
        if (probing) { // that one did not answer: ask the next one
            nextCandidate();
            return;
        }
        if (j != null && j.cb != null && ++j.tries < 2) queue.addFirst(j); // send it again on the new link
        if (queue.isEmpty()) return;
        if (++connectFails >= 3) {
            connectFails = 0;
            failedAt = SystemClock.elapsedRealtime();
            failAll(why);
            return;
        }
        Api.MAIN.postDelayed(this::pump, 800);
    }

    private void failAll(String why) {
        List<Job> jobs = new ArrayList<>(queue);
        queue.clear();
        if (inFlight != null) {
            Api.MAIN.removeCallbacks(inFlight.timeout);
            jobs.add(0, inFlight);
            inFlight = null;
        }
        for (Job j : jobs) if (j.cb != null) j.cb.done(null, why);
    }

    private void closeGatt() {
        BluetoothGatt g = gatt;
        gatt = null;
        cmdCh = null;
        otaCh = null;
        mtu = 23;
        buf.reset();
        otaAck.offer(-1); // an upload waiting for a piece's answer: the link is gone
        if (g != null) {
            try {
                g.disconnect();
            } catch (Exception ignored) {
            }
            try {
                g.close();
            } catch (Exception ignored) {
            }
        }
    }

    /** Ends the connection (it opens again by itself on the next command). */
    void close() {
        stopFinding();
        Api.MAIN.removeCallbacks(findTimeout);
        Api.MAIN.removeCallbacks(connectTimeout);
        closeGatt();
        state = IDLE;
        probing = false;
    }

    // ------------------------------------------------------------------ GATT events (on a Bluetooth thread: moved to the main one)
    private final class Callback extends BluetoothGattCallback {
        @Override
        public void onConnectionStateChange(BluetoothGatt g, int status, int newState) {
            Api.MAIN.post(() -> {
                if (g != gatt) {
                    if (newState == BluetoothProfile.STATE_DISCONNECTED) g.close();
                    return;
                }
                if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                    if (!g.requestMtu(247)) g.discoverServices();
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED || status != BluetoothGatt.GATT_SUCCESS) {
                    linkFailed(state == READY ? "the Bluetooth link dropped" : "could not connect over Bluetooth");
                }
            });
        }

        @Override
        public void onMtuChanged(BluetoothGatt g, int mtu, int status) {
            Api.MAIN.post(() -> {
                if (g == gatt && status == BluetoothGatt.GATT_SUCCESS) Ble.this.mtu = mtu;
                if (g == gatt && !g.discoverServices()) linkFailed("could not connect over Bluetooth");
            });
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt g, int status) {
            Api.MAIN.post(() -> {
                if (g != gatt) return;
                BluetoothGattService svc = g.getService(SERVICE);
                BluetoothGattCharacteristic cmd = svc == null ? null : svc.getCharacteristic(CMD);
                BluetoothGattCharacteristic reply = svc == null ? null : svc.getCharacteristic(REPLY);
                if (cmd == null || reply == null) {
                    // A socket with program 1.5 has the setup service but no command channel.
                    Api.MAIN.removeCallbacks(connectTimeout);
                    closeGatt();
                    state = IDLE;
                    failedAt = SystemClock.elapsedRealtime();
                    lastProblem = "its program is older than 1.6 (Bluetooth control needs 1.6)";
                    failAll(lastProblem);
                    return;
                }
                cmdCh = cmd;
                otaCh = svc.getCharacteristic(OTA);
                g.setCharacteristicNotification(reply, true);
                BluetoothGattDescriptor d = reply.getDescriptor(SetupActivity.CCCD);
                boolean ok;
                if (d == null) ok = false;
                else if (Build.VERSION.SDK_INT >= 33)
                    ok = g.writeDescriptor(d, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE) == BluetoothStatusCodes.SUCCESS;
                else {
                    d.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                    ok = g.writeDescriptor(d);
                }
                if (!ok) linkFailed("could not connect over Bluetooth");
            });
        }

        @Override
        public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor d, int status) {
            Api.MAIN.post(() -> {
                if (g != gatt) return;
                if (status != BluetoothGatt.GATT_SUCCESS) linkFailed("could not connect over Bluetooth");
                else hello();
            });
        }

        @Override
        public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic c, int status) {
            if (OTA.equals(c.getUuid())) { // a program piece: upload() waits for this before the next one
                otaAck.offer(status);
                return;
            }
            if (status == BluetoothGatt.GATT_SUCCESS) return;
            Api.MAIN.post(() -> {
                if (g == gatt) linkFailed("the socket did not take the command");
            });
        }

        // Android 13 and later
        @Override
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c, byte[] value) {
            if (!REPLY.equals(c.getUuid())) return;
            byte[] v = value == null ? null : value.clone();
            Api.MAIN.post(() -> {
                if (g == gatt) onPiece(v);
            });
        }

        // Android 12 and earlier
        @Override
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c) {
            if (!REPLY.equals(c.getUuid())) return;
            byte[] v = c.getValue() == null ? null : c.getValue().clone();
            Api.MAIN.post(() -> {
                if (g == gatt) onPiece(v);
            });
        }
    }
}
