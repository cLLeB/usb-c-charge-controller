package org.smartsocket.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.ParcelUuid;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Sets up a new socket over Bluetooth: no switching Wi-Fi networks, no addresses.
 * The socket advertises "SmartSocket-xxxxxx" while it is in setup (first start, or after holding its
 * button for 3 s). The app reads the Wi-Fi networks the socket can see, the user picks one and types
 * its password, a name and a PIN, and the socket reports when it has joined.
 */
@SuppressLint("MissingPermission")
public class SetupActivity extends Activity {
    static final UUID SERVICE = UUID.fromString("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e50");
    static final UUID INFO = UUID.fromString("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e51");
    static final UUID NETS = UUID.fromString("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e52");
    static final UUID SETUP = UUID.fromString("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e53");
    static final UUID RESULT = UUID.fromString("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e54");
    static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");

    private Ui ui;
    private Store store;
    private LinearLayout col, found, form;
    private TextView status;
    private final Map<String, BluetoothDevice> devices = new LinkedHashMap<>();
    private BluetoothAdapter adapter;
    private BluetoothGatt gatt;
    private JSONObject info;
    private Spinner network;
    private EditText otherNetwork, pass, name, pin, oldPin;
    private boolean scanning;
    private boolean joining; // details sent, waiting for the socket's answer
    private int joinTry;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        ui = new Ui(this);
        store = new Store(this);
        col = ui.page(this, "New socket");
        LinearLayout c = ui.card(col);
        c.addView(ui.text("1. Plug the socket into a charger.\n2. A new socket opens its setup by itself (fast blinking light). "
                + "An old one: hold its button for 3 s until the light stays on, then let go.\n3. Keep this phone close to it.", 15, ui.ink, false));
        status = ui.text("Looking for sockets nearby…", 15, ui.dim, false);
        status.setPadding(0, ui.dp(12), 0, ui.dp(4));
        col.addView(status);
        found = new LinearLayout(this);
        found.setOrientation(LinearLayout.VERTICAL);
        col.addView(found);
        form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        col.addView(form);

