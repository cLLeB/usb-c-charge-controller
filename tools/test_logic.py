"""Scenario tests for logic.py. Run: python test_logic.py"""

from logic import (BASIC_MA, HIGH_MA, MID_MA, Config, State, clamp_config, in_window,
                   phone_present, report_battery, source_ma, step)

results = []


def check(name, cond):
    results.append((name, bool(cond)))


def run(st, cfg, t0, t1, present=True, src=HIGH_MA, dt=50):
    for t in range(t0, t1 + 1, dt):
        step(st, cfg, t, present, src)


# 1. Nothing is powered during the first 5 s, whatever the state.
st, cfg = State(), Config()
run(st, cfg, 0, 4950)
check("no output during boot wait", not st.vbus)
run(st, cfg, 5000, 5200)
check("output on after boot wait when allowed", st.vbus)

# 2. Phone must be seen for 150 ms before power is applied.
st, cfg = State(), Config()
run(st, cfg, 0, 6000, present=False)
check("no output without a phone", not st.vbus)
step(st, cfg, 6050, True, HIGH_MA)
step(st, cfg, 6150, True, HIGH_MA)
check("no output before 150 ms debounce", not st.vbus)
step(st, cfg, 6200, True, HIGH_MA)
check("output on after debounce", st.vbus)
step(st, cfg, 6250, False, HIGH_MA)
check("one missing reading does not cut the power", st.vbus)
step(st, cfg, 6300, True, HIGH_MA)
step(st, cfg, 6350, False, HIGH_MA)
check("a second glitch after a good reading does not cut it either", st.vbus)
step(st, cfg, 6400, False, HIGH_MA)
check("still on 50 ms into a real unplug", st.vbus)
step(st, cfg, 6450, False, HIGH_MA)
check("output off 100 ms after the phone is unplugged", not st.vbus)
step(st, cfg, 6500, True, HIGH_MA)
step(st, cfg, 6600, True, HIGH_MA)
check("plugging back in waits for the debounce again", not st.vbus)
step(st, cfg, 6650, True, HIGH_MA)
check("and then powers", st.vbus)

# 3. Full cycle with 80 / 40 limits.
st, cfg = State(), Config()
run(st, cfg, 0, 6000)
report_battery(st, cfg, 60, 6000); run(st, cfg, 6050, 6100)
check("60 % keeps charging", st.vbus)
report_battery(st, cfg, 80, 7000); run(st, cfg, 7050, 7100)
check("80 % stops", not st.vbus and st.dirty)
report_battery(st, cfg, 79, 8000); run(st, cfg, 8050, 8100)
check("79 % stays stopped (no chatter)", not st.vbus)
report_battery(st, cfg, 41, 9000); run(st, cfg, 9050, 9100)
check("41 % stays stopped", not st.vbus)
report_battery(st, cfg, 40, 10000); run(st, cfg, 10050, 10100)
check("40 % starts again", st.vbus)
report_battery(st, cfg, 79, 11000); run(st, cfg, 11050, 11100)
check("79 % on the way up keeps charging", st.vbus)

# 4. Current offered: capped by the setting and by the charger.
st, cfg = State(), Config(max_ma=HIGH_MA)
run(st, cfg, 0, 6000, src=MID_MA)
check("charger offering 1.5 A limits a 3 A setting", st.out_ma == MID_MA)
cfg.max_ma = BASIC_MA
run(st, cfg, 6050, 6100, src=HIGH_MA)
check("basic setting limits a 3 A charger", st.out_ma == BASIC_MA)
st, cfg = State(allow=False), Config(max_ma=HIGH_MA)
run(st, cfg, 0, 6000)
check("basic signal while stopped", st.out_ma == BASIC_MA and not st.vbus)

# 5. Modes.
st, cfg = State(allow=False), Config(mode="on")
run(st, cfg, 0, 6000)
check("mode on ignores the limit", st.vbus)
st, cfg = State(allow=True), Config(mode="off")
run(st, cfg, 0, 6000)
check("mode off never powers", not st.vbus)

# 6. Charge to 100 % once.
st, cfg = State(allow=False), Config()
run(st, cfg, 0, 6000)
st.boost = True; run(st, cfg, 6050, 6300)
check("boost charges past the limit", st.vbus)
report_battery(st, cfg, 100, 7000); run(st, cfg, 7050, 7100)
check("boost ends at 100 %", not st.vbus and not st.boost)
st.boost = True; run(st, cfg, 7150, 7400)
run(st, cfg, 7450, 7600, present=False)
check("boost ends when the phone is unplugged", not st.boost)

# 7. Restart while stopped: the saved latch is restored, output stays off.
st, cfg = State(allow=False), Config()
run(st, cfg, 0, 10000)
check("restart keeps a stopped phone stopped", not st.vbus)

# 8. Safety resume.
st, cfg = State(), Config(safety_h=12)
run(st, cfg, 0, 6000)
report_battery(st, cfg, 85, 6000); run(st, cfg, 6050, 6100)
check("stopped at 85 %", not st.vbus)
t = 6000 + 12 * 3600 * 1000
step(st, cfg, t - 1000, True, HIGH_MA)
check("still stopped just before 12 h", not st.vbus)
step(st, cfg, t + 1000, True, HIGH_MA)
check("resumes after 12 h without a report", st.vbus)
st, cfg = State(), Config(safety_h=0)
run(st, cfg, 0, 6000)
report_battery(st, cfg, 85, 6000)
step(st, cfg, 6000 + 100 * 3600 * 1000, True, HIGH_MA)
check("safety resume off means stopped forever", not st.vbus)

