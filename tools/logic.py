"""Decision logic of the smart charge switch, in plain Python.

This is the reference for firmware/smart_socket/smart_socket.ino: the firmware's
Logic section is a line-for-line transcription of step(). test_logic.py runs the
scenarios, and mock_server.py uses it to drive the app and the web page without hardware.

Version 1.5 adds three rules on top of the battery limits: a charge timer (for devices
that cannot report their level), charging hours (for example off-peak electricity), and
a heat cut-out from the controller's own temperature sensor.
"""

from dataclasses import dataclass

BOOT_WAIT_MS = 5000      # charger may switch 5 V off and on while it settles
ATTACH_DEBOUNCE_MS = 150  # USB-C asks for 100 to 200 ms before turning power on
DETACH_DEBOUNCE_MS = 100  # one bad reading must not look like an unplug; USB-C allows up to 650 ms
BASIC_MA, MID_MA, HIGH_MA = 500, 1500, 3000
HOT_C, COOL_C = 75, 65    # stop charging at 75 C inside the case, allow again below 65 C


@dataclass
class Config:
    upper: int = 80          # stop charging at or above this percentage
    lower: int = 40          # start charging at or below this percentage
    mode: str = "auto"       # "auto", "on" (always charge) or "off" (never charge)
    max_ma: int = BASIC_MA   # cap on the current offered to the phone
    safety_h: int = 0        # resume after this many hours without a report, 0 = never
    timer_min: int = 0       # stop after this many minutes of charging per plug-in, 0 = no timer
    sched_on: bool = False   # charge only between sched_start and sched_end (minutes after midnight)
    sched_start: int = 22 * 60
    sched_end: int = 6 * 60


@dataclass
class State:
    allow: bool = True       # the latch: True between "fell to lower" and "reached upper"
    boost: bool = False      # charge to 100 % once
    level: int = -1          # last reported percentage, -1 = none yet
    report_ms: int = -1      # when it was reported
    present_since: int = -1  # when the phone was first seen, -1 = not present
    absent_since: int = -1   # when the reading first said "no phone", -1 = phone seen on the last pass
    vbus: bool = False       # output: 5 V to the phone
    out_ma: int = BASIC_MA   # output: current offered to the phone
    dirty: bool = False      # allow changed and must be saved to flash
    hot: bool = False        # heat cut-out latched
    session_ms: int = 0      # charging time since the phone was plugged in (for the timer)
    last_ms: int = -1        # time of the previous pass
    reason: str = "boot"     # why the output is off ("" when charging)


def clamp_config(cfg: Config) -> None:
    cfg.upper = max(20, min(100, cfg.upper))
    cfg.lower = max(5, min(cfg.upper - 5, cfg.lower))
    if cfg.mode not in ("auto", "on", "off"):
        cfg.mode = "auto"
    if cfg.max_ma not in (BASIC_MA, MID_MA, HIGH_MA):
        cfg.max_ma = BASIC_MA
    cfg.safety_h = max(0, min(72, cfg.safety_h))
    cfg.timer_min = max(0, min(24 * 60, cfg.timer_min))
    cfg.sched_start = max(0, min(24 * 60 - 1, cfg.sched_start))
    cfg.sched_end = max(0, min(24 * 60 - 1, cfg.sched_end))


def report_battery(st: State, cfg: Config, level: int, now_ms: int) -> None:
    level = max(0, min(100, level))
    st.level, st.report_ms = level, now_ms
    before = st.allow
    if level >= cfg.upper:
        st.allow = False
    elif level <= cfg.lower:
        st.allow = True
    if level >= 100:
        st.boost = False
    st.dirty = st.dirty or (st.allow != before)


def source_ma(d1_mv: int) -> int:
    """Charger's offer from the halved CC reading on D1."""
    if d1_mv > 620:
        return HIGH_MA
    if d1_mv > 330:
        return MID_MA
    return BASIC_MA


def phone_present(d2_mv: int, out_ma: int) -> bool:
    """Phone detected from the CC reading on D2. Too low means a short, too high means open."""
    open_mv = 2300 if out_ma == HIGH_MA else 1600
    return 150 < d2_mv < open_mv


def in_window(minute: int, start: int, end: int) -> bool:
    """True if `minute` (after midnight) is inside start..end, which may wrap past midnight.
    An unknown time (-1) counts as inside, so a socket that has not learnt the time still charges."""
    if minute < 0 or start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def step(st: State, cfg: Config, now_ms: int, present: bool, src_ma: int,
         minute: int = -1, temp_c: float = 0.0) -> None:
    """One pass of the control loop. Sets st.vbus, st.out_ma and st.reason."""
    if st.vbus and st.last_ms >= 0:
        st.session_ms += now_ms - st.last_ms
    st.last_ms = now_ms

    if present:
        st.absent_since = -1
        if st.present_since < 0:
            st.present_since = now_ms
    else:
        if st.absent_since < 0:
            st.absent_since = now_ms
        if now_ms - st.absent_since >= DETACH_DEBOUNCE_MS:
            st.present_since = -1
            st.boost = False
            st.session_ms = 0

    if temp_c >= HOT_C:
        st.hot = True
    elif temp_c <= COOL_C:
        st.hot = False

    if (cfg.mode == "auto" and not st.allow and cfg.safety_h > 0 and st.report_ms >= 0
            and now_ms - st.report_ms > cfg.safety_h * 3600 * 1000):
        st.allow, st.dirty = True, True

    settled = now_ms >= BOOT_WAIT_MS
    debounced = st.present_since >= 0 and now_ms - st.present_since >= ATTACH_DEBOUNCE_MS
    timer_done = cfg.timer_min > 0 and st.session_ms >= cfg.timer_min * 60 * 1000
    if not settled:
        reason = "boot"
    elif not debounced:
        reason = "no_phone"
    elif st.hot:
        reason = "hot"
    elif cfg.mode == "off":
        reason = "off"
    elif st.boost:
        reason = ""
    elif cfg.sched_on and not in_window(minute, cfg.sched_start, cfg.sched_end):
        reason = "schedule"
    elif timer_done:
        reason = "timer"
    elif cfg.mode == "auto" and not st.allow:
        reason = "limit"
    else:
        reason = ""

    st.reason = reason
    st.vbus = reason == ""
    st.out_ma = min(src_ma, cfg.max_ma) if st.vbus else BASIC_MA
