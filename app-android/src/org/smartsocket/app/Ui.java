package org.smartsocket.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

/** Builds the screens in code, in one consistent look that follows the phone's light or dark setting. */
final class Ui {
    final Context c;
    final boolean dark;
    final int bg, card, ink, dim, line, accent, go, stop, onAccent;

    Ui(Context c) {
        this.c = c;
        dark = (c.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        bg = dark ? 0xFF111614 : 0xFFF2F5F3;
        card = dark ? 0xFF1B2320 : 0xFFFFFFFF;
        ink = dark ? 0xFFE6EEEA : 0xFF15211C;
        dim = dark ? 0xFF9AAFA6 : 0xFF5A6A63;
        line = dark ? 0xFF2D3A35 : 0xFFDCE4E0;
        accent = dark ? 0xFF4FB38C : 0xFF1F6F53;
        go = dark ? 0xFF4FB38C : 0xFF1F8A55;
        stop = dark ? 0xFFF0A13A : 0xFFC2780E;
        onAccent = dark ? 0xFF0D1512 : 0xFFFFFFFF;
    }

    int dp(float v) { return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, c.getResources().getDisplayMetrics())); }

    /** A scrolling page with a title; returns the column to fill. */
    LinearLayout page(Activity a, String title) {
        a.getWindow().setStatusBarColor(bg);
        a.getWindow().setNavigationBarColor(bg);
        if (!dark) a.getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        ScrollView sv = new ScrollView(c);
        sv.setBackgroundColor(bg);
        sv.setFillViewport(true);
        LinearLayout col = new LinearLayout(c);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(16), dp(20), dp(16), dp(28));
        sv.addView(col);
        // From Android 15 the page is drawn under the status bar and the navigation bar: it keeps its
        // content clear of them (and of a camera cutout) by padding itself by the room they take.
        final int left = col.getPaddingLeft(), top = col.getPaddingTop(), right = col.getPaddingRight(), bottom = col.getPaddingBottom();
        sv.setOnApplyWindowInsetsListener((v, insets) -> {
            int l, t, r, b;
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars() | android.view.WindowInsets.Type.displayCutout());
                l = bars.left; t = bars.top; r = bars.right; b = bars.bottom;
            } else {
                l = insets.getSystemWindowInsetLeft(); t = insets.getSystemWindowInsetTop(); r = insets.getSystemWindowInsetRight(); b = insets.getSystemWindowInsetBottom();
            }
            col.setPadding(left + l, top + t, right + r, bottom + b);
            return insets;
        });
        TextView h = text(title, 26, ink, true);
        h.setPadding(0, dp(4), 0, dp(14));
        col.addView(h);
        a.setContentView(sv);
        return col;
    }

    TextView text(String s, float sp, int color, boolean bold) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        return t;
    }

    TextView label(String s) {
        TextView t = text(s.toUpperCase(), 12, accent, true);
        t.setLetterSpacing(0.08f);
        t.setPadding(0, dp(4), 0, dp(8));
        return t;
    }

    LinearLayout card(LinearLayout parent) {
        LinearLayout box = new LinearLayout(c);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(14), dp(16), dp(16));
        GradientDrawable g = new GradientDrawable();
        g.setColor(card);
        g.setCornerRadius(dp(14));
        g.setStroke(dp(1), line);
        box.setBackground(g);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(12);
        parent.addView(box, lp);
        return box;
    }

    LinearLayout row() {
        LinearLayout r = new LinearLayout(c);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        return r;
    }

    Button button(String s, boolean primary, View.OnClickListener l) {
        Button b = new Button(c);
        b.setText(s);
        b.setAllCaps(false);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        style(b, primary);
        b.setOnClickListener(l);
        b.setMinHeight(dp(46));
        return b;
    }

    void style(Button b, boolean primary) {
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(10));
        g.setColor(primary ? accent : card);
        g.setStroke(dp(1), primary ? accent : line);
        b.setBackground(new RippleDrawable(ColorStateList.valueOf(dim & 0x33FFFFFF), g, null));
        b.setTextColor(primary ? onAccent : ink);
        b.setStateListAnimator(null);
    }

    LinearLayout.LayoutParams wide() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(8);
        return lp;
    }

    LinearLayout.LayoutParams weight() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        lp.setMarginEnd(dp(6));
        return lp;
    }

    EditText field(String hint, boolean secret, boolean number) {
        EditText e = new EditText(c);
        e.setHint(hint);
        e.setTextColor(ink);
        e.setHintTextColor(dim);
        e.setSingleLine(true);
        int type = number ? InputType.TYPE_CLASS_NUMBER : InputType.TYPE_CLASS_TEXT;
        if (secret) type |= number ? InputType.TYPE_NUMBER_VARIATION_PASSWORD : InputType.TYPE_TEXT_VARIATION_PASSWORD;
        e.setInputType(type);
        return e;
    }

    AlertDialog.Builder dialog(String title) {
        return new AlertDialog.Builder(c, dark ? android.R.style.Theme_DeviceDefault_Dialog_Alert : android.R.style.Theme_DeviceDefault_Light_Dialog_Alert).setTitle(title);
    }

    void toast(String s) { Toast.makeText(c, s, Toast.LENGTH_SHORT).show(); }

    View dot(int color) {
        View v = new View(c);
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.OVAL);
        g.setColor(color);
        v.setBackground(g);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(14), dp(14));
        lp.setMarginEnd(dp(12));
        v.setLayoutParams(lp);
        return v;
    }

    void recolor(View dot, int color) { ((GradientDrawable) dot.getBackground()).setColor(color); }

    /** Plain-language line for a socket's status. */
    static String headline(org.json.JSONObject s) {
        if (s == null) return "Not answering";
        if (s.optBoolean("charging")) return "Charging";
        switch (s.optString("reason")) {
            case "boot": return "Starting";
            case "no_phone": return "No device plugged in";
            case "hot": return "Paused: too warm";
            case "off": return "Never charge is on";
            case "schedule": return "Paused: outside charging hours";
            case "timer": return "Paused: timer finished";
            case "limit": return "Paused at the battery limit";
            default: return "Paused";
        }
    }

    static String detail(org.json.JSONObject s) {
        if (s == null) return "";
        String r = s.optString("reason");
        if (s.optBoolean("charging")) {
            if (s.optBoolean("boost")) return "Going to 100 % this once";
            if ("on".equals(s.optString("mode"))) return "Always charge is on";
            if (s.optInt("timer_min") > 0 && s.optLong("timer_left_s") >= 0)
                return "Timer: " + (s.optLong("timer_left_s") / 60) + " min left";
            return "Until " + s.optInt("upper") + " %";
        }
        switch (r) {
            case "hot": return "Inside the case: " + s.optDouble("temp_c") + " °C. It restarts once cooler.";
            case "schedule": return "Charges between " + s.optString("sched_start") + " and " + s.optString("sched_end");
            case "limit": return "Starts again at " + s.optInt("lower") + " %";
            case "timer": return "Unplug and plug in again to restart the timer";
            default: return "";
        }
    }

    static int stateColor(Ui ui, org.json.JSONObject s) {
        if (s == null) return ui.line;
        if (s.optBoolean("charging")) return ui.go;
        if ("no_phone".equals(s.optString("reason")) || "boot".equals(s.optString("reason"))) return ui.dim;
        return ui.stop;
    }

    static int parseColor(String hex) { return Color.parseColor(hex); }
}
