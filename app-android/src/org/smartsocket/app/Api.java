package org.smartsocket.app;

import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** HTTP calls to a socket. Every call runs on a worker thread; callbacks come back on the main thread. */
final class Api {
    static final ExecutorService POOL = Executors.newFixedThreadPool(8);
    static final Handler MAIN = new Handler(Looper.getMainLooper());

    static final class Result {
        int code;
        JSONObject json;
        JSONArray arr;
        String error;
        String via;  // Link.WIFI or Link.BLE: how the answer came (null: it did not)
        String hint; // when nothing answered: why not, in plain words

        boolean ok() { return code == 200 && error == null; }
        boolean needsPin() { return code == 401; }
        boolean locked() { return code == 429; }
    }

    interface Callback { void done(Result r); }

    private Api() {}

    static Map<String, String> args(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put(kv[i], kv[i + 1]);
        return m;
    }

    static Result call(String base, String method, String path, Map<String, String> args, String pin, int timeoutMs) {
        Result r = new Result();
        try {
            StringBuilder url = new StringBuilder(base).append(path);
            Map<String, String> all = args == null ? new LinkedHashMap<>() : new LinkedHashMap<>(args);
            if (pin != null && !pin.isEmpty()) all.put("pin", pin);
            char sep = '?';
            for (Map.Entry<String, String> e : all.entrySet()) {
                url.append(sep).append(URLEncoder.encode(e.getKey(), "UTF-8")).append('=')
                        .append(URLEncoder.encode(e.getValue(), "UTF-8"));
                sep = '&';
            }
            HttpURLConnection c = (HttpURLConnection) new URL(url.toString()).openConnection();
            c.setInstanceFollowRedirects(false);
            c.setRequestMethod(method);
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs + 3000);
            c.setUseCaches(false);
            if ("POST".equals(method)) {
                c.setDoOutput(true);
                c.setFixedLengthStreamingMode(0);
                c.getOutputStream().close();
            }
            r.code = c.getResponseCode();
            InputStream in = r.code < 400 ? c.getInputStream() : c.getErrorStream();
            String body = in == null ? "" : read(in).trim();
            c.disconnect();
            if (body.startsWith("[")) r.arr = new JSONArray(body);
            else if (body.startsWith("{")) r.json = new JSONObject(body);
            if (r.json == null && r.arr == null) return notSocket(r); // a school or hotel network's login page
            if (r.code != 200) r.error = r.json != null ? r.json.optString("error", "error " + r.code) : "error " + r.code;
        } catch (Exception e) {
            r.code = -1;
            r.error = "offline";
        }
        return r;
    }

    static void async(String base, String method, String path, Map<String, String> args, String pin, Callback cb) {
        POOL.execute(() -> {
            Result r = call(base, method, path, args, pin, 3000);
            MAIN.post(() -> cb.done(r));
        });
    }

    /** How much of a program has gone out (called on the worker thread). */
    interface Progress { void at(int got, int size); }

    /** Sends a new program (a .bin file) as a multipart upload to /api/update. Call it on a worker thread. */
    static Result upload(String base, String pin, byte[] data, Progress p) {
        Result r = new Result();
        String boundary = "----smartsocket" + System.currentTimeMillis();
        try {
            String url = base + "/api/update" + (pin == null || pin.isEmpty() ? "" : "?pin=" + URLEncoder.encode(pin, "UTF-8"));
            byte[] head = ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"firmware.bin\"\r\n"
                    + "Content-Type: application/octet-stream\r\n\r\n").getBytes(StandardCharsets.UTF_8);
            byte[] tail = ("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8);
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setInstanceFollowRedirects(false);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setConnectTimeout(5000);
            c.setReadTimeout(60000); // the socket checks the program before it answers
            c.setFixedLengthStreamingMode(head.length + data.length + tail.length);
            c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
            try (OutputStream out = c.getOutputStream()) {
                out.write(head);
                for (int i = 0; i < data.length; i += 4096) {
                    int n = Math.min(4096, data.length - i);
                    out.write(data, i, n);
                    if (p != null && ((i / 4096) % 8 == 0 || i + n >= data.length)) p.at(i + n, data.length);
                }
                out.write(tail);
            }
            r.code = c.getResponseCode();
            InputStream in = r.code < 400 ? c.getInputStream() : c.getErrorStream();
            String body = in == null ? "" : read(in).trim();
            if (body.startsWith("{")) r.json = new JSONObject(body);
            if (r.json == null) return notSocket(r);
            if (r.code != 200) r.error = r.json != null ? r.json.optString("error", "error " + r.code) : "error " + r.code;
        } catch (Exception e) {
            r.code = -1;
            r.error = "offline";
        }
        return r;
    }

    /**
     * Every answer from a socket is JSON. Anything else (a redirect such as 307, or a web page) came from
     * the network itself, typically the login page of a school or hotel Wi-Fi: treat the socket as not
     * reachable over Wi-Fi, so the app goes on to Bluetooth instead of showing "error 307".
     */
    private static Result notSocket(Result r) {
        r.code = -1;
        r.json = null;
        r.arr = null;
        r.error = "offline";
        return r;
    }

    static String read(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[2048];
        int n;
        while ((n = in.read(buf)) > 0) b.write(buf, 0, n);
        return b.toString("UTF-8");
    }
}