# 9. Settings are kept sane.
cfg = Config(upper=30, lower=90, mode="x", max_ma=7, safety_h=-3)
clamp_config(cfg)
check("lower is forced at least 5 below upper", cfg.lower <= cfg.upper - 5)
check("bad mode and current fall back", cfg.mode == "auto" and cfg.max_ma == BASIC_MA and cfg.safety_h == 0)

# 10. Analog thresholds against the USB-C voltage windows (in mV).
check("charger basic (0.25-0.61 V, halved)", source_ma(125) == BASIC_MA and source_ma(305) == BASIC_MA)
check("charger 1.5 A (0.70-1.16 V, halved)", source_ma(350) == MID_MA and source_ma(580) == MID_MA)
check("charger 3 A (1.31-2.04 V, halved)", source_ma(655) == HIGH_MA and source_ma(1020) == HIGH_MA)
check("phone seen with 33 k (0.44 V)", phone_present(440, BASIC_MA))
check("phone seen with 12 k (0.98 V)", phone_present(980, MID_MA))
check("phone seen with 4.7 k (1.72 V)", phone_present(1720, HIGH_MA))
check("open CC reads as no phone", not phone_present(3000, BASIC_MA) and not phone_present(2900, HIGH_MA))
check("shorted CC reads as no phone", not phone_present(40, BASIC_MA))

# 11. Charge timer (version 1.5): for devices that cannot report their level.
st, cfg = State(), Config(timer_min=2)
run(st, cfg, 0, 6000)
check("timer: charging at first", st.vbus and st.reason == "")
run(st, cfg, 6050, 6000 + 119 * 1000, dt=1000)
check("timer: still charging just before 2 min", st.vbus)
run(st, cfg, 6000 + 120 * 1000, 6000 + 125 * 1000, dt=1000)
check("timer: stops after 2 min of charging", not st.vbus and st.reason == "timer")
run(st, cfg, 140000, 140500, present=False)
run(st, cfg, 140550, 141000)
check("timer: starts again when the device is plugged back in", st.vbus)
st, cfg = State(), Config(timer_min=1)
run(st, cfg, 0, 6000)
st.boost = True
run(st, cfg, 6050, 6000 + 90 * 1000, dt=1000)
check("timer: '100 % once' overrides the timer", st.vbus)
st, cfg = State(), Config(timer_min=1, mode="on")
run(st, cfg, 0, 6000 + 70 * 1000, dt=1000)
check("timer: also applies in 'always charge'", not st.vbus and st.reason == "timer")

# 12. Charging hours (version 1.5), for example off-peak 22:00 to 06:00.
check("window wraps midnight", in_window(23 * 60, 22 * 60, 6 * 60) and in_window(60, 22 * 60, 6 * 60))
check("window outside", not in_window(12 * 60, 22 * 60, 6 * 60))
check("window same day", in_window(10 * 60, 9 * 60, 17 * 60) and not in_window(17 * 60, 9 * 60, 17 * 60))
check("unknown time counts as inside", in_window(-1, 22 * 60, 6 * 60))
st, cfg = State(), Config(sched_on=True)
run(st, cfg, 0, 6000)
step(st, cfg, 6050, True, HIGH_MA, minute=12 * 60)
check("schedule: no charging at noon", not st.vbus and st.reason == "schedule")
step(st, cfg, 6100, True, HIGH_MA, minute=23 * 60)
check("schedule: charging at 23:00", st.vbus)
step(st, cfg, 6150, True, HIGH_MA, minute=-1)
check("schedule: charging when the time is not known yet", st.vbus)
st.boost = True
step(st, cfg, 6200, True, HIGH_MA, minute=12 * 60)
check("schedule: '100 % once' overrides the hours", st.vbus)
st, cfg = State(allow=False), Config(sched_on=True)
run(st, cfg, 0, 6000)
step(st, cfg, 6050, True, HIGH_MA, minute=23 * 60)
check("schedule: inside the hours the battery limit still applies", not st.vbus and st.reason == "limit")

# 13. Heat cut-out (version 1.5) from the controller's temperature sensor.
st, cfg = State(), Config(mode="on")
run(st, cfg, 0, 6000)
step(st, cfg, 6050, True, HIGH_MA, temp_c=76)
check("heat: stops at 76 C, even in 'always charge'", not st.vbus and st.reason == "hot")
st.boost = True
step(st, cfg, 6100, True, HIGH_MA, temp_c=70)
check("heat: stays stopped at 70 C (hysteresis), even with '100 % once'", not st.vbus)
step(st, cfg, 6150, True, HIGH_MA, temp_c=64)
check("heat: charges again below 65 C", st.vbus)

# 14. Reasons reported to the app.
st, cfg = State(), Config()
step(st, cfg, 1000, True, HIGH_MA)
check("reason: boot wait", st.reason == "boot")
run(st, cfg, 5000, 6000, present=False)
check("reason: no phone", st.reason == "no_phone")
st, cfg = State(), Config(mode="off")
run(st, cfg, 0, 6000)
check("reason: never charge", st.reason == "off")

cfg = Config(timer_min=99999, sched_start=-5, sched_end=5000)
clamp_config(cfg)
check("timer and hours are kept sane", cfg.timer_min == 1440 and cfg.sched_start == 0 and cfg.sched_end == 1439)

width = max(len(n) for n, _ in results)
for name, ok in results:
    print(f"{name:<{width}}  {'PASS' if ok else 'FAIL'}")
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)} of {len(results)} passed")
raise SystemExit(1 if failed else 0)
