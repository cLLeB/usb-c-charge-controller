// Smart Socket for Windows 1.5: remote control for the smart charge switch (program 1.5).
// Build with build.cmd (uses the C# compiler that ships with Windows, nothing to install).
//
// SmartSocket.exe                            normal use
// SmartSocket.exe --tray                     start in the notification area (used by "Start with Windows")
// SmartSocket.exe --shot out.png [URL] [tab] connect to URL (or search with "-"), save a picture of the window, exit (testing)
// add --only-wifi, --only-usb, --only-ble (or --no-wifi, --no-usb, --no-ble) to test one way of reaching the sockets
//
// Every socket is reached by Wi-Fi (HTTP), by its USB cable (serial commands) or by Bluetooth (the
// same commands over BLE), whichever answers, preferring them in that order and falling back when one
// stops answering. Program updates need Wi-Fi.
// Every socket on this computer's network is listed with its state, all of them watched at once.
// For the chosen socket: charging limits and modes, charge timer, charging hours, safety hours,
// charging history, name, PIN, light brightness, "find me", program update over Wi-Fi, and setting
// a new socket up over its USB cable. A notification-area icon reports when charging starts or stops.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Ports;
using System.Linq;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

namespace SmartSocket
{
    static class Json
    {
        public static Dictionary<string, object> Parse(string json)
        {
            try { return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json) ?? new Dictionary<string, object>(); }
            catch { return new Dictionary<string, object>(); }
        }
        public static bool B(Dictionary<string, object> d, string k) { return d.ContainsKey(k) && d[k] is bool && (bool)d[k]; }
        public static string S(Dictionary<string, object> d, string k) { return d.ContainsKey(k) && d[k] != null ? d[k].ToString() : ""; }
        public static int I(Dictionary<string, object> d, string k)
        {
            try { return d.ContainsKey(k) && d[k] != null ? Convert.ToInt32(d[k]) : 0; } catch { return 0; }
        }
        public static long L(Dictionary<string, object> d, string k)
        {
            try { return d.ContainsKey(k) && d[k] != null ? Convert.ToInt64(d[k]) : 0; } catch { return 0; }
        }
        public static double D(Dictionary<string, object> d, string k)
        {
            try { return d.ContainsKey(k) && d[k] != null ? Convert.ToDouble(d[k]) : 0; } catch { return 0; }
        }
        // The socket's parser understands \" and \\ only, so nothing else is escaped (accents pass as they are).
        public static string Str(string s) { return "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\""; }
    }

    class Status
    {
        public bool settled, phone, charging, boost, pin_set, sched_on, hot;
        public string mode = "auto", ssid = "", ip = "", fw = "", name = "", reason = "", time = "", host = "", sched_start = "", sched_end = "";
        public int level = -1, level_age_s = -1, upper = 80, lower = 40, max_ma = 500, src_ma = 500, timer_min, timer_left_s = -1;
        public int safety_h, led = 100, charge_s = -1, uptime_s, rssi;
        public double temp_c;

        public static Status Parse(Dictionary<string, object> d)
        {
            var s = new Status();
            s.settled = Json.B(d, "settled"); s.phone = Json.B(d, "phone"); s.charging = Json.B(d, "charging"); s.boost = Json.B(d, "boost");
            s.pin_set = Json.B(d, "pin_set"); s.sched_on = Json.B(d, "sched_on"); s.hot = Json.B(d, "hot");
            s.mode = Json.S(d, "mode"); s.ssid = Json.S(d, "ssid"); s.ip = Json.S(d, "ip"); s.fw = Json.S(d, "fw"); s.host = Json.S(d, "host");
            s.name = Json.S(d, "name"); s.reason = Json.S(d, "reason"); s.time = Json.S(d, "time");
            s.sched_start = Json.S(d, "sched_start"); s.sched_end = Json.S(d, "sched_end");
            s.level = Json.I(d, "level"); s.level_age_s = Json.I(d, "level_age_s"); s.upper = Json.I(d, "upper"); s.lower = Json.I(d, "lower");
            s.max_ma = Json.I(d, "max_ma"); s.src_ma = Json.I(d, "src_ma"); s.safety_h = Json.I(d, "safety_h"); s.led = Json.I(d, "led");
            s.timer_min = Json.I(d, "timer_min"); s.timer_left_s = Json.I(d, "timer_left_s");
            s.charge_s = Json.I(d, "charge_s"); s.uptime_s = Json.I(d, "uptime_s"); s.rssi = Json.I(d, "rssi");
            s.temp_c = Json.D(d, "temp_c");
            return s;
        }
    }

    // One socket found on the network (or typed in by hand), and what was last heard from it.
    class Sock
    {
        public string Id = "", Name = "", Url = "";
        public string Port = "";     // USB serial port ("COM11"), "" if not plugged into this computer
        public ulong Ble;            // Bluetooth address, 0 if not seen
        public string BleName = "";
        public volatile Link Via;    // the link the last answer came by
        public readonly Dictionary<Link, DateTime> Retry = new Dictionary<Link, DateTime>();   // a failed link rests until then
        public readonly Dictionary<Link, string> Failed = new Dictionary<Link, string>();      // why it failed (lock Retry)
        public Status Last;          // last good status
        public int Code = -1;        // HTTP code of the last poll: -1 not yet asked, 0 no answer, 401 PIN needed, 429 locked
        public int WaitS;
        public bool InFlight, PinAsked;
        public bool? WasCharging;    // for the start/stop notifications
        public string Where()
        {
            return Url.Length > 0 ? Url.Replace("http://", "") : Port.Length > 0 ? Port : BleName.Length > 0 ? BleName : Id.Length > 0 ? "socket " + Id : "?";
        }
        public string Label() { return Name.Length > 0 ? Name : Where(); }
        public override string ToString() { return Label(); }
    }

    class Reply
    {
        public int Code;            // HTTP status, 0 = no answer
        public Dictionary<string, object> Data = new Dictionary<string, object>();
        public string Error;
    }

    static class Net
    {
        public static Reply Call(string url, string method, string pin, int timeoutMs, byte[] body = null, string contentType = null)
        {
            var r = new Reply();
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = method; req.Timeout = timeoutMs; req.ReadWriteTimeout = timeoutMs; req.Proxy = null;
                req.AllowAutoRedirect = false;
                if (!string.IsNullOrEmpty(pin)) req.Headers["X-PIN"] = pin;
                if (body != null)
                {
                    req.ContentType = contentType; req.ContentLength = body.Length;
                    using (var s = req.GetRequestStream()) s.Write(body, 0, body.Length);
                }
                else if (method == "POST") req.ContentLength = 0;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var reader = new StreamReader(resp.GetResponseStream()))
                {
                    r.Code = (int)resp.StatusCode;
                    r.Data = Json.Parse(reader.ReadToEnd());
                }
                // Every answer from a socket is JSON. A redirect (307) or a web page came from the network itself,
                // e.g. a school Wi-Fi's login page: count it as no answer, so the next link (USB, Bluetooth) is tried.
                if (r.Code >= 300 || r.Data == null) return NotSocket(r);
            }
            catch (WebException e)
            {
                var resp = e.Response as HttpWebResponse;
                if (resp != null)
                {
                    r.Code = (int)resp.StatusCode;
                    try { using (var reader = new StreamReader(resp.GetResponseStream())) r.Data = Json.Parse(reader.ReadToEnd()); } catch { }
                }
                r.Error = e.Message;
            }
            catch (Exception e) { r.Error = e.Message; if (r.Code >= 200 && r.Code < 400) return NotSocket(r); }
            return r;
        }

        static Reply NotSocket(Reply r)
        {
            r.Code = 0; r.Data = new Dictionary<string, object>(); r.Error = "not a socket's answer (the network's own page)";
            return r;
        }

        // History comes back as an object with a list inside; the generic parser keeps the list as object[].
        public static List<Dictionary<string, object>> Runs(Dictionary<string, object> d)
        {
            var list = new List<Dictionary<string, object>>();
            object o;
            if (d.TryGetValue("runs", out o) && o is System.Collections.IEnumerable)
                foreach (var x in (System.Collections.IEnumerable)o)
                {
                    var dx = x as Dictionary<string, object>;
                    if (dx != null) list.Add(dx);
                }
            return list;
        }

        // Every address of this computer's local networks (assumed /24, as home routers use).
        public static List<string> LocalAddresses()
        {
            var list = new List<string>();
            foreach (var ni in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (ni.OperationalStatus != OperationalStatus.Up || ni.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                foreach (var ua in ni.GetIPProperties().UnicastAddresses)
                {
                    if (ua.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                    var b = ua.Address.GetAddressBytes();
                    if (b[0] == 169 && b[1] == 254) continue;
                    string prefix = b[0] + "." + b[1] + "." + b[2] + ".";
                    for (int i = 1; i < 255; i++)
                        if (!list.Contains(prefix + i)) list.Add(prefix + i);
                }
            }
            return list;
        }

        public static List<Sock> Scan()
        {
            var found = new List<Sock>();
            var addrs = LocalAddresses();
            int w, io; System.Threading.ThreadPool.GetMinThreads(out w, out io);
            System.Threading.ThreadPool.SetMinThreads(Math.Max(w, 160), io);   // the requests mostly wait
            Parallel.ForEach(addrs, new ParallelOptions { MaxDegreeOfParallelism = 160 }, delegate(string a)
            {
                var r = Call("http://" + a + "/api/info", "GET", null, 900);
                if (r.Code != 200 || Json.S(r.Data, "id").Length == 0 || !Json.S(r.Data, "host").StartsWith("smartsocket")) return;
                var s = new Sock { Id = Json.S(r.Data, "id"), Name = Json.S(r.Data, "name"), Url = "http://" + a };
                lock (found) if (!found.Any(f => f.Id == s.Id)) found.Add(s);
            });
            return found.OrderBy(s => s.Name).ToList();
        }
    }

    // The ways to reach a socket, in the order they are preferred.
    enum Link { None, Wifi, Usb, Ble }

    // One shared owner for each serial port, so the main window and the USB Wi-Fi setup window take
    // turns instead of fighting over it. A port nobody used for a few seconds is closed again, so other
    // programs (the Arduino tools) can have it.
    static class SerialHub
    {
        class P { public SerialPort Sp; public DateTime Used; public readonly object Lock = new object(); }
        static readonly Dictionary<string, P> ports = new Dictionary<string, P>();
        static readonly System.Threading.Timer idle = new System.Threading.Timer(Idle, null, 4000, 4000);

        static P Get(string name)
        {
            lock (ports) { P p; if (!ports.TryGetValue(name, out p)) ports[name] = p = new P(); return p; }
        }

        // Runs f with the port open and to itself. Throws TimeoutException if someone else keeps it longer than waitMs.
        public static T Use<T>(string name, int waitMs, Func<SerialPort, T> f)
        {
            var p = Get(name);
            if (!System.Threading.Monitor.TryEnter(p.Lock, waitMs)) throw new TimeoutException(name + " is busy");
            try
            {
                if (p.Sp == null || !p.Sp.IsOpen)
                {
                    Close(p);
                    var sp = new SerialPort(name, 115200);
                    sp.NewLine = "\n"; sp.ReadTimeout = 400; sp.WriteTimeout = 2000; sp.Encoding = Encoding.UTF8;
                    sp.Open();
                    p.Sp = sp;
                }
                p.Used = DateTime.Now;
                try { return f(p.Sp); }
                catch { Close(p); throw; }
            }
            finally { p.Used = DateTime.Now; System.Threading.Monitor.Exit(p.Lock); }
        }

        // Sends one line and returns the first answer line that "want" accepts (null if none came in time).
        public static string Ask(string name, string line, Func<string, bool> want, int ms)
        {
            return Use(name, ms, delegate(SerialPort sp)
            {
                sp.DiscardInBuffer();
                sp.WriteLine(line);
                return ReadUntil(sp, want, ms);
            });
        }

        public static string ReadUntil(SerialPort sp, Func<string, bool> want, int ms)
        {
            var until = DateTime.Now.AddMilliseconds(ms);
            while (DateTime.Now < until)
            {
                try { string l = sp.ReadLine().Trim(); if (want(l)) return l; }
                catch (TimeoutException) { }
            }
            return null;
        }

        public static void Release(string name)
        {
            var p = Get(name);
            if (!System.Threading.Monitor.TryEnter(p.Lock, 2000)) return;
            try { Close(p); } finally { System.Threading.Monitor.Exit(p.Lock); }
        }

        static void Close(P p)
        {
            try { if (p.Sp != null) p.Sp.Dispose(); } catch { }
            p.Sp = null;
        }

        static void Idle(object o)
        {
            List<P> all; lock (ports) all = ports.Values.ToList();
            foreach (var p in all)
            {
                if (p.Sp == null || !System.Threading.Monitor.TryEnter(p.Lock)) continue;
                try { if ((DateTime.Now - p.Used).TotalSeconds > 8) Close(p); }
                finally { System.Threading.Monitor.Exit(p.Lock); }
            }
        }
    }

    // Bluetooth Low Energy through the Windows Runtime: watching for sockets' advertisements and
    // sending command lines to them (CMD characteristic, answer in pieces on REPLY ending with "\n").
    static class BleHub
    {
        public static readonly Guid Service = new Guid("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e50"),
                                    CmdId = new Guid("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e55"),
                                    ReplyId = new Guid("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e56"),
                                    OtaId = new Guid("6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e57");

        public class Seen { public ulong Addr; public string Name = "", Id = ""; public bool Svc, Asked; public DateTime At; public int Rssi; }
        static readonly Dictionary<ulong, Seen> seen = new Dictionary<ulong, Seen>();
        static Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher watcher;

        // "on", "off", "no adapter", "not checked yet" or a problem.
        public static string State = "not checked yet";
        public static bool Usable { get { return State != "off" && State != "no adapter" && State != "no Bluetooth LE"; } }

        // Waits for a Windows Runtime operation (instead of AsTask, which needs the SDK's Windows.winmd to compile).
        static T Get<T>(Windows.Foundation.IAsyncOperation<T> op, int ms)
        {
            using (var ev = new System.Threading.ManualResetEvent(false))
            {
                op.Completed = delegate { try { ev.Set(); } catch { } };
                if (!ev.WaitOne(ms)) { try { op.Cancel(); } catch { } throw new TimeoutException("Bluetooth did not answer in time"); }
                if (op.Status == Windows.Foundation.AsyncStatus.Completed) return op.GetResults();
                throw op.ErrorCode ?? new Exception("Bluetooth: " + op.Status);
            }
        }

        // Checks the adapter and its radio, and keeps the advertisement watcher running while it is on.
        public static void Check()
        {
            try
            {
                var ad = Get(Windows.Devices.Bluetooth.BluetoothAdapter.GetDefaultAsync(), 5000);
                if (ad == null) { State = "no adapter"; return; }
                if (!ad.IsLowEnergySupported) { State = "no Bluetooth LE"; return; }
                var radio = Get(ad.GetRadioAsync(), 5000);
                if (radio != null && radio.State != Windows.Devices.Radios.RadioState.On) { State = "off"; return; }
                lock (seen)
                {
                    if (watcher == null)
                    {
                        watcher = new Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher();
                        watcher.ScanningMode = Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode.Active;
                        watcher.Received += OnSeen;
                    }
                    if (watcher.Status != Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcherStatus.Started) watcher.Start();
                }
                State = "on";
            }
            catch (Exception e) { State = "unavailable (" + e.Message.Trim() + ")"; }
        }

        static void OnSeen(Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher w,
                           Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementReceivedEventArgs e)
        {
            try
            {
                string n = e.Advertisement.LocalName ?? "";
                bool svc = false;
                foreach (var u in e.Advertisement.ServiceUuids) if (u == Service) svc = true;
                bool named = n.StartsWith("SmartSocket-");
                lock (seen)
                {
                    Seen s;
                    if (!seen.TryGetValue(e.BluetoothAddress, out s))
                    {
                        if (!svc && !named) return;
                        seen[e.BluetoothAddress] = s = new Seen { Addr = e.BluetoothAddress };
                    }
                    if (named)
                    {
                        s.Name = n;
                        string id = n.Substring(12);
                        if (id.Length == 6 && id.All(Uri.IsHexDigit)) s.Id = id;
                    }
                    s.Svc |= svc; s.At = DateTime.Now; s.Rssi = e.RawSignalStrengthInDBm;
                }
            }
            catch { }
        }

        public static List<Seen> Sightings()
        {
            lock (seen) return seen.Values.Select(s => new Seen { Addr = s.Addr, Name = s.Name, Id = s.Id, Svc = s.Svc, At = s.At, Rssi = s.Rssi }).ToList();
        }

        // A device with the socket service but no usable name: ask it who it is (once).
        public static void IdentifyUnnamed()
        {
            List<Seen> todo;
            lock (seen) { todo = seen.Values.Where(s => s.Id.Length == 0 && !s.Asked).ToList(); foreach (var s in todo) s.Asked = true; }
            foreach (var s in todo)
            {
                try
                {
                    string a = Ask(s.Addr, "info", "", 8000);
                    int i = a == null ? -1 : a.IndexOf('{');
                    if (i < 0) continue;
                    var d = Json.Parse(a.Substring(i));
                    if (!Json.S(d, "host").StartsWith("smartsocket")) continue;
                    lock (seen) { s.Id = Json.S(d, "id"); if (s.Name.Length == 0) s.Name = "SmartSocket-" + s.Id; }
                }
                catch { }
            }
        }

        class Conn
        {
            public Windows.Devices.Bluetooth.BluetoothLEDevice Dev;
            public Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService Svc;
            public Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristic Cmd, Rep;
            public readonly MemoryStream Buf = new MemoryStream();
            public readonly System.Threading.AutoResetEvent Got = new System.Threading.AutoResetEvent(false);
            public string AuthedPin;
            public volatile bool Stale;
            public DateTime Used;
            public readonly object Lock = new object();
        }
        static readonly Dictionary<ulong, Conn> conns = new Dictionary<ulong, Conn>();
        static readonly System.Threading.Timer idle = new System.Threading.Timer(Idle, null, 10000, 10000);

        // Sends one command line and returns its "#cmd {...}" answer, or null if none came. Gives the PIN first when there is one.
        public static string Ask(ulong addr, string line, string pin, int ms)
        {
            Conn c;
            lock (conns) { if (!conns.TryGetValue(addr, out c)) conns[addr] = c = new Conn(); }
            if (!System.Threading.Monitor.TryEnter(c.Lock, ms)) throw new TimeoutException("Bluetooth link busy");
            try
            {
                c.Used = DateTime.Now;
                if (c.Stale) Drop(c);
                if (c.Dev == null) Open(c, addr);
                if (!string.IsNullOrEmpty(pin) && c.AuthedPin != pin)
                {
                    string a = Send(c, "auth " + pin, ms);
                    if (a == null) { Drop(c); return null; }
                    if (a.Contains("\"ok\":true")) c.AuthedPin = pin;
                }
                string ans = Send(c, line, ms);
                if (ans == null) Drop(c);
                return ans;
            }
            catch { Drop(c); throw; }
            finally { c.Used = DateTime.Now; System.Threading.Monitor.Exit(c.Lock); }
        }

        // Loads a new program over Bluetooth (program 1.7 and later): "ota begin", the file in pieces
        // written with response to the OTA characteristic, then "ota end", which checks it and restarts.
        // Returns null when done, otherwise why not.
        public static int LastChunk;   // piece size of the last upload (for tests)
        public static string Upload(ulong addr, byte[] data, string pin, Action<int, int> progress)
        {
            Conn c;
            lock (conns) { if (!conns.TryGetValue(addr, out c)) conns[addr] = c = new Conn(); }
            if (!System.Threading.Monitor.TryEnter(c.Lock, 15000)) return "Bluetooth link busy";
            IDisposable fast = null;
            try
            {
                if (c.Stale) Drop(c);
                if (c.Dev == null) Open(c, addr);
                if (!string.IsNullOrEmpty(pin) && c.AuthedPin != pin)
                {
                    string a0 = Send(c, "auth " + pin, 8000);
                    if (a0 == null || !a0.Contains("\"ok\":true")) return "PIN refused";
                    c.AuthedPin = pin;
                }
                Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristic ota;
                try { ota = Char(c.Svc, OtaId); }
                catch { return "old program"; }
                // Ask for short connection intervals while the program goes over (Windows 11; ignored elsewhere).
                try { fast = c.Dev.RequestPreferredConnectionParameters(Windows.Devices.Bluetooth.BluetoothLEPreferredConnectionParameters.ThroughputOptimized); }
                catch { }
                string md5;
                using (var h = System.Security.Cryptography.MD5.Create())
                    md5 = string.Concat(h.ComputeHash(data).Select(b => b.ToString("x2")));
                string a = Send(c, "ota begin " + data.Length + " " + md5, 20000);
                if (a == null) return "no answer to the start";
                if (!a.Contains("\"active\":true")) return a;
                // Windows agrees a bigger packet size with the socket shortly after connecting; until then
                // it reports the 23-byte minimum, which would make the upload ten times slower.
                int pdu = 23;
                for (int i = 0; i < 30 && pdu <= 23; i++)
                {
                    try { pdu = c.Svc.Session.MaxPduSize; } catch { }
                    if (pdu <= 23) System.Threading.Thread.Sleep(100);
                }
                int chunk = Math.Max(20, Math.Min(509, pdu - 3));
                LastChunk = chunk;
                bool noResp = (ota.CharacteristicProperties & Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicProperties.WriteWithoutResponse) != 0;
                for (int i = 0; i < data.Length; i += chunk)
                {
                    int n = Math.Min(chunk, data.Length - i);
                    var part = new byte[n];
                    Buffer.BlockCopy(data, i, part, 0, n);
                    var w = new Windows.Storage.Streams.DataWriter();
                    w.WriteBytes(part);
                    // Most pieces go without waiting for an answer; every 16th (and the last) waits, which keeps
                    // the laptop from running ahead of the socket's flash writes. Bluetooth keeps them in order.
                    bool sync = !noResp || (i / chunk) % 16 == 15 || i + n >= data.Length;
                    var st = Get(ota.WriteValueAsync(w.DetachBuffer(), sync ? Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption.WriteWithResponse
                                                                             : Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption.WriteWithoutResponse), 15000);
                    if (st != Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus.Success)
                    {
                        try { Send(c, "ota abort", 3000); } catch { }
                        return "sending stopped (" + st + ")";
                    }
                    c.Used = DateTime.Now;
                    if (progress != null && ((i / chunk) % 25 == 0 || i + n >= data.Length)) progress(i + n, data.Length);
                }
                a = Send(c, "ota end", 30000);
                if (a == null) return "no answer to the final check";
                if (!a.Contains("\"ok\":true")) return a;
                Drop(c);
                return null;
            }
            catch (Exception e) { Drop(c); return e.Message; }
            finally
            {
                try { if (fast != null) fast.Dispose(); } catch { }
                c.Used = DateTime.Now; System.Threading.Monitor.Exit(c.Lock);
            }
        }

        static void Open(Conn c, ulong addr)
        {
            var dev = Get(Windows.Devices.Bluetooth.BluetoothLEDevice.FromBluetoothAddressAsync(addr), 10000);
            if (dev == null) throw new Exception("Bluetooth device not found");
            try
            {
                Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService svc = null;
                // The plain "all services" call: on some laptops the by-UUID call fails uncached with 0x80070016.
                foreach (var mode in new[] { Windows.Devices.Bluetooth.BluetoothCacheMode.Cached, Windows.Devices.Bluetooth.BluetoothCacheMode.Uncached })
                {
                    try
                    {
                        var r = Get(dev.GetGattServicesAsync(mode), 15000);
                        if (r.Status != Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus.Success) continue;
                        svc = r.Services.FirstOrDefault(x => x.Uuid == Service);
                        if (svc != null) break;
                    }
                    catch { }
                }
                if (svc == null) throw new Exception("socket service not found over Bluetooth");
                c.Svc = svc;
                c.Cmd = Char(svc, CmdId); c.Rep = Char(svc, ReplyId);
                var conn = c;
                c.Rep.ValueChanged += delegate(Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristic ch,
                                               Windows.Devices.Bluetooth.GenericAttributeProfile.GattValueChangedEventArgs e)
                {
                    try
                    {
                        var rd = Windows.Storage.Streams.DataReader.FromBuffer(e.CharacteristicValue);
                        var b = new byte[rd.UnconsumedBufferLength];
                        rd.ReadBytes(b);
                        lock (conn.Buf) conn.Buf.Write(b, 0, b.Length);
                        conn.Got.Set();
                    }
                    catch { }
                };
                var st = Get(c.Rep.WriteClientCharacteristicConfigurationDescriptorAsync(
                    Windows.Devices.Bluetooth.GenericAttributeProfile.GattClientCharacteristicConfigurationDescriptorValue.Notify), 10000);
                if (st != Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus.Success) throw new Exception("the socket refused notifications (" + st + ")");
                dev.ConnectionStatusChanged += delegate(Windows.Devices.Bluetooth.BluetoothLEDevice d, object o)
                {
                    try { if (d.ConnectionStatus == Windows.Devices.Bluetooth.BluetoothConnectionStatus.Disconnected) conn.Stale = true; } catch { }
                };
                c.Dev = dev; c.AuthedPin = null; c.Stale = false;
            }
            catch
            {
                try { if (c.Svc != null) c.Svc.Dispose(); } catch { }
                c.Svc = null; c.Cmd = c.Rep = null;
                try { dev.Dispose(); } catch { }
                throw;
            }
        }

        static Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristic Char(Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService svc, Guid id)
        {
            foreach (var mode in new[] { Windows.Devices.Bluetooth.BluetoothCacheMode.Cached, Windows.Devices.Bluetooth.BluetoothCacheMode.Uncached })
            {
                try
                {
                    var r = Get(svc.GetCharacteristicsAsync(mode), 10000);
                    if (r.Status != Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus.Success) continue;
                    var c = r.Characteristics.FirstOrDefault(x => x.Uuid == id);
                    if (c != null) return c;
                }
                catch { }
            }
            throw new Exception("socket characteristic " + id + " not found");
        }

        static string Send(Conn c, string line, int ms)
        {
            string cmd = line.Split(' ')[0];
            lock (c.Buf) c.Buf.SetLength(0);
            c.Got.Reset();
            var w = new Windows.Storage.Streams.DataWriter();
            w.WriteBytes(Encoding.UTF8.GetBytes(line));
            var st = Get(c.Cmd.WriteValueAsync(w.DetachBuffer(), Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption.WriteWithResponse), ms);
            if (st != Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus.Success) return null;
            var until = DateTime.Now.AddMilliseconds(ms);
            while (true)
            {
                string text; lock (c.Buf) text = Encoding.UTF8.GetString(c.Buf.ToArray());
                foreach (var l in text.Split('\n').Take(Math.Max(0, text.Split('\n').Length - 1)))
                    if (l.Trim().StartsWith("#" + cmd + " ")) return l.Trim();
                int left = (int)(until - DateTime.Now).TotalMilliseconds;
                if (left <= 0) return null;
                c.Got.WaitOne(Math.Min(left, 200));
            }
        }

        static void Drop(Conn c)
        {
            try { if (c.Svc != null) c.Svc.Dispose(); } catch { }
            try { if (c.Dev != null) c.Dev.Dispose(); } catch { }
            c.Svc = null; c.Dev = null; c.Cmd = c.Rep = null; c.AuthedPin = null; c.Stale = false;
        }

        // Lets go of a Bluetooth connection nobody used for half a minute (the phone app may want it).
        static void Idle(object o)
        {
            List<Conn> all; lock (conns) all = conns.Values.ToList();
            foreach (var c in all)
            {
                if (c.Dev == null || !System.Threading.Monitor.TryEnter(c.Lock)) continue;
                try { if ((DateTime.Now - c.Used).TotalSeconds > 30) Drop(c); }
                finally { System.Threading.Monitor.Exit(c.Lock); }
            }
        }
    }

    // Reaching a socket by Wi-Fi (HTTP), USB (serial) or Bluetooth with the same requests: the HTTP
    // paths the app uses are turned into the firmware's command lines, and "#cmd {json}" answers back
    // into replies with HTTP-like codes (200 ok, 401 PIN needed, 400 refused, 0 no answer).
    static class Links
    {
        public static bool WifiOn = true, UsbOn = true, BleOn = true;   // test switches: --only-ble, --only-usb, --no-wifi...

        public static string Name(Link l) { return l == Link.Wifi ? "Wi-Fi" : l == Link.Usb ? "USB" : l == Link.Ble ? "Bluetooth" : "nothing"; }

        public static List<Link> Of(Sock s)
        {
            var l = new List<Link>();
            if (WifiOn && s.Url.Length > 0) l.Add(Link.Wifi);
            if (UsbOn && s.Port.Length > 0) l.Add(Link.Usb);
            if (BleOn && s.Ble != 0 && BleHub.Usable) l.Add(Link.Ble);
            return l;
        }

        static int Backoff(Link l) { return l == Link.Wifi ? 30 : l == Link.Usb ? 10 : 15; }

        // Tries the links in order (the one in use first for commands; the preferred order for polls,
        // skipping links that failed a moment ago) and remembers which one answered.
        public static Reply Exchange(Sock s, string method, string path, string pin, int ms, bool poll)
        {
            var order = Of(s);
            if (order.Count == 0) return new Reply { Error = "no way to reach it" };
            var via = s.Via;
            if (!poll && order.Contains(via)) { order.Remove(via); order.Insert(0, via); }
            Reply last = null;
            bool tried = false;
            for (int i = 0; i < order.Count; i++)
            {
                var l = order[i];
                bool resting;
                lock (s.Retry) resting = s.Retry.ContainsKey(l) && DateTime.Now < s.Retry[l];
                if (resting && (poll || l != via) && (tried || i < order.Count - 1)) continue;
                tried = true;
                // While another link works, a quick look is enough to tell whether Wi-Fi is back.
                int t = l == Link.Wifi && via != Link.Wifi && via != Link.None ? Math.Min(ms, 1500) : ms;
                Reply r;
                try { r = Via(s, l, method, path, pin, t); }
                catch (Exception e) { r = new Reply { Error = e.Message }; }
                if (r.Code > 0)
                {
                    lock (s.Retry) { s.Retry.Remove(l); s.Failed.Remove(l); }
                    s.Via = l;
                    return r;
                }
                lock (s.Retry) { s.Retry[l] = DateTime.Now.AddSeconds(Backoff(l)); s.Failed[l] = r.Error ?? "no answer"; }
                last = r;
            }
            if (tried) s.Via = Link.None;
            return last ?? new Reply { Error = "no answer" };
        }

        public static Reply Via(Sock s, Link l, string method, string path, string pin, int ms)
        {
            if (l == Link.Wifi) return Net.Call(s.Url + path, method, pin, ms);
            string cmd, arg;
            if (!ToCommand(path, out cmd, out arg)) return new Reply { Error = "only over Wi-Fi" };
            string line = arg.Length > 0 ? cmd + " " + arg : cmd, answer;
            if (l == Link.Usb)
            {
                // Over USB "info" keeps its plain program 1.5 answer; everything else answers "#cmd {...}".
                answer = cmd == "info"
                    ? SerialHub.Ask(s.Port, "info", a => a.StartsWith("{") && a.Contains("\"host\""), ms)
                    : SerialHub.Ask(s.Port, line, a => a.StartsWith("#" + cmd + " "), ms);
            }
            else answer = BleHub.Ask(s.Ble, line, pin, Math.Max(ms, 4000));
            var r = ToReply(answer);
            // Someone plugged another socket into this port: this one is no longer there.
            string id = Json.S(r.Data, "id");
            if (r.Code == 200 && id.Length > 0 && s.Id.Length > 0 && id != s.Id)
            {
                if (l == Link.Usb) s.Port = ""; else s.Ble = 0;
                return new Reply { Error = "another socket answered" };
            }
            return r;
        }

        static string Arg(string q, string key)
        {
            foreach (var part in q.Split('&'))
                if (part.StartsWith(key + "=")) return part.Substring(key.Length + 1);
            return "";
        }

        static string Plain(string v) { return Uri.UnescapeDataString(v.Replace("+", " ")).Replace("\r", " ").Replace("\n", " "); }

        public static bool ToCommand(string path, out string cmd, out string arg)
        {
            int qi = path.IndexOf('?');
            string p = qi < 0 ? path : path.Substring(0, qi), q = qi < 0 ? "" : path.Substring(qi + 1);
            arg = "";
            switch (p)
            {
                case "/api/status": cmd = "full"; return true;
                case "/api/info": cmd = "info"; return true;
                case "/api/config": cmd = "config"; arg = q; return true;
                case "/api/boost": cmd = "boost"; arg = Arg(q, "on") == "1" ? "1" : "0"; return true;
                case "/api/name": cmd = "name"; arg = Plain(Arg(q, "name")); return true;
                case "/api/pin": cmd = "pin"; arg = Plain(Arg(q, "new")); return true;
                case "/api/identify": cmd = "identify"; return true;
                case "/api/history": cmd = "history"; return true;
                case "/api/nets": cmd = "nets"; return true;
                default: cmd = ""; return false;
            }
        }

        public static Reply ToReply(string answer)
        {
            var r = new Reply();
            int i = answer == null ? -1 : answer.IndexOf('{');
            if (i < 0) { r.Error = answer == null ? "no answer" : "unexpected answer: " + answer; return r; }
            r.Data = Json.Parse(answer.Substring(i));
            string err = Json.S(r.Data, "error");
            if (err.Length == 0) r.Code = 200;
            else { r.Code = err == "pin" ? 401 : 400; r.Error = err; }
            return r;
        }

        // Asks every given serial port whether a socket is on it.
        public static List<Sock> ScanUsb(IEnumerable<string> portNames)
        {
            var found = new List<Sock>();
            Parallel.ForEach(portNames, delegate(string pn)
            {
                string a = null;
                try { a = SerialHub.Ask(pn, "info", l => l.StartsWith("{") && l.Contains("\"host\""), 1800); } catch { }
                var d = a == null ? null : Json.Parse(a);
                if (d == null || Json.S(d, "id").Length == 0 || !Json.S(d, "host").StartsWith("smartsocket")) { SerialHub.Release(pn); return; }
                lock (found) found.Add(new Sock { Id = Json.S(d, "id"), Name = Json.S(d, "name"), Port = pn });
            });
            return found;
        }
    }

    // PINs, known sockets and options, kept in %APPDATA%\SmartSocket.
    static class Store
    {
        static string Dir { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "SmartSocket"); } }
        static string F(string n) { return Path.Combine(Dir, n); }

        static Dictionary<string, string> Read(string file)
        {
            var d = new Dictionary<string, string>();
            try
            {
                foreach (var line in File.ReadAllLines(F(file)))
                {
                    int i = line.IndexOf('=');
                    if (i > 0) d[line.Substring(0, i)] = line.Substring(i + 1);
                }
            }
            catch { }
            return d;
        }
        static void Write(string file, Dictionary<string, string> d)
        {
            try { Directory.CreateDirectory(Dir); File.WriteAllLines(F(file), d.Select(kv => kv.Key + "=" + kv.Value).ToArray()); } catch { }
        }

        public static string Pin(string id) { string p; return !string.IsNullOrEmpty(id) && Read("pins.txt").TryGetValue(id, out p) ? p : ""; }
        public static void SetPin(string id, string pin)
        {
            if (string.IsNullOrEmpty(id)) return;
            var d = Read("pins.txt");
            if (string.IsNullOrEmpty(pin)) d.Remove(id); else d[id] = pin;
            Write("pins.txt", d);
        }

        // Known sockets: id -> "url|name", so the list is there at once on the next start.
        // and links.txt: id -> "port|bluetooth address|bluetooth name" (a separate file so older versions still read sockets.txt).
        public static List<Sock> Known()
        {
            var links = Read("links.txt");
            return Read("sockets.txt").Select(kv =>
            {
                var parts = kv.Value.Split(new[] { '|' }, 2);
                var s = new Sock { Id = kv.Key, Url = parts[0], Name = parts.Length > 1 ? parts[1] : "" };
                string l;
                if (links.TryGetValue(kv.Key, out l))
                {
                    var lp = l.Split('|');
                    s.Port = lp[0];
                    ulong a; if (lp.Length > 1 && ulong.TryParse(lp[1], out a)) s.Ble = a;
                    if (lp.Length > 2) s.BleName = lp[2];
                }
                return s;
            }).ToList();
        }
        public static void SetKnown(IEnumerable<Sock> socks)
        {
            var d = new Dictionary<string, string>();
            var links = new Dictionary<string, string>();
            foreach (var s in socks)
                if (s.Id.Length > 0)
                {
                    d[s.Id] = s.Url + "|" + s.Name;
                    if (s.Port.Length > 0 || s.Ble != 0) links[s.Id] = s.Port + "|" + s.Ble + "|" + s.BleName;
                }
            Write("sockets.txt", d);
            Write("links.txt", links);
        }

        public static bool Option(string name, bool def)
        {
            string v; return Read("options.txt").TryGetValue(name, out v) ? v == "1" : def;
        }
        public static void SetOption(string name, bool on)
        {
            var d = Read("options.txt"); d[name] = on ? "1" : "0"; Write("options.txt", d);
        }
    }

    static class Ask
    {
        // A small dialog with one text box. Returns null if cancelled.
        public static string Text(IWin32Window owner, string title, string prompt, bool secret, string start = "")
        {
            using (var f = new Form())
            {
                f.Text = title; f.Font = new Font("Segoe UI", 10f); f.FormBorderStyle = FormBorderStyle.FixedDialog;
                f.MinimizeBox = false; f.MaximizeBox = false; f.StartPosition = FormStartPosition.CenterParent; f.ClientSize = new Size(360, 136);
                var l = new Label { Text = prompt, AutoSize = false }; l.SetBounds(14, 10, 332, 42); f.Controls.Add(l);
                var t = new TextBox { UseSystemPasswordChar = secret, Text = start }; t.SetBounds(14, 56, 332, 27); f.Controls.Add(t);
                var ok = new Button { Text = "OK", DialogResult = DialogResult.OK }; ok.SetBounds(180, 96, 80, 30); f.Controls.Add(ok);
                var no = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel }; no.SetBounds(266, 96, 80, 30); f.Controls.Add(no);
                f.AcceptButton = ok; f.CancelButton = no;
                return f.ShowDialog(owner) == DialogResult.OK ? t.Text.Trim() : null;
            }
        }
    }

    // Wi-Fi over USB: what the socket sees, the networks it knows, and joining a new one. Uses the
    // program 1.6 serial commands (nets, forget, setup, join); program 1.5 understands setup and status.
    class UsbSetupForm : Form
    {
        readonly ComboBox port = new ComboBox(), ssid = new ComboBox();
        readonly TextBox pass = new TextBox(), user = new TextBox(), name = new TextBox(), pin = new TextBox();
        readonly ListBox saved = new ListBox();
        readonly Button go = new Button(), refresh = new Button(), look = new Button(), forget = new Button();
        readonly CheckBox showPass = new CheckBox();
        readonly Label result = new Label(), state = new Label();
        public string JoinedUrl, JoinedPin;

        public UsbSetupForm()
        {
            Text = "Socket Wi-Fi (over USB)"; Font = new Font("Segoe UI", 10f); FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; MinimizeBox = false; StartPosition = FormStartPosition.CenterParent; ClientSize = new Size(720, 520);
            int x = 16, w = 330, y = 12;
            var intro = new Label { Text = "Plug the socket's controller (the XIAO) into this computer with a USB-C cable.", AutoSize = false };
            intro.SetBounds(x, y, 690, 22); Controls.Add(intro); y += 30;

            // left: join a network
            Row("USB port", port, ref y, x, w - 90); refresh.Text = "Refresh"; refresh.SetBounds(x + w - 84, y - 37, 84, 29); Controls.Add(refresh);
            port.DropDownStyle = ComboBoxStyle.DropDownList;
            Row("Wi-Fi network (pick one it can see, or type a hidden one)", ssid, ref y, x, w - 90);
            look.Text = "Look again"; look.SetBounds(x + w - 84, y - 37, 84, 29); Controls.Add(look);
            ssid.DropDownStyle = ComboBoxStyle.DropDown;
            Row("Password (empty for an open network)", pass, ref y, x, w); pass.UseSystemPasswordChar = true;
            showPass.Text = "Show"; showPass.AutoSize = true; showPass.Location = new Point(x + w - 62, y - 58); Controls.Add(showPass);
            Row("Username: only if the network asks for one (e.g. eduroam)", user, ref y, x, w);
            Row("Name for this socket (optional)", name, ref y, x, w);
            Row("New PIN, 4 to 12 characters (optional)", pin, ref y, x, w); pin.UseSystemPasswordChar = true;
            go.Text = "Connect it to this network"; go.SetBounds(x, y + 2, w, 34); Controls.Add(go); y += 44;
            result.AutoSize = false; result.SetBounds(x, y, w, 60); result.ForeColor = SystemColors.GrayText; Controls.Add(result);

            // right: what it knows
            int rx = 370, rw = 334, ry = 42;
            Controls.Add(new Label { Text = "Networks it already knows (joins the strongest one by itself)", AutoSize = false, Bounds = new Rectangle(rx, ry, rw, 40) }); ry += 44;
            saved.SetBounds(rx, ry, rw, 220); Controls.Add(saved); ry += 226;
            forget.Text = "Forget the selected network"; forget.SetBounds(rx, ry, rw, 32); Controls.Add(forget); ry += 44;
            state.AutoSize = false; state.SetBounds(rx, ry, rw, 110); state.ForeColor = SystemColors.GrayText; Controls.Add(state);

            refresh.Click += delegate { Ports(); };
            port.SelectedIndexChanged += delegate { LoadNets(); };
            look.Click += delegate { LookAgain(); };
            showPass.CheckedChanged += delegate { pass.UseSystemPasswordChar = !showPass.Checked; };
            ssid.SelectedIndexChanged += delegate
            {
                var n = ssid.SelectedItem as SeenNet;
                if (n != null) { user.Enabled = true; if (n.User) user.Focus(); else pass.Focus(); }
            };
            forget.Click += delegate { Forget(); };
            go.Click += delegate { Start(); };
            Shown += delegate { Ports(); };
        }

        class SeenNet
        {
            public string Ssid; public int Rssi; public bool Open, User, Known;
            public override string ToString()
            {
                return Ssid + "   (" + Bars(Rssi) + (Open ? ", open" : "") + (User ? ", username" : "") + (Known ? ", known" : "") + ")";
            }
            static string Bars(int r) { return r >= -60 ? "strong" : r >= -72 ? "good" : r >= -82 ? "weak" : "very weak"; }
        }

        void Row(string label, Control c, ref int y, int x, int w)
        {
            var l = new Label { Text = label, AutoSize = false, Bounds = new Rectangle(x, y, w + 100, 20) }; Controls.Add(l); y += 22;
            c.SetBounds(x, y, w, 27); Controls.Add(c); y += 36;
        }

        void Ports()
        {
            port.Items.Clear();
            foreach (var p in SerialPort.GetPortNames().OrderBy(p => p)) port.Items.Add(p);
            if (port.Items.Count > 0) port.SelectedIndex = port.Items.Count - 1;
            else result.Text = "No USB serial port found. Plug the controller in, then press Refresh.";
        }

        string PortName { get { return port.SelectedItem == null ? null : port.SelectedItem.ToString(); } }

        // Sends one command and waits for its "#cmd {...}" answer (null if none came).
        // The port is shared with the main window's USB link through SerialHub, so the two take turns.
        string Ask(string portName, string line, string answerPrefix, int ms)
        {
            return SerialHub.Ask(portName, line, l => l.StartsWith(answerPrefix), ms);
        }

        static Dictionary<string, object> Body(string answer)
        {
            if (answer == null) return null;
            int i = answer.IndexOf('{');
            return i < 0 ? null : Json.Parse(answer.Substring(i));
        }

        static List<Dictionary<string, object>> List(Dictionary<string, object> d, string key)
        {
            var l = new List<Dictionary<string, object>>(); object o;
            if (d != null && d.TryGetValue(key, out o) && o is System.Collections.IEnumerable)
                foreach (var x in (System.Collections.IEnumerable)o) { var dx = x as Dictionary<string, object>; if (dx != null) l.Add(dx); }
            return l;
        }

        // Reads what the socket knows and sees, and its Wi-Fi state.
        void LoadNets()
        {
            string pn = PortName;
            if (pn == null) return;
            state.Text = "Asking the socket...";
            Task.Factory.StartNew(delegate
            {
                string info = null, nets = null, err = null;
                try { info = Ask(pn, "info", "{", 2500); nets = Ask(pn, "nets", "#nets", 2500); }
                catch (Exception e) { err = e.Message; }
                try { BeginInvoke((MethodInvoker)delegate { Show(info, nets, err); }); } catch { }
            });
        }

        void Show(string info, string nets, string err)
        {
            if (err != null || info == null) { state.Text = err != null ? "Could not use the port: " + err : "No answer on this port. Is it the socket, running program 1.5 or newer?"; return; }
            var i = Body(info);
            bool wifi = Json.B(i, "wifi");
            state.Text = Json.S(i, "name") + ", program " + Json.S(i, "fw") + "\n" +
                         (wifi ? "On Wi-Fi \"" + Json.S(i, "ssid") + "\" at " + Json.S(i, "ip") : Json.B(i, "setup") ? "Not on Wi-Fi: setup is open (fast blinking light)" : "Looking for a network it knows...");
            var n = Body(nets);
            if (n == null) { state.Text += "\n(Program 1.5: it knows one network only. Update to 1.6 for the full list.)"; return; }
            string keep = ssid.Text;
            ssid.Items.Clear();
            foreach (var s in List(n, "seen").OrderByDescending(s => Json.I(s, "rssi")))
                ssid.Items.Add(new SeenNet { Ssid = Json.S(s, "ssid"), Rssi = Json.I(s, "rssi"), Open = Json.B(s, "open"), User = Json.B(s, "user"), Known = Json.B(s, "known") });
            ssid.Text = keep;
            saved.Items.Clear();
            string current = Json.S(n, "current");
            foreach (var s in List(n, "saved"))
            {
                string nm = Json.S(s, "ssid");
                saved.Items.Add(nm + (Json.S(s, "user").Length > 0 ? "   (user " + Json.S(s, "user") + ")" : "") + (nm == current ? "   (in use)" : ""));
            }
            if (saved.Items.Count == 0) saved.Items.Add("(none yet)");
        }

        void LookAgain()
        {
            string pn = PortName;
            if (pn == null) return;
            state.Text = "Scanning (about 3 s)...";
            Task.Factory.StartNew(delegate
            {
                try { Ask(pn, "scan", "#scan", 2000); System.Threading.Thread.Sleep(3000); } catch { }
                try { BeginInvoke((MethodInvoker)LoadNets); } catch { }
            });
        }

        void Forget()
        {
            string pn = PortName;
            if (pn == null || saved.SelectedItem == null) return;
            string nm = saved.SelectedItem.ToString();
            int cut = nm.IndexOf("   (");
            if (cut >= 0) nm = nm.Substring(0, cut);
            if (nm == "(none yet)") return;
            Task.Factory.StartNew(delegate
            {
                try { Ask(pn, "forget " + nm, "#forget", 2500); } catch { }
                try { BeginInvoke((MethodInvoker)LoadNets); } catch { }
            });
        }

        string ChosenSsid()
        {
            var n = ssid.SelectedItem as SeenNet;
            return n != null && ssid.Text == n.ToString() ? n.Ssid : ssid.Text.Trim();
        }

        void Start()
        {
            string pn = PortName;
            if (pn == null) { result.Text = "Choose the USB port."; return; }
            string s = ChosenSsid();
            if (s.Length == 0) { result.Text = "Choose or type the Wi-Fi network."; return; }
            string p = pin.Text.Trim();
            if (p.Length > 0 && (p.Length < 4 || p.Length > 12)) { result.Text = "A PIN has 4 to 12 characters."; return; }
            string line = "setup {\"ssid\":" + Json.Str(s) + ",\"pass\":" + Json.Str(pass.Text) + ",\"user\":" + Json.Str(user.Text.Trim()) +
                          ",\"name\":" + Json.Str(name.Text.Trim()) + ",\"pin\":" + Json.Str(p) + "}";
            if (Encoding.UTF8.GetByteCount(line) > 580) { result.Text = "The details are too long for the serial line."; return; }
            bool slow = user.Text.Trim().Length > 0;
            go.Enabled = false; result.ForeColor = SystemColors.GrayText; result.Text = "Joining " + s + "... (up to " + (slow ? 30 : 20) + " s)";
            Task.Factory.StartNew(delegate
            {
                string msg; bool ok = false; string url = null;
                try { ok = Run(pn, line, s, slow ? 40 : 30, out msg, out url); }
                catch (Exception e) { msg = "Could not use " + pn + ": " + e.Message; }
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        go.Enabled = true; result.ForeColor = ok ? Color.FromArgb(31, 157, 85) : Color.FromArgb(185, 28, 28); result.Text = msg;
                        if (ok) { JoinedUrl = url; JoinedPin = pin.Text.Trim(); pass.Text = ""; }
                        LoadNets();
                    });
                }
                catch { }
            });
        }

        // Sends the setup line and follows the join until it succeeds or fails.
        bool Run(string portName, string line, string ssidWanted, int seconds, out string msg, out string url)
        {
            string m = null, u = null;
            bool ok = SerialHub.Use(portName, 6000, delegate(SerialPort sp)
            {
                sp.DiscardInBuffer();
                sp.WriteLine(line);
                string answer = Wait(sp, l => l.StartsWith("setup:"), 4000);
                if (answer == null) { m = "No answer on this port. Is it the socket, and does it run program 1.5 or newer?"; return false; }
                if (!answer.Contains("accepted")) { m = answer.Contains("pin") ? "Refused: the socket has a PIN; this port should not need it. Update to 1.6." : "Refused: " + answer; return false; }
                var until = DateTime.Now.AddSeconds(seconds);
                System.Threading.Thread.Sleep(1500);
                while (DateTime.Now < until)
                {
                    sp.WriteLine("status");
                    string st = Wait(sp, l => l.StartsWith("{") && l.Contains("\"state\""), 2500);
                    if (st != null)
                    {
                        var d = Json.Parse(st);
                        string stt = Json.S(d, "state"), ip = Json.S(d, "ip");
                        if (stt == "ok" && ip.Length > 0)
                        {
                            u = "http://" + ip;
                            m = "Done. It is on \"" + ssidWanted + "\" at " + ip + " and will come back to it by itself.";
                            return true;
                        }
                        if (stt == "fail")
                        {
                            int r = Json.I(d, "reason");
                            m = "It could not join that network (" + Reason(r) + ").";
                            return false;
                        }
                    }
                    System.Threading.Thread.Sleep(800);
                }
                m = "No result after " + seconds + " s. Check the name and password, then try again.";
                return false;
            });
            msg = m; url = u;
            return ok;
        }

        // The usual reasons a network refuses (ESP-IDF disconnect reason codes).
        static string Reason(int r)
        {
            switch (r)
            {
                case 15: case 204: case 202: return "wrong password, reason " + r;
                case 201: return "network not found: too far, 5 GHz only, or name mistyped; reason 201";
                case 2: case 23: return "the network rejected the login, check username and password; reason " + r;
                case 0: return "no reason given";
                default: return "reason " + r;
            }
        }

        static string Wait(SerialPort sp, Func<string, bool> want, int ms) { return SerialHub.ReadUntil(sp, want, ms); }
    }

    class MainForm : Form
    {
        static readonly Color Go = Color.FromArgb(31, 157, 85), Stop = Color.FromArgb(217, 119, 6), Off = Color.FromArgb(138, 148, 163),
                              Bad = Color.FromArgb(185, 28, 28);
        static readonly int[] Currents = { 500, 1500, 3000 };
        const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";

        // left: every socket
        readonly ListBox list = new ListBox();
        readonly Button search = new Button(), addAddr = new Button(), usb = new Button();
        readonly TabControl tabs = new TabControl();

        // Charging tab
        readonly Panel dot = new Panel(), bar = new Panel();
        readonly Label headline = new Label(), detail = new Label(), levelText = new Label(), chargeFor = new Label();
        readonly Button boost = new Button();
        readonly TrackBar upper = new TrackBar(), lower = new TrackBar();
        readonly Label upperVal = new Label(), lowerVal = new Label();
        readonly RadioButton modeAuto = new RadioButton(), modeOn = new RadioButton(), modeOff = new RadioButton();
        readonly ComboBox maxMa = new ComboBox();

        // Timer and hours tab
        readonly CheckBox timerOn = new CheckBox(), hoursOn = new CheckBox();
        readonly NumericUpDown timerMin = new NumericUpDown(), safetyH = new NumericUpDown();
        readonly DateTimePicker hoursFrom = new DateTimePicker(), hoursTo = new DateTimePicker();
        readonly Button applyTimes = new Button();
        readonly Label timesNote = new Label();
        bool timesDirty;

        // History tab
        readonly ListView history = new ListView();
        readonly Button refreshHistory = new Button();
        readonly Label historyNote = new Label();

        // Socket tab
        readonly TextBox nameBox = new TextBox();
        readonly Button rename = new Button(), pinBtn = new Button(), find = new Button(), page = new Button(), update = new Button();
        readonly TrackBar led = new TrackBar();
        readonly Label ledVal = new Label(), facts = new Label(), updateNote = new Label();
        readonly CheckBox optNotify = new CheckBox(), optTray = new CheckBox(), optStartup = new CheckBox();

        readonly Timer timer = new Timer();
        readonly NotifyIcon tray = new NotifyIcon();
        readonly List<Sock> socks = new List<Sock>();

        Sock cur;
        DateTime holdUntil = DateTime.MinValue;
        bool quitting, filling, uploading, bleChecking, portsChecking;
        int ticks;
        readonly HashSet<string> portsSeen = new HashSet<string>();
        public string ShotPath, ShotTab;
        DateTime started = DateTime.Now;

        Status St { get { return cur == null ? null : cur.Last; } }

        public MainForm(string startAddress, bool startInTray)
        {
            Text = "Smart Socket";
            Font = new Font("Segoe UI", 10f);
            AutoScaleMode = AutoScaleMode.Font;
            ClientSize = new Size(800, 610);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            Icon = MakeIcon(Go);

            // ---- left: every socket ----
            Add(this, new Label { Text = "Sockets", AutoSize = true, Font = new Font("Segoe UI", 10f, FontStyle.Bold) }, 12, 12);
            list.DrawMode = DrawMode.OwnerDrawFixed; list.ItemHeight = 50; list.IntegralHeight = false;
            Add(this, list, 12, 38, 236, 440);
            search.Text = "Search for sockets"; Add(this, search, 12, 486, 236, 32);
            addAddr.Text = "Add by address..."; Add(this, addAddr, 12, 524, 236, 32);
            usb.Text = "Wi-Fi setup over USB..."; Add(this, usb, 12, 562, 236, 32);
            Add(this, tabs, 260, 12, 528, 586);

            BuildCharging(Page("Charging"));
            BuildTimes(Page("Timer and hours"));
            BuildHistory(Page("History"));
            BuildSocket(Page("Socket"));

            list.DrawItem += DrawSock;
            list.SelectedIndexChanged += delegate { var s = list.SelectedItem as Sock; if (s != null && s != cur) Select(s); };
            search.Click += delegate { Search(); };
            addAddr.Click += delegate
            {
                string a = Ask.Text(this, "Add a socket", "Its address, e.g. 192.168.1.69 or smartsocket-157698.local", false);
                if (!string.IsNullOrEmpty(a)) AddByAddress(Normal(a));
            };
            usb.Click += delegate { UsbSetup(); };
            tabs.SelectedIndexChanged += delegate { if (tabs.SelectedTab != null && tabs.SelectedTab.Text == "History") LoadHistory(); };

            // ---- notification area ----
            tray.Icon = Icon; tray.Text = "Smart Socket"; tray.Visible = true;
            var menu = new ContextMenuStrip();
            menu.Items.Add("Open", null, delegate { ShowWindow(); });
            menu.Items.Add("Exit", null, delegate { quitting = true; Close(); });
            tray.ContextMenuStrip = menu;
            tray.DoubleClick += delegate { ShowWindow(); };
            FormClosing += delegate(object s, FormClosingEventArgs e)
            {
                if (!quitting && ShotPath == null && optTray.Checked && e.CloseReason == CloseReason.UserClosing)
                {
                    e.Cancel = true; Hide();
                    tray.ShowBalloonTip(4000, "Smart Socket", "Still watching your sockets here. Right-click the icon to exit.", ToolTipIcon.Info);
                }
            };
            FormClosed += delegate { tray.Visible = false; };

            timer.Interval = 3000; timer.Tick += delegate { ticks++; SyncBle(); if (ticks % 2 == 0) WatchPorts(); if (ticks % 5 == 0) CheckBle(); PollAll(); CheckShot(); };
            Shown += delegate
            {
                if (startInTray) Hide();
                if (startAddress == null) foreach (var s in Store.Known()) socks.Add(s);
                if (Links.UsbOn) foreach (var pn in SerialPort.GetPortNames()) portsSeen.Add(pn);   // Search() asks them
                RefreshList();
                timer.Start();
                CheckBle();
                if (startAddress != null) AddByAddress(Normal(startAddress));
                else { if (socks.Count > 0) Select(socks[0]); else ShowCur(); Search(); }
            };
        }

        // ------------------------------------------------------------------ layout
        TabPage Page(string title) { var p = new TabPage(title) { UseVisualStyleBackColor = true }; tabs.TabPages.Add(p); return p; }
        static void Add(Control parent, Control c, int x, int y) { c.Location = new Point(x, y); parent.Controls.Add(c); }
        static void Add(Control parent, Control c, int x, int y, int w, int h) { c.SetBounds(x, y, w, h); parent.Controls.Add(c); }
        static Label Note(string text) { return new Label { Text = text, AutoSize = false, ForeColor = SystemColors.GrayText }; }
        static Label Heading(string text) { return new Label { Text = text, AutoSize = true, Font = new Font("Segoe UI", 10f, FontStyle.Bold) }; }

        void BuildCharging(TabPage p)
        {
            int x = 16, w = 486, y = 14;
            Add(p, dot, x, y + 8, 22, 22);
            headline.Font = new Font("Segoe UI", 16f, FontStyle.Bold); headline.AutoSize = true; headline.Text = "Searching";
            Add(p, headline, x + 32, y - 4);
            detail.AutoSize = true; detail.ForeColor = SystemColors.GrayText; Add(p, detail, x + 35, y + 28); y += 62;
            Add(p, bar, x, y, w, 26); y += 30;
            levelText.TextAlign = ContentAlignment.MiddleCenter; levelText.ForeColor = SystemColors.GrayText; Add(p, levelText, x, y, w, 22); y += 24;
            chargeFor.TextAlign = ContentAlignment.MiddleCenter; chargeFor.ForeColor = SystemColors.GrayText; Add(p, chargeFor, x, y, w, 22); y += 30;
            boost.Text = "Charge to 100 % this once"; Add(p, boost, x, y, w, 34); y += 48;

            Add(p, new Label { Text = "Stop charging at", AutoSize = true }, x, y);
            upperVal.TextAlign = ContentAlignment.MiddleRight; Add(p, upperVal, x + w - 70, y, 70, 22); y += 22;
            SetupBar(upper, 20, 100); Add(p, upper, x - 6, y, w + 12, 40); y += 44;
            Add(p, new Label { Text = "Start charging at", AutoSize = true }, x, y);
            lowerVal.TextAlign = ContentAlignment.MiddleRight; Add(p, lowerVal, x + w - 70, y, 70, 22); y += 22;
            SetupBar(lower, 5, 95); Add(p, lower, x - 6, y, w + 12, 40); y += 50;

            int third = w / 3;
            Mode(p, modeAuto, "Use limits", x, y, third);
            Mode(p, modeOn, "Always charge", x + third, y, third);
            Mode(p, modeOff, "Never charge", x + 2 * third, y, w - 2 * third); y += 48;

            Add(p, new Label { Text = "Most current to offer the phone", AutoSize = true }, x, y); y += 24;
            maxMa.DropDownStyle = ComboBoxStyle.DropDownList;
            maxMa.Items.AddRange(new object[] { "Basic (up to 0.9 A)", "1.5 A", "3 A" });
            Add(p, maxMa, x, y, w, 27);

            dot.Paint += delegate(object s, PaintEventArgs e)
            {
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                using (var b = new SolidBrush(DotColor(cur))) e.Graphics.FillEllipse(b, 1, 1, 19, 19);
            };
            bar.Paint += PaintBar;
            boost.Click += delegate { Command("/api/boost?on=" + (St != null && St.boost ? "0" : "1")); };
            upper.Scroll += delegate { if (lower.Value > upper.Value - 5) lower.Value = Math.Max(lower.Minimum, upper.Value - 5); Hold(); ShowLimits(); };
            lower.Scroll += delegate { if (lower.Value > upper.Value - 5) upper.Value = Math.Min(upper.Maximum, lower.Value + 5); Hold(); ShowLimits(); };
            upper.MouseUp += delegate { SendLimits(); }; lower.MouseUp += delegate { SendLimits(); };
            upper.KeyUp += delegate { SendLimits(); }; lower.KeyUp += delegate { SendLimits(); };
            modeAuto.Click += delegate { Command("/api/config?mode=auto"); };
            modeOn.Click += delegate { Command("/api/config?mode=on"); };
            modeOff.Click += delegate { Command("/api/config?mode=off"); };
            maxMa.SelectionChangeCommitted += delegate { Command("/api/config?max_ma=" + Currents[maxMa.SelectedIndex]); };
            upper.Value = 80; lower.Value = 40; ShowLimits();
        }

        void BuildTimes(TabPage p)
        {
            int x = 16, w = 486, y = 16;
            Add(p, Heading("Charge timer"), x, y); y += 28;
            timerOn.Text = "Stop charging after"; timerOn.AutoSize = true; Add(p, timerOn, x, y + 2);
            timerMin.Minimum = 5; timerMin.Maximum = 720; timerMin.Increment = 5; timerMin.Value = 60; Add(p, timerMin, x + 180, y, 80, 27);
            Add(p, new Label { Text = "minutes of charging", AutoSize = true }, x + 268, y + 3); y += 34;
            Add(p, Note("Counted from when this charge starts. Useful for a quick top-up."), x, y, w, 40); y += 50;

            Add(p, Heading("Charging hours"), x, y); y += 28;
            hoursOn.Text = "Only charge between"; hoursOn.AutoSize = true; Add(p, hoursOn, x, y + 2);
            foreach (var d in new[] { hoursFrom, hoursTo }) { d.Format = DateTimePickerFormat.Custom; d.CustomFormat = "HH:mm"; d.ShowUpDown = true; }
            Add(p, hoursFrom, x + 180, y, 80, 27);
            Add(p, new Label { Text = "and", AutoSize = true }, x + 268, y + 3);
            Add(p, hoursTo, x + 304, y, 80, 27); y += 34;
            Add(p, Note("For example 23:00 to 07:00 for cheaper night-time electricity. The socket takes the time from the internet (France time)."), x, y, w, 40); y += 50;

            Add(p, Heading("Safety"), x, y); y += 28;
            Add(p, new Label { Text = "Charge anyway after", AutoSize = true }, x, y + 3);
            safetyH.Minimum = 0; safetyH.Maximum = 72; safetyH.Value = 0; Add(p, safetyH, x + 180, y, 80, 27);
            Add(p, new Label { Text = "hours with no battery report", AutoSize = true }, x + 268, y + 3); y += 34;
            Add(p, Note("0 switches this off. It covers the phone going quiet, for example when its app has been closed."), x, y, w, 40); y += 54;

            applyTimes.Text = "Save these settings"; Add(p, applyTimes, x, y, 200, 34);
            timesNote.ForeColor = SystemColors.GrayText; timesNote.AutoSize = false; Add(p, timesNote, x + 212, y + 7, w - 212, 24);

            EventHandler dirty = delegate { if (!filling) { timesDirty = true; timesNote.Text = "Not saved yet"; } };
            timerOn.CheckedChanged += dirty; hoursOn.CheckedChanged += dirty; timerMin.ValueChanged += dirty; safetyH.ValueChanged += dirty;
            hoursFrom.ValueChanged += dirty; hoursTo.ValueChanged += dirty;
            applyTimes.Click += delegate
            {
                string q = "/api/config?timer_min=" + (timerOn.Checked ? (int)timerMin.Value : 0) +
                           "&sched_on=" + (hoursOn.Checked ? 1 : 0) + "&sched_start=" + hoursFrom.Value.ToString("HH:mm") +
                           "&sched_end=" + hoursTo.Value.ToString("HH:mm") + "&safety_h=" + (int)safetyH.Value;
                timesDirty = false; timesNote.Text = "Saved";
                Command(q);
            };
        }

        void BuildHistory(TabPage p)
        {
            historyNote.Text = "The last 20 charging runs, newest first."; historyNote.ForeColor = SystemColors.GrayText; historyNote.AutoSize = true;
            Add(p, historyNote, 16, 16);
            refreshHistory.Text = "Refresh"; Add(p, refreshHistory, 402, 10, 100, 30);
            history.View = View.Details; history.FullRowSelect = true; history.GridLines = true; history.HeaderStyle = ColumnHeaderStyle.Nonclickable;
            history.Columns.Add("Started", 150); history.Columns.Add("Lasted", 96); history.Columns.Add("From", 58); history.Columns.Add("To", 58);
            history.Columns.Add("Why it stopped", 118);
            Add(p, history, 16, 48, 486, 496);
            refreshHistory.Click += delegate { LoadHistory(); };
        }

        void BuildSocket(TabPage p)
        {
            int x = 16, w = 486, y = 16;
            Add(p, new Label { Text = "Name", AutoSize = true }, x, y); y += 24;
            Add(p, nameBox, x, y, w - 110, 27); rename.Text = "Rename"; Add(p, rename, x + w - 100, y - 1, 100, 29); y += 40;

            Add(p, new Label { Text = "Light brightness", AutoSize = true }, x, y);
            ledVal.TextAlign = ContentAlignment.MiddleRight; Add(p, ledVal, x + w - 70, y, 70, 22); y += 22;
            led.Minimum = 0; led.Maximum = 100; led.TickFrequency = 10; led.SmallChange = 5; led.LargeChange = 10;
            Add(p, led, x - 6, y, w + 12, 40); y += 48;

            int half = (w - 8) / 2;
            find.Text = "Find me (flash its light)"; Add(p, find, x, y, half, 32);
            page.Text = "Open its own page"; Add(p, page, x + half + 8, y, half, 32); y += 38;
            pinBtn.Text = "Set or change the PIN..."; Add(p, pinBtn, x, y, half, 32);
            update.Text = "Load a new program..."; Add(p, update, x + half + 8, y, half, 32); y += 36;
            updateNote.ForeColor = SystemColors.GrayText; updateNote.AutoSize = false; Add(p, updateNote, x, y, w, 22); y += 28;

            facts.AutoSize = false; facts.ForeColor = SystemColors.GrayText; Add(p, facts, x, y, w, 160); y += 166;

            Add(p, Heading("This app"), x, y); y += 26;
            optNotify.Text = "Tell me when charging starts or stops"; optNotify.AutoSize = true; Add(p, optNotify, x, y); y += 26;
            optTray.Text = "Keep watching in the notification area when closed"; optTray.AutoSize = true; Add(p, optTray, x, y); y += 26;
            optStartup.Text = "Start with Windows (in the notification area)"; optStartup.AutoSize = true; Add(p, optStartup, x, y);

            optNotify.Checked = Store.Option("notify", true);
            optTray.Checked = Store.Option("tray", true);
            optStartup.Checked = StartsWithWindows();
            optNotify.CheckedChanged += delegate { Store.SetOption("notify", optNotify.Checked); };
            optTray.CheckedChanged += delegate { Store.SetOption("tray", optTray.Checked); };
            optStartup.CheckedChanged += delegate { SetStartWithWindows(optStartup.Checked); };

            rename.Click += delegate
            {
                string n = nameBox.Text.Trim();
                if (n.Length == 0 || cur == null) return;
                Command("/api/name?name=" + Uri.EscapeDataString(n));
            };
            nameBox.KeyDown += delegate(object s, KeyEventArgs e) { if (e.KeyCode == Keys.Enter) { rename.PerformClick(); e.SuppressKeyPress = true; } };
            led.Scroll += delegate { ledVal.Text = led.Value + " %"; Hold(); };
            led.MouseUp += delegate { Command("/api/config?led=" + led.Value); };
            led.KeyUp += delegate { Command("/api/config?led=" + led.Value); };
            find.Click += delegate
            {
                var s = cur; if (s == null) return;
                string pin = Store.Pin(s.Id);
                Task.Factory.StartNew(delegate { Links.Exchange(s, "POST", "/api/identify", pin, 4000, false); });
            };
            page.Click += delegate { if (cur != null && cur.Url.Length > 0) try { System.Diagnostics.Process.Start(cur.Url + "/"); } catch { } };
            pinBtn.Click += delegate { ChangePin(); };
            update.Click += delegate { LoadProgram(); };
        }

        static void SetupBar(TrackBar t, int min, int max) { t.Minimum = min; t.Maximum = max; t.TickFrequency = 5; t.SmallChange = 5; t.LargeChange = 5; }
        static void Mode(Control p, RadioButton r, string text, int x, int y, int w)
        {
            r.Appearance = Appearance.Button; r.Text = text; r.TextAlign = ContentAlignment.MiddleCenter; r.AutoCheck = false;
            Add(p, r, x, y, w, 34);
        }
        void Hold() { holdUntil = DateTime.Now.AddSeconds(4); }
        void ShowLimits() { upperVal.Text = upper.Value + " %"; lowerVal.Text = lower.Value + " %"; bar.Invalidate(); }

        static int Snap(int v) { return (int)Math.Round(v / 5.0) * 5; }
        void SendLimits()
        {
            upper.Value = Math.Min(upper.Maximum, Math.Max(upper.Minimum, Snap(upper.Value)));
            lower.Value = Math.Min(lower.Maximum, Math.Max(lower.Minimum, Snap(lower.Value)));
            ShowLimits();
            Command("/api/config?upper=" + upper.Value + "&lower=" + lower.Value);
        }

        static string Normal(string a)
        {
            a = (a ?? "").Trim().TrimEnd('/');
            if (a.Length == 0) return "";
            if (!a.StartsWith("http://") && !a.StartsWith("https://")) a = "http://" + a;
            return a;
        }

        // ------------------------------------------------------------------ the socket list
        static Color DotColor(Sock s)
        {
            if (s == null || s.Last == null || s.Code != 200) return Off;
            if (s.Last.hot) return Bad;
            if (!s.Last.settled || !s.Last.phone) return Off;
            return s.Last.charging ? Go : Stop;
        }

        static string StateLine(Sock s)
        {
            if (s.Code == 401) return "PIN needed";
            if (s.Code == 429) return "Locked: too many wrong PINs";
            if (s.Code == -1) return "Connecting";
            if (s.Last == null || s.Code != 200) return "No answer";
            var t = s.Last;
            if (!t.settled) return "Starting";
            if (!t.phone) return "No phone";
            if (t.hot) return "Too warm, paused";
            string lvl = t.level >= 0 ? t.level + " %" : "no report";
            return (t.charging ? "Charging, " : "Paused, ") + lvl;
        }

        void DrawSock(object sender, DrawItemEventArgs e)
        {
            if (e.Index < 0 || e.Index >= list.Items.Count) return;
            var s = (Sock)list.Items[e.Index];
            bool sel = (e.State & DrawItemState.Selected) != 0;
            var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias;
            using (var bg = new SolidBrush(sel ? Color.FromArgb(220, 236, 252) : list.BackColor)) g.FillRectangle(bg, e.Bounds);
            using (var b = new SolidBrush(DotColor(s))) g.FillEllipse(b, e.Bounds.X + 10, e.Bounds.Y + 16, 16, 16);
            using (var bold = new Font(Font, FontStyle.Bold))
                TextRenderer.DrawText(g, s.Label(), bold, new Point(e.Bounds.X + 34, e.Bounds.Y + 5), SystemColors.ControlText);
            TextRenderer.DrawText(g, StateLine(s) + ViaText(s, ", "), Font, new Point(e.Bounds.X + 34, e.Bounds.Y + 26), SystemColors.GrayText);
        }

        // ", via USB" when the last answer came by a link (lead goes in front).
        static string ViaText(Sock s, string lead)
        {
            var v = s.Via;
            return v != Link.None && s.Code > 0 ? lead + "via " + Links.Name(v) : "";
        }

        void RefreshList()
        {
            list.BeginUpdate();
            var keep = cur;
            list.Items.Clear();
            foreach (var s in socks) list.Items.Add(s);
            if (keep != null && socks.Contains(keep)) list.SelectedItem = keep;
            list.EndUpdate();
            Store.SetKnown(socks);
        }

        // Entries found by Wi-Fi, USB and Bluetooth are one socket when they have the same id.
        Sock Merge(Sock s)
        {
            var old = socks.FirstOrDefault(o => (s.Id.Length > 0 && o.Id == s.Id) || (s.Url.Length > 0 && o.Url == s.Url));
            if (old == null) { socks.Add(s); return s; }
            if (s.Url.Length > 0) old.Url = s.Url;
            if (s.Port.Length > 0)
            {
                old.Port = s.Port;
                foreach (var o in socks) if (o != old && o.Port == s.Port) o.Port = "";   // a port has one socket
            }
            if (s.Ble != 0) { old.Ble = s.Ble; old.BleName = s.BleName; }
            if (s.Id.Length > 0) old.Id = s.Id; if (s.Name.Length > 0) old.Name = s.Name;
            return old;
        }

        // Socket seen over Bluetooth: give the entry with that id its address, or add it.
        void SyncBle()
        {
            if (!Links.BleOn) return;
            bool changed = false;
            List<BleHub.Seen> seenNow;
            try { seenNow = BleHub.Sightings(); } catch { return; }
            foreach (var b in seenNow)
            {
                if (b.Id.Length == 0) continue;
                var s = socks.FirstOrDefault(o => o.Id == b.Id);
                if (s == null) { socks.Add(new Sock { Id = b.Id, Ble = b.Addr, BleName = b.Name }); changed = true; }
                else if (s.Ble != b.Addr || s.BleName != b.Name) { s.Ble = b.Addr; s.BleName = b.Name; changed = true; }
            }
            if (!changed) return;
            RefreshList();
            if (cur == null && socks.Count > 0) Select(socks[0]);
        }

        void CheckBle()
        {
            if (!Links.BleOn || bleChecking) return;
            bleChecking = true;
            Task.Factory.StartNew(delegate
            {
                try { BleHub.Check(); BleHub.IdentifyUnnamed(); } catch { }
                bleChecking = false;
            });
        }

        // A serial port that appeared since the last look (the socket was plugged in): ask it.
        void WatchPorts()
        {
            if (!Links.UsbOn || portsChecking) return;
            var now = SerialPort.GetPortNames();
            portsSeen.RemoveWhere(p => !now.Contains(p));
            var fresh = now.Where(p => !portsSeen.Contains(p)).ToList();
            if (fresh.Count == 0) return;
            foreach (var p in fresh) portsSeen.Add(p);
            portsChecking = true;
            Task.Factory.StartNew(delegate
            {
                List<Sock> found;
                try { found = Links.ScanUsb(fresh); } catch { found = new List<Sock>(); }
                try { BeginInvoke((MethodInvoker)delegate { portsChecking = false; Found(found); }); } catch { }
            });
        }

        void Found(List<Sock> found)
        {
            if (found.Count == 0) return;
            foreach (var s in found) Merge(s);
            RefreshList();
            if (cur == null && socks.Count > 0) Select(socks[0]);
            PollAll();
        }

        void Search()
        {
            search.Enabled = false; search.Text = "Searching...";
            CheckBle();
            Task.Factory.StartNew(delegate
            {
                var found = new List<Sock>();
                // USB answers in a moment; the network search takes longer, so the USB sockets are listed first.
                if (Links.UsbOn)
                    Task.Factory.StartNew(delegate
                    {
                        List<Sock> usb;
                        try { usb = Links.ScanUsb(SerialPort.GetPortNames()); } catch { usb = new List<Sock>(); }
                        try { BeginInvoke((MethodInvoker)delegate { Found(usb); }); } catch { }
                    });
                if (Links.WifiOn) found.AddRange(Net.Scan());
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        search.Enabled = true; search.Text = "Search for sockets";
                        foreach (var s in found) Merge(s);
                        RefreshList();
                        if (cur == null && socks.Count > 0) Select(socks[0]);
                        if (socks.Count == 0) ShowCur();
                        PollAll();
                    });
                }
                catch { }
            });
        }

        void AddByAddress(string url)
        {
            if (url.Length == 0) return;
            Task.Factory.StartNew(delegate
            {
                var r = Net.Call(url + "/api/info", "GET", null, 4000);
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        var s = new Sock { Url = url };
                        if (r.Code == 200) { s.Id = Json.S(r.Data, "id"); s.Name = Json.S(r.Data, "name"); }
                        s = Merge(s);
                        RefreshList();
                        Select(s);
                    });
                }
                catch { }
            });
        }

        void Select(Sock s)
        {
            cur = s; s.PinAsked = false;
            if (list.SelectedItem != s) list.SelectedItem = s;
            timesDirty = false; timesNote.Text = "";
            history.Items.Clear(); updateNote.Text = "";
            nameBox.Text = s.Name;
            ShowCur();
            Poll(s);
            if (tabs.SelectedTab != null && tabs.SelectedTab.Text == "History") LoadHistory();
        }

        void ShowWindow() { Show(); WindowState = FormWindowState.Normal; Activate(); }

        // ------------------------------------------------------------------ talking to the sockets
        void PollAll() { foreach (var s in socks.ToList()) Poll(s); }

        void Poll(Sock s)
        {
            if (s.InFlight) return;
            s.InFlight = true;
            string pin = Store.Pin(s.Id);
            Task.Factory.StartNew(delegate
            {
                var r = Links.Exchange(s, "GET", "/api/status", pin, 4000, true);
                try { BeginInvoke((MethodInvoker)delegate { s.InFlight = false; Took(s, r); }); } catch { }
            });
        }

        // A command for the chosen socket. Most answers are its full status; the rest are followed by a poll.
        void Command(string path)
        {
            var s = cur;
            if (s == null) return;
            string pin = Store.Pin(s.Id);
            Task.Factory.StartNew(delegate
            {
                var r = Links.Exchange(s, "POST", path, pin, 5000, false);
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        if (r.Code == 200 && !r.Data.ContainsKey("settled"))
                        {
                            if (r.Data.ContainsKey("name")) { s.Name = Json.S(r.Data, "name"); RefreshList(); }
                            Poll(s);
                        }
                        else Took(s, r);
                    });
                }
                catch { }
            });
        }

        void Took(Sock s, Reply r)
        {
            s.Code = r.Code;
            if (r.Code == 429) s.WaitS = Math.Max(1, Json.I(r.Data, "wait_s"));
            if (r.Code == 200)
            {
                var t = Status.Parse(r.Data);
                s.Last = t;
                string id = Json.S(r.Data, "id");
                if (s.Id.Length == 0 && id.Length > 0)
                {
                    s.Id = id;
                    var twin = socks.FirstOrDefault(o => o != s && o.Id == id);
                    if (twin != null)
                    {
                        // the same socket was already listed by another link: keep one entry
                        if (twin.Url.Length == 0) twin.Url = s.Url;
                        socks.Remove(s);
                        RefreshList();
                        if (cur == s) Select(twin);
                        return;
                    }
                }
                // Reached by USB or Bluetooth: its own report says where it is on Wi-Fi, so Wi-Fi can be tried (and preferred) too.
                if (s.Via != Link.Wifi && t.ip.Length > 0 && t.ip != "0.0.0.0")
                {
                    string host = s.Url.Replace("http://", "");
                    IPAddress ignored;
                    if (s.Url.Length == 0 || (IPAddress.TryParse(host, out ignored) && host != t.ip)) { s.Url = "http://" + t.ip; Store.SetKnown(socks); }
                }
                if (t.name.Length > 0 && t.name != s.Name) { s.Name = t.name; Store.SetKnown(socks); }
                bool charging = t.settled && t.charging;
                if (s.WasCharging.HasValue && s.WasCharging.Value != charging && optNotify.Checked) Notify(s, t, charging);
                s.WasCharging = charging;
            }
            list.Invalidate();
            if (s == cur) ShowCur();
            if (s == cur && r.Code == 401 && !s.PinAsked && Visible && ShotPath == null)
            {
                s.PinAsked = true;
                string p = Ask.Text(this, "PIN", "Type the PIN of " + s.Label() + ".", true);
                if (p != null) { Store.SetPin(s.Id, p); s.PinAsked = false; Poll(s); }
            }
            tray.Text = TrayText();
        }

        void Notify(Sock s, Status t, bool charging)
        {
            string at = t.level >= 0 ? " at " + t.level + " %" : "";
            string msg = charging ? "Charging started" + at : "Charging stopped" + at + Why(t, ": ");
            tray.ShowBalloonTip(6000, s.Label(), msg, ToolTipIcon.Info);
        }

        string TrayText()
        {
            string t = "Smart Socket";
            foreach (var s in socks.Take(3)) t += "\n" + s.Label() + ": " + StateLine(s);
            return t.Length > 63 ? t.Substring(0, 63) : t;
        }

        // ------------------------------------------------------------------ showing the chosen socket
        void ShowCur()
        {
            filling = true;
            try { ShowCharging(); ShowTimes(); ShowSocket(); }
            finally { filling = false; }
            dot.Invalidate(); bar.Invalidate();
        }

        void ShowCharging()
        {
            var s = cur; var t = St;
            if (s == null) { headline.Text = "No socket yet"; detail.Text = "Search for sockets (Wi-Fi, USB, Bluetooth), or set one up over USB"; levelText.Text = ""; chargeFor.Text = ""; return; }
            if (s.Code == 401) { headline.Text = "PIN needed"; detail.Text = "Type its PIN to control it"; levelText.Text = ""; chargeFor.Text = ""; return; }
            if (s.Code == 429) { headline.Text = "Locked"; detail.Text = "Too many wrong PINs: wait " + s.WaitS + " s"; return; }
            if (t == null) { headline.Text = s.Code == -1 ? "Connecting" : "No answer"; detail.Text = s.Where() + (s.Code == -1 ? "" : ": is it plugged in?"); levelText.Text = ""; chargeFor.Text = ""; return; }
            if (s.Code != 200) { headline.Text = "No answer"; detail.Text = "Showing what it said last. Is it plugged in?"; }
            else if (!t.settled) { headline.Text = "Starting"; detail.Text = "Waiting for the charger to settle"; }
            else if (!t.phone) { headline.Text = "No phone"; detail.Text = "Plug the phone into the socket cable"; }
            else if (t.charging)
            {
                headline.Text = "Charging";
                detail.Text = t.mode == "on" ? "Always charge is on" : t.boost ? "Going to 100 % this once"
                            : t.timer_min > 0 && t.timer_left_s >= 0 ? "Timer: " + (t.timer_left_s + 59) / 60 + " min left" : "Until " + t.upper + " %";
            }
            else { headline.Text = t.hot ? "Too warm" : "Paused"; detail.Text = Cap(t.mode == "off" ? "never charge is on" : Why(t, "")); }
            levelText.Text = t.level < 0 ? "No battery report yet" : "Phone at " + t.level + " %, reported " + Age(t.level_age_s);
            chargeFor.Text = t.charging && t.charge_s >= 0 ? "Charging for " + Duration(t.charge_s) + ", offering " + Amps(Math.Min(t.max_ma, t.src_ma)) : "";
            boost.Text = t.boost ? "Going to 100 % (click to cancel)" : "Charge to 100 % this once";
            if (DateTime.Now > holdUntil)
            {
                upper.Value = Math.Min(upper.Maximum, Math.Max(upper.Minimum, t.upper));
                lower.Value = Math.Min(lower.Maximum, Math.Max(lower.Minimum, t.lower));
                ShowLimits();
            }
            modeAuto.Checked = t.mode == "auto"; modeOn.Checked = t.mode == "on"; modeOff.Checked = t.mode == "off";
            if (!maxMa.DroppedDown) maxMa.SelectedIndex = Math.Max(0, Array.IndexOf(Currents, t.max_ma));
        }

        void ShowTimes()
        {
            var t = St;
            if (t == null || timesDirty) return;
            timerOn.Checked = t.timer_min > 0;
            if (t.timer_min > 0) timerMin.Value = Math.Min(timerMin.Maximum, Math.Max(timerMin.Minimum, t.timer_min));
            hoursOn.Checked = t.sched_on;
            hoursFrom.Value = TimeOf(t.sched_start, hoursFrom.Value); hoursTo.Value = TimeOf(t.sched_end, hoursTo.Value);
            safetyH.Value = Math.Min(safetyH.Maximum, Math.Max(0, t.safety_h));
        }

        void ShowSocket()
        {
            var s = cur; var t = St;
            if (s == null) { facts.Text = ""; return; }
            bool wifi = s.Via == Link.Wifi && s.Code > 0;
            bool ble = Links.BleOn && s.Ble != 0 && BleHub.Usable;
            update.Enabled = (wifi || ble) && !uploading;
            page.Enabled = wifi;
            if (!uploading && (updateNote.Text.Length == 0 || updateNote.Text.StartsWith("New programs")))
                updateNote.Text = wifi || ble ? "" : "New programs load over Wi-Fi or Bluetooth: switch the laptop's Bluetooth on.";
            if (!nameBox.Focused && s.Name.Length > 0 && nameBox.Text != s.Name) nameBox.Text = s.Name;
            if (t == null) { facts.Text = LinkLines(s); return; }
            if (DateTime.Now > holdUntil) { led.Value = Math.Min(100, Math.Max(0, t.led)); ledVal.Text = led.Value + " %"; }
            facts.Text = LinkLines(s) + "\n" +
                "Address: " + (s.Url.Length > 0 ? s.Url.Replace("http://", "") : "none yet") + (t.host.Length > 0 ? "   (" + t.host + ")" : "") + "\n" +
                "Wi-Fi: " + t.ssid + (t.rssi != 0 ? ", signal " + t.rssi + " dBm (" + Signal(t.rssi) + ")" : "") + "\n" +
                "Charger offers " + Amps(t.src_ma) + ". Controller at " + t.temp_c.ToString("0") + " C" + (t.time.Length > 0 ? ", its clock says " + t.time : "") + "\n" +
                "Program version " + t.fw + ", running for " + Duration(t.uptime_s) + "\n" +
                (t.pin_set ? "Protected by a PIN." : "No PIN: anyone on this Wi-Fi can change it.");
        }

        // "Connected via USB (COM11)" and how each other way to it is doing.
        static string LinkLines(Sock s)
        {
            var v = s.Via;
            string now = v != Link.None && s.Code > 0
                ? "Connected via " + Links.Name(v) + " (" + (v == Link.Wifi ? s.Url.Replace("http://", "") : v == Link.Usb ? s.Port : s.BleName) + ")"
                : s.Code == -1 ? "Connecting..." : "Not connected: no link answers";
            var others = new List<string>();
            Dictionary<Link, string> failed; lock (s.Retry) failed = new Dictionary<Link, string>(s.Failed);
            Func<Link, string> how = l => l == v && s.Code > 0 ? "in use" : failed.ContainsKey(l) ? "no answer" : "standing by";
            others.Add("Wi-Fi " + (!Links.WifiOn ? "switched off" : s.Url.Length == 0 ? "address unknown" : how(Link.Wifi)));
            others.Add("USB " + (!Links.UsbOn ? "switched off" : s.Port.Length == 0 ? "not plugged in" : s.Port + " " + how(Link.Usb)));
            string bs = BleHub.State;
            others.Add("Bluetooth " + (!Links.BleOn ? "switched off" : bs == "off" ? "is off on this computer" : bs == "no adapter" ? "missing on this computer"
                                       : s.Ble == 0 ? "not seen" : how(Link.Ble)));
            return now + "\nLinks: " + string.Join(", ", others);
        }

        static string Cap(string s) { return s.Length == 0 ? s : char.ToUpper(s[0]) + s.Substring(1); }

        static DateTime TimeOf(string hhmm, DateTime fallback)
        {
            try
            {
                var p = hhmm.Split(':');
                return DateTime.Today.AddHours(int.Parse(p[0])).AddMinutes(int.Parse(p[1]));
            }
            catch { return fallback; }
        }

        // Why charging is paused, as the end of a sentence ("lead" goes in front when there is a reason).
        static string Why(Status t, string lead)
        {
            switch (t.reason)
            {
                case "hot": return lead + "too warm, starts again when it cools";
                case "schedule": return lead + "outside the charging hours";
                case "timer": return lead + "the timer has run out";
                case "limit": return lead + "limit reached, starts again at " + t.lower + " %";
                case "off": return lead + "never charge is on";
                default: return lead.Length > 0 ? "" : "starts again at " + t.lower + " %";
            }
        }

        static string WhyRun(string why)
        {
            switch (why)
            {
                case "limit": return "Limit reached";
                case "timer": return "Timer";
                case "schedule": return "Charging hours";
                case "hot": return "Too warm";
                case "off": return "Switched off";
                case "no_phone": return "Phone unplugged";
                case "boot": return "Socket restarted";
                default: return why;
            }
        }

        static string Signal(int rssi) { return rssi >= -60 ? "good" : rssi >= -72 ? "fair" : "weak"; }
        static string Amps(int ma) { return ma >= 3000 ? "3 A" : ma >= 1500 ? "1.5 A" : "basic current"; }
        static string Age(int s)
        {
            if (s < 0) return "";
            if (s < 90) return s + " s ago";
            if (s < 5400) return (int)Math.Round(s / 60.0) + " min ago";
            return (int)Math.Round(s / 3600.0) + " h ago";
        }
        static string Duration(int s)
        {
            if (s < 0) return "";
            if (s < 3600) return Math.Max(1, s / 60) + " min";
            if (s < 86400) return s / 3600 + " h " + (s % 3600) / 60 + " min";
            return s / 86400 + " days " + (s % 86400) / 3600 + " h";
        }

        void PaintBar(object sender, PaintEventArgs e)
        {
            var g = e.Graphics; int w = bar.Width, h = bar.Height;
            g.Clear(Color.FromArgb(226, 229, 234));
            var t = St;
            if (t != null && t.level > 0)
                using (var b = new SolidBrush(t.charging ? Go : Stop)) g.FillRectangle(b, 0, 0, w * t.level / 100, h);
            using (var p = new Pen(Color.FromArgb(60, 60, 70), 2))
            {
                g.DrawLine(p, w * lower.Value / 100, 0, w * lower.Value / 100, h);
                g.DrawLine(p, w * upper.Value / 100, 0, w * upper.Value / 100, h);
            }
        }

        // ------------------------------------------------------------------ history, PIN, program, USB
        void LoadHistory()
        {
            var s = cur;
            if (s == null) return;
            historyNote.Text = "Loading...";
            string pin = Store.Pin(s.Id);
            Task.Factory.StartNew(delegate
            {
                var r = Links.Exchange(s, "GET", "/api/history", pin, 5000, false);
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        if (s != cur) return;
                        history.Items.Clear();
                        if (r.Code != 200) { historyNote.Text = r.Code == 401 ? "PIN needed." : "No answer."; return; }
                        var runs = Net.Runs(r.Data);
                        foreach (var run in runs)
                        {
                            long start = Json.L(run, "start");
                            string when = Json.I(run, "clock") == 1
                                ? new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddSeconds(start).ToLocalTime().ToString("ddd d MMM HH:mm")
                                : "time unknown";
                            int lvl0 = run.ContainsKey("lvl0") ? Json.I(run, "lvl0") : -1, lvl1 = run.ContainsKey("lvl1") ? Json.I(run, "lvl1") : -1;
                            history.Items.Add(new ListViewItem(new[] {
                                when, Duration(Json.I(run, "dur_s")), lvl0 >= 0 ? lvl0 + " %" : "?", lvl1 >= 0 ? lvl1 + " %" : "?", WhyRun(Json.S(run, "why")) }));
                        }
                        historyNote.Text = runs.Count == 0 ? "No charging runs yet." : "The last " + runs.Count + " charging runs, newest first.";
                    });
                }
                catch { }
            });
        }

        void ChangePin()
        {
            var s = cur;
            if (s == null || St == null) return;
            string p = Ask.Text(this, "PIN", "New PIN, 4 to 12 characters. Leave empty to remove the PIN.", true);
            if (p == null) return;
            if (p.Length > 0 && (p.Length < 4 || p.Length > 12)) { MessageBox.Show(this, "A PIN has 4 to 12 characters.", "PIN"); return; }
            string old = Store.Pin(s.Id);
            Task.Factory.StartNew(delegate
            {
                var r = Links.Exchange(s, "POST", "/api/pin?new=" + Uri.EscapeDataString(p), old, 4000, false);
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        if (r.Code == 200) { Store.SetPin(s.Id, p); Poll(s); MessageBox.Show(this, p.Length > 0 ? "PIN set." : "PIN removed.", "PIN"); }
                        else MessageBox.Show(this, "The socket did not accept it (" + (r.Code > 0 ? r.Code.ToString() : r.Error) + ").", "PIN");
                    });
                }
                catch { }
            });
        }

        // Upload a program file (the .bin from "Export compiled binary" or arduino-cli --output-dir) over
        // Wi-Fi when the socket answers that way, otherwise over Bluetooth (slower, works on any network).
        void LoadProgram()
        {
            var s = cur;
            if (s == null || St == null) return;
            string file;
            using (var d = new OpenFileDialog { Title = "Choose the new program", Filter = "Program file (*.bin)|*.bin" })
            {
                if (d.ShowDialog(this) != DialogResult.OK) return;
                file = d.FileName;
            }
            byte[] data;
            try { data = File.ReadAllBytes(file); } catch (Exception e) { updateNote.Text = e.Message; return; }
            if (data.Length < 100000 || data[0] != 0xE9) { updateNote.Text = "That does not look like a program for the controller (use smart_socket.ino.bin)."; return; }
            if (MessageBox.Show(this, "Load " + Path.GetFileName(file) + " (" + data.Length / 1024 + " kB) into " + s.Label() +
                                "?\nCharging pauses while it loads, then the socket restarts.", "New program", MessageBoxButtons.OKCancel) != DialogResult.OK) return;
            string boundary = "----SmartSocket" + DateTime.Now.Ticks.ToString("x");
            var head = Encoding.ASCII.GetBytes("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" +
                                               Path.GetFileName(file) + "\"\r\nContent-Type: application/octet-stream\r\n\r\n");
            var tail = Encoding.ASCII.GetBytes("\r\n--" + boundary + "--\r\n");
            var body = new byte[head.Length + data.Length + tail.Length];
            Buffer.BlockCopy(head, 0, body, 0, head.Length);
            Buffer.BlockCopy(data, 0, body, head.Length, data.Length);
            Buffer.BlockCopy(tail, 0, body, head.Length + data.Length, tail.Length);
            string pin = Store.Pin(s.Id);
            update.Enabled = false; uploading = true;
            if (!(s.Via == Link.Wifi && s.Code > 0))
            {
                updateNote.Text = "Loading over Bluetooth... (2 to 4 minutes, keep the laptop near)";
                ulong addr = s.Ble;
                Task.Factory.StartNew(delegate
                {
                    string err = BleHub.Upload(addr, data, pin, (got, all) =>
                    {
                        try { BeginInvoke((MethodInvoker)delegate { updateNote.Text = "Loading over Bluetooth... " + got * 100 / all + " %"; }); } catch { }
                    });
                    try
                    {
                        BeginInvoke((MethodInvoker)delegate
                        {
                            uploading = false; update.Enabled = true;
                            updateNote.Text = err == null ? "Loaded. The socket is restarting; it is back in about 15 s."
                                            : err == "old program" ? "This socket's program is older than 1.7 and cannot take updates over Bluetooth. Load 1.7 once over Wi-Fi or USB."
                                            : "Did not load (the old program keeps running): " + err;
                        });
                    }
                    catch { }
                });
                return;
            }
            updateNote.Text = "Loading... (about half a minute)";
            Task.Factory.StartNew(delegate
            {
                var r = Net.Call(s.Url + "/api/update" + (pin.Length > 0 ? "?pin=" + Uri.EscapeDataString(pin) : ""), "POST", pin, 120000,
                                 body, "multipart/form-data; boundary=" + boundary);
                try
                {
                    BeginInvoke((MethodInvoker)delegate
                    {
                        uploading = false; update.Enabled = true;
                        updateNote.Text = r.Code == 200 ? "Loaded. The socket is restarting; it is back in about 15 s."
                                        : r.Code == 401 ? "Refused: PIN needed." : "Did not load: " + (r.Error ?? "error " + r.Code);
                    });
                }
                catch { }
            });
        }

        void UsbSetup()
        {
            using (var f = new UsbSetupForm())
            {
                f.ShowDialog(this);
                if (f.JoinedUrl == null) return;
                string pin = f.JoinedPin, url = f.JoinedUrl;
                Task.Factory.StartNew(delegate
                {
                    var r = Net.Call(url + "/api/info", "GET", null, 4000);
                    if (r.Code == 200 && !string.IsNullOrEmpty(pin)) Store.SetPin(Json.S(r.Data, "id"), pin);
                    try { BeginInvoke((MethodInvoker)delegate { AddByAddress(url); }); } catch { }
                });
            }
        }

        // ------------------------------------------------------------------ Windows bits
        static bool StartsWithWindows()
        {
            try { using (var k = Registry.CurrentUser.OpenSubKey(RunKey)) return k != null && k.GetValue("SmartSocket") != null; } catch { return false; }
        }
        static void SetStartWithWindows(bool on)
        {
            try
            {
                using (var k = Registry.CurrentUser.OpenSubKey(RunKey, true))
                {
                    if (k == null) return;
                    if (on) k.SetValue("SmartSocket", "\"" + Application.ExecutablePath + "\" --tray");
                    else k.DeleteValue("SmartSocket", false);
                }
            }
            catch { }
        }

        // A plain round icon, so the app needs no image file.
        static Icon MakeIcon(Color c)
        {
            using (var bmp = new Bitmap(32, 32))
            {
                using (var g = Graphics.FromImage(bmp))
                {
                    g.SmoothingMode = SmoothingMode.AntiAlias; g.Clear(Color.Transparent);
                    using (var b = new SolidBrush(c)) g.FillEllipse(b, 2, 2, 28, 28);
                    using (var p = new Pen(Color.White, 3)) { g.DrawLine(p, 16, 8, 16, 17); g.DrawArc(p, 9, 10, 14, 14, -60, 300); }
                }
                return Icon.FromHandle(bmp.GetHicon());
            }
        }

        void CheckShot()
        {
            if (ShotPath == null) return;
            if (ShotTab != null)
                foreach (TabPage p in tabs.TabPages)
                    if (p.Text.StartsWith(ShotTab, StringComparison.OrdinalIgnoreCase) && tabs.SelectedTab != p) { tabs.SelectedTab = p; return; }
            double age = (DateTime.Now - started).TotalSeconds;
            if (age < 7 || (St == null && age < 25)) return;
            timer.Stop();
            using (var bmp = new Bitmap(Width, Height))
            {
                DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height));
                bmp.Save(ShotPath, System.Drawing.Imaging.ImageFormat.Png);
            }
            Environment.ExitCode = St != null ? 0 : 2;
            quitting = true;
            Close();
        }
    }

    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string shot = null, addr = null, tab = null;
            // Link switches for testing: --only-wifi, --only-usb, --only-ble, --no-wifi, --no-usb, --no-ble
            var flags = args.Where(a => a.StartsWith("--only-") || a.StartsWith("--no-")).ToList();
            args = args.Where(a => !flags.Contains(a)).ToArray();
            foreach (var f in flags)
            {
                if (f.StartsWith("--only-")) { Links.WifiOn = f == "--only-wifi"; Links.UsbOn = f == "--only-usb"; Links.BleOn = f == "--only-ble"; }
                else if (f == "--no-wifi") Links.WifiOn = false;
                else if (f == "--no-usb") Links.UsbOn = false;
                else if (f == "--no-ble") Links.BleOn = false;
            }
            bool tray = args.Length > 0 && args[0] == "--tray";
            if (args.Length >= 2 && args[0] == "--shot")
            {
                shot = args[1];
                addr = args.Length >= 3 && args[2] != "-" ? args[2] : null;
                tab = args.Length >= 4 ? args[3] : null;
            }
            var form = new MainForm(addr, tray);
            form.ShotPath = shot; form.ShotTab = tab;
            Application.Run(form);
        }
    }
}