        LinearLayout other = ui.card(col);
        other.addView(ui.label("Other ways"));
        other.addView(ui.text("• WPS: press the socket's button 3 times quickly, then the WPS button on your router (Livebox: the button marked WPS).\n"
                + "• Setup network: join “SmartSocket-setup-…” in this phone's Wi-Fi settings; the setup page opens by itself.\n"
                + "• Computer: with the socket on USB, the Windows app has a “Set up over USB” button.", 14, ui.ink, false));
        startScan();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        stopScan();
        if (gatt != null) gatt.close();
    }

    // ------------------------------------------------------------------ finding sockets
    private void startScan() {
        List<String> need = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 31) {
            Collections.addAll(need, Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT);
        } else {
            need.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }
        List<String> missing = new ArrayList<>();
        for (String p : need) if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) missing.add(p);
        if (!missing.isEmpty()) {
            requestPermissions(missing.toArray(new String[0]), 2);
            return;
        }
        BluetoothManager bm = (BluetoothManager) getSystemService(BLUETOOTH_SERVICE);
        adapter = bm == null ? null : bm.getAdapter();
        if (adapter == null) {
            status.setText("This device has no Bluetooth. Use WPS or the setup network below.");
            return;
        }
        if (!adapter.isEnabled()) {
            status.setText("Turn Bluetooth on, then come back to this screen.");
            startActivity(new Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE));
            return;
        }
        List<ScanFilter> filters = Collections.singletonList(new ScanFilter.Builder().setServiceUuid(new ParcelUuid(SERVICE)).build());
        ScanSettings s = new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build();
        adapter.getBluetoothLeScanner().startScan(filters, s, scanCb);
        scanning = true;
        Api.MAIN.postDelayed(() -> {
            if (scanning && devices.isEmpty()) status.setText("No socket in setup nearby yet. Is its light blinking fast?");
        }, 12000);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        for (int r : res) {
            if (r != PackageManager.PERMISSION_GRANTED) {
                status.setText("Bluetooth permission is needed for this way of setting up. The other ways below work without it.");
                return;
            }
        }
        startScan();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!scanning && gatt == null && adapter != null && adapter.isEnabled()) startScan();
    }

    private void stopScan() {
        if (scanning && adapter != null && adapter.isEnabled()) adapter.getBluetoothLeScanner().stopScan(scanCb);
        scanning = false;
    }

    private final ScanCallback scanCb = new ScanCallback() {
        @Override
        public void onScanResult(int type, ScanResult r) {
            BluetoothDevice d = r.getDevice();
            if (devices.containsKey(d.getAddress())) return;
            devices.put(d.getAddress(), d);
            String n = r.getScanRecord() != null && r.getScanRecord().getDeviceName() != null ? r.getScanRecord().getDeviceName() : "SmartSocket";
            status.setText("Tap the socket to set up:");
            found.addView(ui.button(n, true, v -> connect(d, n)), ui.wide());
        }
    };

    // ------------------------------------------------------------------ talking to it over Bluetooth
    private void connect(BluetoothDevice d, String label) {
        stopScan();
        found.removeAllViews();
        status.setText("Connecting to " + label + "…");
        gatt = d.connectGatt(this, false, gattCb, BluetoothDevice.TRANSPORT_LE);
    }

    private BluetoothGattCharacteristic ch(UUID id) {
        BluetoothGattService s = gatt == null ? null : gatt.getService(SERVICE);
        return s == null ? null : s.getCharacteristic(id);
    }

    private String value(BluetoothGattCharacteristic c) {
        byte[] v = c.getValue();
        return v == null ? "" : new String(v, StandardCharsets.UTF_8);
    }

    private final BluetoothGattCallback gattCb = new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(BluetoothGatt g, int s, int state) {
            if (state == BluetoothProfile.STATE_CONNECTED) {
                g.requestMtu(247);
            } else if (state == BluetoothProfile.STATE_DISCONNECTED) {
                Api.MAIN.post(() -> {
                    if (info == null) status.setText("Lost the connection. Go back and try again, closer to the socket.");
                    else if (joining) { // the socket's radio is busy joining: link up again and ask for the answer
                        status.setText("Bluetooth dropped while it joins. Linking up again…");
                        Api.MAIN.postDelayed(() -> { if (joining && gatt != null) gatt.connect(); }, 1500);
                    }
                });
            }
        }

        @Override
        public void onMtuChanged(BluetoothGatt g, int mtu, int s) { g.discoverServices(); }

        @Override
        public void onServicesDiscovered(BluetoothGatt g, int s) {
            if (joining && info != null && ch(RESULT) != null) {
                g.readCharacteristic(ch(RESULT));
                return;
            }
            BluetoothGattCharacteristic c = ch(INFO);
            if (c == null) {
                Api.MAIN.post(() -> status.setText("This is not a Smart Socket."));
                return;
            }
            g.readCharacteristic(c);
        }

        @Override
        public void onCharacteristicRead(BluetoothGatt g, BluetoothGattCharacteristic c, int s) {
            String v = value(c);
            if (INFO.equals(c.getUuid())) {
                try {
                    info = new JSONObject(v);
                } catch (Exception e) {
                    info = new JSONObject();
                }
                g.readCharacteristic(ch(NETS));
            } else if (NETS.equals(c.getUuid())) {
                Api.MAIN.post(() -> showForm(v));
                BluetoothGattCharacteristic res = ch(RESULT);
                g.setCharacteristicNotification(res, true);
                BluetoothGattDescriptor d = res.getDescriptor(CCCD);
                if (d != null) {
                    d.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                    g.writeDescriptor(d);
                }
            } else if (RESULT.equals(c.getUuid())) {
                Api.MAIN.post(() -> onJoinResult(v));
            }
        }

        @Override
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c) {
            String v = value(c);
            Api.MAIN.post(() -> onJoinResult(v));
        }

        @Override
        public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic c, int s) {
            if (s != BluetoothGatt.GATT_SUCCESS) Api.MAIN.post(() -> status.setText("The socket did not accept the details. Try again."));
        }
    };

    private void showForm(String netsJson) {
        status.setText("Connected to " + info.optString("name", "the socket") + ".");
        form.removeAllViews();
        LinearLayout c = ui.card(form);
        c.addView(ui.label("Home Wi-Fi"));
        List<String> names = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(netsJson);
            for (int i = 0; i < a.length(); i++) names.add(a.getJSONObject(i).getString("ssid"));
        } catch (Exception ignored) {
        }
        names.add("Another network (type its name)");
        network = new Spinner(this);
        network.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, names));
        c.addView(network);
        otherNetwork = ui.field("Network name", false, false);
        c.addView(otherNetwork);
        otherNetwork.setVisibility(names.size() == 1 ? View.VISIBLE : View.GONE);
        network.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            public void onItemSelected(android.widget.AdapterView<?> p, View v, int pos, long id) {
                otherNetwork.setVisibility(pos == names.size() - 1 ? View.VISIBLE : View.GONE);
            }
            public void onNothingSelected(android.widget.AdapterView<?> p) {}
        });
        pass = ui.field("Wi-Fi password", true, false);
        c.addView(pass);

        c = ui.card(form);
        c.addView(ui.label("This socket"));
        name = ui.field("Name, e.g. Bedroom", false, false);
        name.setText(info.optString("name"));
        c.addView(name);
        pin = ui.field("New PIN, 4 to 12 digits (recommended)", true, true);
        c.addView(pin);
        if (info.optBoolean("pin_set")) {
            oldPin = ui.field("Its current PIN", true, true);
            c.addView(oldPin);
        }
        form.addView(ui.button("Connect it to Wi-Fi", true, v -> send(names)), ui.wide());
    }

    private void send(List<String> names) {
        int pos = network.getSelectedItemPosition();
        String ssid = pos == names.size() - 1 ? otherNetwork.getText().toString().trim() : names.get(pos);
        if (ssid.isEmpty()) {
            ui.toast("Choose the Wi-Fi network");
            return;
        }
        String p = pin.getText().toString().trim();
        if (!p.isEmpty() && (p.length() < 4 || p.length() > 12)) {
            ui.toast("A PIN has 4 to 12 digits");
            return;
        }
        try {
            JSONObject j = new JSONObject();
            j.put("ssid", ssid);
            j.put("pass", pass.getText().toString());
            j.put("name", name.getText().toString().trim());
            j.put("pin", p);
            if (oldPin != null) j.put("old_pin", oldPin.getText().toString().trim());
            BluetoothGattCharacteristic c = ch(SETUP);
            c.setValue(j.toString().getBytes(StandardCharsets.UTF_8));
            c.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
            gatt.writeCharacteristic(c);
            if (!p.isEmpty()) pendingPin = p;
            else if (oldPin != null) pendingPin = oldPin.getText().toString().trim();
            status.setText("Joining " + ssid + "… (up to 20 s)");
            joining = true;
            int t = ++joinTry;
            Api.MAIN.postDelayed(() -> {
                if (joining && t == joinTry) {
                    joining = false;
                    status.setText("No answer from the socket. Go back, keep this device close to it and try again.");
                }
            }, 45000);
            form.setVisibility(View.GONE);
            Api.MAIN.postDelayed(this::readResult, 3000);
        } catch (Exception e) {
            ui.toast("Could not send: " + e.getMessage());
        }
    }

    private String pendingPin = "";

    private void readResult() {
        if (gatt != null && ch(RESULT) != null) gatt.readCharacteristic(ch(RESULT));
    }

    private void onJoinResult(String v) {
        JSONObject j;
        try {
            j = new JSONObject(v);
        } catch (Exception e) {
            return;
        }
        String state = j.optString("state");
        switch (state) {
            case "connecting":
                Api.MAIN.postDelayed(this::readResult, 3000);
                break;
            case "ok": {
                if (j.optString("ip").isEmpty()) { // joined, address not given yet: ask again
                    Api.MAIN.postDelayed(this::readResult, 2000);
                    break;
                }
                joining = false;
                String id = info.optString("id");
                String base = "http://" + j.optString("ip");
                store.put(id, name.getText().toString().trim(), base, j.optString("host"));
                store.setPin(id, pendingPin);
                status.setText("Done. It is on Wi-Fi at " + j.optString("ip") + ".");
                if (gatt != null) gatt.disconnect();
                form.removeAllViews();
                form.setVisibility(View.VISIBLE);
                form.addView(ui.button("Open it", true, v2 -> {
                    startActivity(new Intent(this, DeviceActivity.class).putExtra("id", id));
                    finish();
                }), ui.wide());
                break;
            }
            case "fail":
                joining = false;
                status.setText("It could not join that network (reason " + j.optInt("reason") + "). Check the password and try again.");
                form.setVisibility(View.VISIBLE);
                break;
            case "rejected":
                joining = false;
                status.setText("“" + j.optString("why") + "”: the current PIN is wrong.");
                form.setVisibility(View.VISIBLE);
                break;
            default:
                break;
        }
    }
}
