#!/usr/bin/env python3
"""Task panel — translucent desktop widget (KDE Plasma / Wayland).

Visual language: dark-navy glass, amber #F8C671, thin amber border.

How it works
  * bottom composer is multi-line: the FIRST line is the task (numbered),
    every following line becomes a dotted sub-point under it.
    Enter just makes a new line — keep typing sub-points on the spot.
    Ctrl+Enter (or the hint under the composer) saves it.
  * click a task text once to edit it (loads all its lines back into the composer)
  * "＋ ticket" — the configured LLM writes an English title + context, creates
    the issue in your Jira project (assigned to you) and drops a link under the task
  * "done" next to a linked ticket — moves that Jira issue to its Done status
  * "✓" — done (disappears);  trash — delete

Storage: ~/.cache/task-panel/tasks.json
"""
import datetime
import json
import locale
import os
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend as B

HOME = os.path.expanduser("~")
GEO = os.path.join(HOME, ".cache", "task-panel", "geometry.json")

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

CSS = b"""
window { background-color: rgba(15, 18, 28, 0.90); border-radius: 16px;
         border: 1px solid rgba(232, 173, 98, 0.32);
         font-family: "Onest", "Noto Sans", sans-serif; }
label { color: #e8e8f0; font-family: "Onest", "Noto Sans", sans-serif; letter-spacing: 0.4px; }
button, entry, textview, popover { font-family: "Onest", "Noto Sans", sans-serif; }

scrollbar { background: transparent; border: none; }
scrollbar.vertical { background: transparent; border: none; padding: 0; min-width: 0; }
scrollbar.vertical trough { margin: 0; min-width: 0; background: transparent; }
scrollbar.vertical slider {
    border: none; margin: 0; min-width: 3px; min-height: 24px;
    border-radius: 2px; background-color: rgba(232, 232, 240, 0.20);
    background-image: none; background-clip: padding-box; box-shadow: none; }
scrollbar.vertical slider:hover { background-color: rgba(248, 198, 113, 0.55); }

.date { font-size: 14px; font-weight: 700; letter-spacing: 0.5px; }
.sub { color: rgba(232, 232, 240, 0.55); font-size: 10px; letter-spacing: 0.3px; }
.dim { color: rgba(232, 232, 240, 0.38); }

.num { color: #F8C671; font-weight: 700; font-size: 12.5px; }
.tasktext { font-size: 12.5px; color: #e8e8f0; }
.bullet { font-size: 11.5px; color: rgba(232, 232, 240, 0.55); }
.dot { color: rgba(232, 232, 240, 0.38); font-size: 11.5px; }
.ticketrow { color: #F8C671; font-size: 11.5px; }

.hint { color: rgba(232, 232, 240, 0.38); font-size: 9.5px; }
.status { color: rgba(232, 232, 240, 0.6); font-size: 9.5px; }
.status.ok { color: #7FB6A8; }
.status.err { color: #E89B7A; }
.footer { color: rgba(232, 232, 240, 0.4); font-size: 9.5px; }
.sep { background: rgba(232, 232, 240, 0.10); min-height: 1px; }

textview, textview text { background: transparent; color: #e8e8f0; font-size: 12px; letter-spacing: 0.4px; }
textview { background: rgba(232, 232, 240, 0.05);
           border: 1px solid rgba(232, 232, 240, 0.12); border-radius: 10px; padding: 6px; }
textview:focus { border-color: rgba(248, 198, 113, 0.5); }
.busy { color: #F8C671; font-size: 11px; }

button { background: transparent; background-image: none; border: none;
         box-shadow: none; padding: 1px 5px; min-height: 0; min-width: 0; }
button:hover { background: rgba(248, 198, 113, 0.14); border-radius: 7px; }
button.icon { color: rgba(232, 232, 240, 0.5); font-size: 11px; }
button.icon:hover { color: #F8C671; }

/* delete bin - always visible, red on hover */
button.kill { color: rgba(232, 232, 240, 0.75); border-radius: 999px;
              min-width: 18px; min-height: 18px; padding: 0;
              transition: color 120ms ease-in-out; }
button.kill:hover { color: #E89B7A; background: rgba(232, 155, 122, 0.14); }
button.ticket { color: #16110a; background-image: linear-gradient(180deg, #F8C671, #E8AD62);
                border-radius: 8px; padding: 3px 9px; font-size: 9.5px; font-weight: 600; }
button.ticket label { color: #16110a; }
button.ticket:hover { background-image: linear-gradient(180deg, #ffd27f, #F0B76C); }
button.ticket:disabled { background-image: none; background: rgba(248, 198, 113, 0.18); }
button.ticket:disabled label { color: rgba(248, 198, 113, 0.7); }
button.ticketdone { color: #7FB6A8; border: 1px solid rgba(127, 182, 168, 0.5);
                    background: rgba(127, 182, 168, 0.08); border-radius: 999px;
                    padding: 1px 9px; font-size: 9.5px; font-weight: 600; }
button.ticketdone:hover { background: rgba(127, 182, 168, 0.20); border-color: rgba(127, 182, 168, 0.75); }
button.ticketdone.done { color: #0d1713; border-color: transparent; background: #7FB6A8; }
button.ticketdone.done label { color: #0d1713; }
button.ticketdone.loading { color: rgba(127, 182, 168, 0.65); border-style: dashed;
                            border-color: rgba(127, 182, 168, 0.35); }
button.primary { color: #16110a; background-image: linear-gradient(180deg, #F8C671, #E8AD62);
                 border-radius: 9px; padding: 5px 14px; font-weight: 600; font-size: 10px; }
button.primary label { color: #16110a; }
button.ghost { color: rgba(232, 232, 240, 0.7); border: 1px solid rgba(232, 232, 240, 0.16);
               border-radius: 9px; padding: 5px 12px; font-size: 10px; }
button.done { border: 1.5px solid rgba(232, 232, 240, 0.30); border-radius: 6px;
              min-width: 17px; min-height: 17px; color: transparent; padding: 0; font-size: 11px; }
button.done:hover { border-color: transparent; color: #16110a;
                    background-image: linear-gradient(180deg, #F8C671, #E8AD62); }
.link { color: #F8C671; font-size: 11.5px; }
button.link, button.link:hover { padding: 0; }
button.link label, button.link:visited label { color: #F8C671; }
button.link:hover label { text-decoration: underline; }
.ticketmeta { color: rgba(127, 182, 168, 0.85); font-size: 10px; letter-spacing: 0.3px; }

.doneytext { color: rgba(232, 232, 240, 0.45); font-size: 12.5px; }
.donebullet { color: rgba(232, 232, 240, 0.30); font-size: 11.5px; }
.section { color: rgba(232, 232, 240, 0.45); font-size: 9.5px; letter-spacing: 1px; }
button.restore { color: #7FB6A8; font-size: 13px; }
button.restore:hover { color: #7FB6A8; background: rgba(127, 182, 168, 0.14); }
button.toggle-on { background: rgba(248, 198, 113, 0.16); }
button.doneview { border: 1px solid rgba(232, 173, 98, 0.42); border-radius: 8px;
                  padding: 2px 10px; color: #F8C671; font-size: 10px; }
button.doneview:hover { background: rgba(248, 198, 113, 0.12); border-color: rgba(248, 198, 113, 0.6); }
button.doneview.toggle-on { background: rgba(248, 198, 113, 0.18); }

.pill { font-size: 10px; padding: 1px 8px; border-radius: 999px;
        border: 1px solid rgba(248, 198, 113, 0.42); color: #F8C671;
        background: rgba(248, 198, 113, 0.10); }
.pill.over { border-color: rgba(232, 155, 122, 0.55); color: #E89B7A;
             background: rgba(232, 155, 122, 0.10); }
button.rem { color: rgba(232, 232, 240, 0.5); font-size: 11px; }
button.rem:hover { color: #F8C671; }
button.rem.set { color: #F8C671; }

popover { background: rgba(15, 18, 28, 0.98); border: 1px solid rgba(232, 173, 98, 0.30);
          border-radius: 12px; }
popover > contents { background: transparent; color: #e8e8f0; }
calendar { color: #e8e8f0; background: transparent; }
.day-number:selected { background-color: #F8C671; color: #16110a; border-radius: 6px; }
.day-number.today { color: #F8C671; }
.chosen { color: #F8C671; font-size: 11px; }
calendar.header, calendar.holiday { background: transparent; }

.scrim { background: rgba(6, 8, 13, 0.72); }
.remcard { background: rgba(15, 18, 28, 0.98); border: 1px solid rgba(232, 173, 98, 0.32);
           border-radius: 12px; padding: 10px; }
"""


def run_async(fn, on_done=None, on_error=None):
    def worker():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            if on_error:
                GLib.idle_add(on_error, str(e))
            return
        if on_done:
            GLib.idle_add(on_done, res)

    threading.Thread(target=worker, daemon=True).start()


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        # GtkCalendar takes the first day of week from LC_TIME; en_GB starts on
        # Monday. Set it before the calendar is created so the week starts Monday.
        try:
            locale.setlocale(locale.LC_TIME, "en_GB.UTF-8")
        except Exception:
            try:
                locale.setlocale(locale.LC_TIME, "en_GB.utf8")
            except Exception:
                pass
        self.set_title("Tasks")
        self.set_decorated(False)
        self.set_resizable(True)
        self.set_size_request(320, 260)
        self.set_default_size(*(self._load_geometry() or (400, 540)))

        self.store = B.load_tasks()
        self._busy = set()
        self._mode = "add"          # "add" | "edit"
        self._editing = None        # task dict when editing
        self._show_done = True

        outer = Gtk.Overlay()
        self.set_child(outer)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_top(12)
        root.set_margin_bottom(8)
        root.set_margin_start(14)
        root.set_margin_end(14)
        outer.set_child(root)

        # ---------- header ----------
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.append(head)
        self.date_lbl = Gtk.Label()
        self.date_lbl.add_css_class("date")
        self.date_lbl.set_halign(Gtk.Align.START)
        self.date_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        head.append(self.date_lbl)
        self.refresh_date()

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        tools.set_halign(Gtk.Align.END)
        tools.set_hexpand(True)
        head.append(tools)
        self.done_btn = Gtk.Button(label="Hide")
        self.done_btn.add_css_class("doneview")
        self.done_btn.add_css_class("toggle-on")
        self.done_btn.set_tooltip_text("Hide completed")
        self.done_btn.connect("clicked", self.on_toggle_done)
        tools.append(self.done_btn)
        self.icon_button(tools, "⟳", "Sync with Jira", self.on_sync)
        self.icon_button(tools, "✕", "Close", lambda *_: self.get_application().quit())

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self.on_move_begin)
        self.date_lbl.add_controller(drag)

        self.sub_lbl = Gtk.Label()
        self.sub_lbl.add_css_class("sub")
        self.sub_lbl.set_halign(Gtk.Align.START)
        self.sub_lbl.set_xalign(0)
        self.sub_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.sub_lbl.set_margin_top(2)
        root.append(self.sub_lbl)

        sep = Gtk.Box(); sep.add_css_class("sep")
        sep.set_margin_top(8); sep.set_margin_bottom(6)
        root.append(sep)

        # ---------- list ----------
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.list_box.set_margin_top(6)
        self.list_box.set_margin_bottom(6)
        self.list_box.set_margin_end(14)   # keep buttons clear of the scrollbar
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        try:
            sw.set_overlay_scrolling(False)   # reserve space so the bar can't cover buttons
        except Exception:
            pass
        sw.set_vexpand(True); sw.set_hexpand(True)
        sw.set_child(self.list_box)
        root.append(sw)
        self.sw = sw

        # ---------- composer ----------
        sep2 = Gtk.Box(); sep2.add_css_class("sep")
        sep2.set_margin_top(6); sep2.set_margin_bottom(8)
        root.append(sep2)

        self.hint = Gtk.Label()
        self.hint.add_css_class("hint")
        self.hint.set_halign(Gtk.Align.START); self.hint.set_xalign(0)
        self.hint.set_ellipsize(Pango.EllipsizeMode.END)
        root.append(self.hint)

        self.tv = Gtk.TextView()
        self.tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.tv.set_top_margin(4); self.tv.set_bottom_margin(4)
        self.tv.set_left_margin(4); self.tv.set_right_margin(4)
        tvscroll = Gtk.ScrolledWindow()
        tvscroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tvscroll.set_size_request(-1, 74)
        tvscroll.set_child(self.tv)
        tvscroll.set_margin_top(4)
        root.append(tvscroll)
        kc = Gtk.EventControllerKey.new()
        kc.connect("key-pressed", self.on_composer_key)
        self.tv.add_controller(kc)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        foot.set_margin_top(6)
        root.append(foot)

        self.status = Gtk.Label(label="")
        self.status.add_css_class("status")
        self.status.set_halign(Gtk.Align.START); self.status.set_xalign(0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.set_hexpand(True)
        foot.append(self.status)

        self.count_lbl = Gtk.Label(label="")
        self.count_lbl.add_css_class("footer")
        self.count_lbl.set_halign(Gtk.Align.END)
        foot.append(self.count_lbl)

        # ---------- reminder picker (in-window overlay, always interactive) ----
        self._reminder_task = None
        self.rem_scrim = Gtk.Box()
        self.rem_scrim.add_css_class("scrim")
        self.rem_scrim.set_visible(False)
        scrim_click = Gtk.GestureClick.new()
        scrim_click.connect("pressed", self._on_scrim_click)
        self.rem_scrim.add_controller(scrim_click)

        self.rem_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.rem_card.add_css_class("remcard")
        self.rem_card.set_halign(Gtk.Align.CENTER)
        self.rem_card.set_valign(Gtk.Align.CENTER)
        self.rem_card.set_margin_top(10); self.rem_card.set_margin_bottom(10)
        self.rem_card.set_margin_start(10); self.rem_card.set_margin_end(10)
        self.rem_card.set_visible(False)

        self.calendar = Gtk.Calendar()
        self.rem_card.append(self.calendar)
        self.calendar.connect("day-selected", lambda *_: self._sync_chosen())
        self.chosen_lbl = Gtk.Label()
        self.chosen_lbl.add_css_class("chosen")
        self.chosen_lbl.set_halign(Gtk.Align.START)
        self.chosen_lbl.set_xalign(0)
        self.rem_card.append(self.chosen_lbl)

        trow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.rem_card.append(trow)
        tl = Gtk.Label(label="Time"); tl.add_css_class("sub")
        trow.append(tl)
        self.time_entry = Gtk.Entry()
        self.time_entry.set_text("10:00")
        self.time_entry.set_width_chars(5)
        self.time_entry.set_max_width_chars(5)
        trow.append(self.time_entry)
        for quick in ("09:00", "12:00", "18:00"):
            qb = Gtk.Button(label=quick)
            qb.add_css_class("ghost")
            qb.connect("clicked", lambda *_ , v=quick: self.time_entry.set_text(v))
            trow.append(qb)

        brow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.rem_card.append(brow)
        self.rem_clear = Gtk.Button(label="Clear")
        self.rem_clear.add_css_class("ghost")
        self.rem_clear.connect("clicked", self.clear_reminder)
        brow.append(self.rem_clear)
        sp = Gtk.Box(); sp.set_hexpand(True); brow.append(sp)
        cancelb = Gtk.Button(label="Cancel")
        cancelb.add_css_class("ghost")
        cancelb.connect("clicked", lambda *_: self.close_reminder_pop())
        brow.append(cancelb)
        okb = Gtk.Button(label="OK")
        okb.add_css_class("primary")
        okb.connect("clicked", self.apply_reminder)
        brow.append(okb)

        self._add_resize_handles(outer)
        outer.add_overlay(self.rem_scrim)
        outer.add_overlay(self.rem_card)
        esc = Gtk.EventControllerKey.new()
        esc.connect("key-pressed", self._on_window_key)
        self.add_controller(esc)

        self.set_mode("add")
        self.populate()
        self._last_size = None
        GLib.timeout_add_seconds(1, self._watch_geometry)
        GLib.timeout_add_seconds(30, self.refresh_date)
        GLib.timeout_add_seconds(3, self._initial_sync)
        GLib.timeout_add_seconds(20, self.check_reminders)

    # --------------------------------------------------------------- helpers
    def icon_button(self, box, label, tip, cb):
        b = Gtk.Button(label=label)
        b.add_css_class("icon")
        b.set_tooltip_text(tip)
        b.connect("clicked", cb)
        box.append(b)
        return b

    def kill_button(self, task, tip="Delete"):
        b = Gtk.Button()
        img = Gtk.Image.new_from_icon_name("user-trash-symbolic")
        img.set_pixel_size(13)
        b.set_child(img)
        b.add_css_class("kill")
        b.set_tooltip_text(tip)
        b.connect("clicked", lambda *_, tk=task: self.delete_task(tk))
        return b

    def _ellipsize_link(self, lb):
        """Let a ticket link shrink instead of forcing the window wider."""
        child = lb.get_child()
        if isinstance(child, Gtk.Label):
            child.set_ellipsize(Pango.EllipsizeMode.END)
            child.set_xalign(0)
        lb.set_hexpand(False)

    def set_status(self, text, kind=""):
        self.status.set_text(text)
        for c in ("ok", "err"):
            self.status.remove_css_class(c)
        if kind:
            self.status.add_css_class(kind)

    def set_mode(self, mode):
        self._mode = mode
        if mode == "edit":
            self.hint.set_text("Enter - save · * + space - sub-point · Esc - cancel")
        else:
            self._editing = None
            self.hint.set_text("Enter - add · * + space - sub-point · Shift+Enter - new line")
            self.tv.get_buffer().set_text("")

    def refresh_date(self):
        t = datetime.date.today()
        self.date_lbl.set_markup(
            f'<span foreground="#F8C671"><b>{DAYS[t.weekday()]}</b></span>'
            f'<span foreground="#cfd0da">, {t.day} {MONTHS[t.month - 1]} {t.year}</span>'
        )
        return True

    def _initial_sync(self):
        self.sync_jira()
        GLib.timeout_add_seconds(B.config()["jira"].get("status_refresh_seconds", 600),
                                 self._periodic_sync)
        return False

    def _periodic_sync(self):
        self.sync_jira()
        return True

    # ------------------------------------------------------- geometry save
    def _load_geometry(self):
        try:
            with open(GEO) as f:
                d = json.load(f)
            w, h = int(d["width"]), int(d["height"])
            if w > 80 and h > 80:
                return (w, h)
        except Exception:
            pass
        return None

    def _watch_geometry(self):
        size = (self.get_width(), self.get_height())
        if size != self._last_size:
            self._last_size = size
            try:
                os.makedirs(os.path.dirname(GEO), exist_ok=True)
                with open(GEO, "w") as f:
                    json.dump({"width": size[0], "height": size[1]}, f)
            except Exception:
                pass
        return True

    def _focus_composer(self, to_end=False):
        """One-shot idle focus. Must return False or GLib keeps re-running it,
        which would pin the caret to the end of the text forever."""
        def cb():
            self.tv.grab_focus()
            if to_end:
                buf = self.tv.get_buffer()
                buf.place_cursor(buf.get_end_iter())
            return False
        GLib.idle_add(cb)

    def on_move_begin(self, gesture, x, y):
        try:
            surf = self.get_native().get_surface()
            res = gesture.get_widget().translate_coordinates(self, x, y)
            sx, sy = (res[1], res[2]) if len(res) == 3 else (x, y)
            surf.begin_move(gesture.get_device(), 1, sx, sy, 0)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"move: {e}", "err")

    # ------------------------------------------------------- window resize
    def _resize(self, gesture, edge, x, y):
        try:
            surf = self.get_native().get_surface()
            res = gesture.get_widget().translate_coordinates(self, x, y)
            sx, sy = (res[1], res[2]) if len(res) == 3 else (x, y)
            surf.begin_resize(edge, gesture.get_device(), 1, sx, sy, 0)
        except Exception as e:  # noqa: BLE001
            self.set_status(f"resize: {e}", "err")

    def _add_resize_handles(self, overlay):
        """Invisible grab areas on every edge/corner (undecorated window)."""
        def handle(edge, width, height, halign, valign, cursor):
            area = Gtk.Box()
            area.set_size_request(width, height)
            area.set_halign(halign)
            area.set_valign(valign)
            try:
                area.set_cursor(Gdk.Cursor.new_from_name(cursor))
            except Exception:
                pass
            g = Gtk.GestureDrag.new()
            g.connect("drag-begin", lambda gest, x, y, e=edge: self._resize(gest, e, x, y))
            area.add_controller(g)
            overlay.add_overlay(area)

        handle(Gdk.SurfaceEdge.EAST, 5, 0, Gtk.Align.END, Gtk.Align.FILL, "ew-resize")
        handle(Gdk.SurfaceEdge.WEST, 5, 0, Gtk.Align.START, Gtk.Align.FILL, "ew-resize")
        handle(Gdk.SurfaceEdge.NORTH, 0, 5, Gtk.Align.FILL, Gtk.Align.START, "ns-resize")
        handle(Gdk.SurfaceEdge.SOUTH, 0, 5, Gtk.Align.FILL, Gtk.Align.END, "ns-resize")
        handle(Gdk.SurfaceEdge.NORTH_EAST, 16, 16, Gtk.Align.END, Gtk.Align.START, "ne-resize")
        handle(Gdk.SurfaceEdge.NORTH_WEST, 16, 16, Gtk.Align.START, Gtk.Align.START, "nw-resize")
        handle(Gdk.SurfaceEdge.SOUTH_EAST, 16, 16, Gtk.Align.END, Gtk.Align.END, "se-resize")
        handle(Gdk.SurfaceEdge.SOUTH_WEST, 16, 16, Gtk.Align.START, Gtk.Align.END, "sw-resize")

    # ------------------------------------------------------- composer logic
    def composer_lines(self):
        buf = self.tv.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        return [ln.rstrip() for ln in text.split("\n")]

    def _line_bounds(self):
        buf = self.tv.get_buffer()
        it = buf.get_iter_at_mark(buf.get_insert())
        start = it.copy(); start.set_line_offset(0)
        end = it.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        return buf, start, end, it

    def _maybe_bullet(self):
        """Typing '* ' (or '- ') at the start of a line turns into a bullet."""
        buf, start, end, it = self._line_bounds()
        prefix = buf.get_text(start, it, False)
        if prefix in ("*", "-", "•"):
            buf.delete(start, it)
            buf.insert(start, "• ")
            return True
        return False

    def _on_enter(self):
        buf, start, end, it = self._line_bounds()
        stripped = buf.get_text(start, end, False).strip()
        if stripped.startswith("•"):
            if stripped == "•":          # empty bullet ends the list
                buf.delete(start, end)
                if any(l.strip() for l in self.composer_lines()):
                    self.commit_composer()
                return True
            buf.insert_at_cursor("\n• ")  # keep the bullet list going
            return True
        self.commit_composer()
        return True

    def on_composer_key(self, ctrl, keyval, keycode, state):
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Escape and self._mode == "edit":
            self.cancel_edit()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if shift:
                return False
            return self._on_enter()
        if keyval == Gdk.KEY_space and not shift:
            return self._maybe_bullet()
        return False

    def _strip_marker(self, s):
        t = s.strip()
        for m in ("•", "*", "-"):
            if t.startswith(m):
                t = t[len(m):].strip()
                break
        return t

    def commit_composer(self):
        raw = [ln for ln in self.composer_lines() if ln.strip()]
        if not raw:
            return
        head = self._strip_marker(raw[0])
        rest = raw[1:]
        if not head and rest:
            head = self._strip_marker(rest[0])
            rest = rest[1:]
        if not head:
            return
        bullets = [b for b in (self._strip_marker(ln) for ln in rest) if b]

        if self._mode == "edit" and self._editing is not None:
            task = self._editing
            task["text"] = head
            tickets = [c for c in task.get("children", []) if c.get("kind") == "ticket"]
            task["children"] = [{"id": B.new_id(), "kind": "bullet", "text": b} for b in bullets] + tickets
        else:
            self.store["tasks"].append({
                "id": B.new_id(), "text": head, "done": False,
                "created_at": int(GLib.get_real_time() / 1e6),
                "children": [{"id": B.new_id(), "kind": "bullet", "text": b} for b in bullets],
            })
        B.save_tasks(self.store)
        self.set_status("saved", "ok")
        self.set_mode("add")
        self.populate()
        self._focus_composer()

    def start_edit(self, task):
        self._editing = task
        self.set_mode("edit")
        lines = [task.get("text", "")]
        lines += ["• " + c.get("text", "") for c in task.get("children", []) if c.get("kind") == "bullet"]
        self.tv.get_buffer().set_text("\n".join(lines))
        self._focus_composer(to_end=True)

    def cancel_edit(self):
        self.set_status("")
        self.set_mode("add")

    # ------------------------------------------------------- task actions
    def mark_done(self, task):
        task["done"] = True
        B.save_tasks(self.store)
        self.set_status("✓ done", "ok")
        if self._editing is task:
            self.set_mode("add")
        self.populate()

    def delete_task(self, task):
        self.store["tasks"] = [t for t in self.store["tasks"] if t is not task]
        B.save_tasks(self.store)
        self.set_status("deleted")
        if self._editing is task:
            self.set_mode("add")
        self.populate()

    # ------------------------------------------------------- reminders
    def reminder_pill(self, task):
        r = task.get("remind")
        if not r:
            return None
        dt = datetime.datetime.fromtimestamp(r)
        today = datetime.date.today()
        if dt.date() == today:
            when = "today"
        elif dt.date() == today + datetime.timedelta(days=1):
            when = "tomorrow"
        else:
            when = dt.strftime("%d.%m")
        lbl = Gtk.Label(label=f"⏰ {when} · {dt.strftime('%H:%M')}")
        lbl.add_css_class("pill")
        if r < time.time():
            lbl.add_css_class("over")
        lbl.set_halign(Gtk.Align.START)
        return lbl

    def close_reminder_pop(self):
        self.rem_scrim.set_visible(False)
        self.rem_card.set_visible(False)

    def _sync_chosen(self):
        d = self.calendar.get_date()
        self.chosen_lbl.set_text("Chosen: " + d.format("%d.%m.%Y"))

    def _on_scrim_click(self, gesture, n_press, x, y):
        # The card is a separate overlay child on top of the scrim, so any
        # press that reaches the scrim is by definition outside the card.
        self.close_reminder_pop()

    def _on_window_key(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape and self.rem_scrim.get_visible():
            self.close_reminder_pop()
            return True
        return False

    def open_reminder(self, button, task):
        self._reminder_task = task
        r = task.get("remind")
        dt = (datetime.datetime.fromtimestamp(r) if r
              else datetime.datetime.now().replace(minute=0, second=0, microsecond=0))
        day = GLib.DateTime.new_local(dt.year, dt.month, dt.day, 0, 0, 0.0)
        try:
            self.calendar.set_date(day)
        except Exception:
            self.calendar.select_day(day)
        self.time_entry.set_text(dt.strftime("%H:%M"))
        self.rem_clear.set_visible(bool(r))
        self.rem_scrim.set_visible(True)
        self.rem_card.set_visible(True)
        self._sync_chosen()

    def _parse_time(self, text):
        try:
            hh, mm = text.strip().split(":")
            hh, mm = int(hh), int(mm)
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return hh, mm
        except Exception:
            pass
        return 10, 0

    def apply_reminder(self, *_):
        task = self._reminder_task
        if not task:
            return
        d = self.calendar.get_date()
        hh, mm = self._parse_time(self.time_entry.get_text())
        when = datetime.datetime(d.get_year(), d.get_month(), d.get_day_of_month(), hh, mm)
        task["remind"] = int(when.timestamp())
        task["remind_fired"] = False
        B.save_tasks(self.store)
        self.close_reminder_pop()
        self.set_status("reminder set", "ok")
        self.populate()

    def clear_reminder(self, *_):
        task = self._reminder_task
        if not task:
            return
        task.pop("remind", None)
        task.pop("remind_fired", None)
        B.save_tasks(self.store)
        self.close_reminder_pop()
        self.set_status("reminder cleared")
        self.populate()

    def notify_reminder(self, task):
        title = "⏰ " + task.get("text", "")
        body = datetime.datetime.fromtimestamp(task["remind"]).strftime("reminder · %H:%M")
        try:
            n = Gio.Notification.new(title)
            n.set_body(body)
            app = self.get_application()
            if app:
                app.send_notification(task.get("id"), n)
                return
        except Exception:
            pass
        try:
            subprocess.Popen(["notify-send", title, body])
        except Exception:
            pass

    def check_reminders(self):
        now = time.time()
        changed = False
        for t in self.store["tasks"]:
            if t.get("done"):
                continue
            r = t.get("remind")
            if r and r <= now and not t.get("remind_fired"):
                self.notify_reminder(t)
                t["remind_fired"] = True
                changed = True
        if changed:
            B.save_tasks(self.store)
            self.set_status("reminder fired", "ok")
            self.populate()
        return True

    def create_ticket(self, task):
        if task["id"] in self._busy:
            return
        self._busy.add(task["id"])
        self.set_status("preparing the ticket…")
        self.populate()

        def work():
            title, desc = B.generate_ticket(task["text"])
            issue = B.jira_create_issue(title, desc)
            key = issue.get("key")
            url = B.config()["jira"]["base_url"].rstrip("/") + "/browse/" + key
            return key, url, title, (B.jira_status(key) or "To Do")

        def done(res):
            key, url, title, status = res
            task.setdefault("children", []).append({
                "id": B.new_id(), "kind": "ticket", "key": key, "url": url,
                "text": f"{key} · {title}", "status": status,
            })
            self._busy.discard(task["id"])
            B.save_tasks(self.store)
            self.set_status(f"created {key}", "ok")
            self.populate()

        def fail(err):
            self._busy.discard(task["id"])
            self.set_status("ticket error: " + err, "err")
            self.populate()

        run_async(work, done, fail)

    # ------------------------------------------------------------- render
    def _restore_scroll(self, target):
        """Keep the list where the user left it after a rebuild.

        Removing rows collapses the scrollable height, which makes the
        adjustment jump to 0; re-apply the old offset once the new layout
        has grown enough to accept it (bounded, so a shorter list can't spin).
        """
        adj = self.sw.get_vadjustment()
        tries = {"n": 0}

        def apply():
            hi = max(adj.get_lower(), adj.get_upper() - adj.get_page_size())
            if hi + 0.5 >= target or tries["n"] >= 6:
                adj.set_value(min(target, hi))
                return False
            adj.set_value(hi)
            tries["n"] += 1
            return True

        GLib.timeout_add(16, apply)

    def populate(self):
        target = self.sw.get_vadjustment().get_value()
        if self._reminder_task is not None and self._reminder_task not in self.store["tasks"]:
            self.close_reminder_pop()
        child = self.list_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt

        active = [t for t in self.store["tasks"] if not t.get("done")]
        done = [t for t in self.store["tasks"] if t.get("done")]
        n_tickets = sum(1 for t in active for c in t.get("children", []) if c.get("kind") == "ticket")
        n_sub = sum(1 for t in active for c in t.get("children", []) if c.get("kind") == "bullet")
        self.sub_lbl.set_text(
            f"● {len(active)} tasks  ·  {n_sub} sub-points  ·  {n_tickets} tickets  ·  ✓ {len(done)}"
        )

        if not active and not (self._show_done and done):
            empty = Gtk.Label(label="Your tasks will appear here")
            empty.add_css_class("dim"); empty.set_margin_top(18)
            self.list_box.append(empty)

        for i, task in enumerate(active, 1):
            self.list_box.append(self.render_task(task, i))

        if self._show_done:
            sep = Gtk.Box(); sep.add_css_class("sep")
            sep.set_margin_top(8); sep.set_margin_bottom(2)
            self.list_box.append(sep)
            head = Gtk.Label(label=f"COMPLETED ({len(done)})")
            head.add_css_class("section")
            head.set_halign(Gtk.Align.START); head.set_xalign(0)
            self.list_box.append(head)
            if not done:
                lbl = Gtk.Label(label="no completed tasks")
                lbl.add_css_class("dim")
                lbl.set_halign(Gtk.Align.START); lbl.set_xalign(0)
                self.list_box.append(lbl)
            for task in done:
                self.list_box.append(self.render_done_task(task))

        self._restore_scroll(target)

    def render_done_task(self, task):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_valign(Gtk.Align.START)
        row.set_margin_start(30)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_hexpand(True)
        content.set_margin_end(6)
        row.append(content)

        t = Gtk.Label()
        t.add_css_class("doneytext")
        t.set_markup(f"<s>{GLib.markup_escape_text(task['text'])}</s>")
        t.set_wrap(True); t.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        t.set_xalign(0); t.set_halign(Gtk.Align.FILL)
        content.append(t)
        for c in task.get("children", []):
            if c.get("kind") == "bullet":
                b = Gtk.Label(label="•  " + c.get("text", ""))
                b.add_css_class("donebullet")
                b.set_wrap(True); b.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                b.set_xalign(0); b.set_halign(Gtk.Align.FILL)
                content.append(b)
            elif c.get("kind") == "ticket":
                row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                arrow = Gtk.Label(label="↗"); arrow.add_css_class("ticketrow")
                row2.append(arrow)
                lb = Gtk.LinkButton.new_with_label(c.get("url", ""), c.get("text", c.get("key", "")))
                lb.add_css_class("link"); lb.set_halign(Gtk.Align.START)
                self._ellipsize_link(lb)
                row2.append(lb)
                content.append(row2)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        actions.set_valign(Gtk.Align.START)
        row.append(actions)

        rb = Gtk.Button(label="↺")
        rb.add_css_class("restore")
        rb.set_tooltip_text("Restore to active")
        rb.connect("clicked", lambda *_, tk=task: self.unmark_done(tk))
        actions.append(rb)

        actions.append(self.kill_button(task, "Delete permanently"))
        return row

    def on_toggle_done(self, *_):
        self._show_done = not self._show_done
        if self._show_done:
            self.done_btn.set_label("Hide")
            self.done_btn.add_css_class("toggle-on")
            self.done_btn.set_tooltip_text("Hide completed")
        else:
            self.done_btn.set_label("Completed")
            self.done_btn.remove_css_class("toggle-on")
            self.done_btn.set_tooltip_text("Show completed")
        self.populate()

    def unmark_done(self, task):
        task["done"] = False
        B.save_tasks(self.store)
        self.set_status("restored to active", "ok")
        self.populate()

    def render_task(self, task, num):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_valign(Gtk.Align.START)
        row.set_margin_top(2)
        row.set_margin_bottom(2)

        num_lbl = Gtk.Label(label=str(num))
        num_lbl.add_css_class("num")
        num_lbl.set_xalign(1.0)
        num_lbl.set_halign(Gtk.Align.START)
        num_lbl.set_size_request(22, -1)
        num_lbl.set_valign(Gtk.Align.START)
        row.append(num_lbl)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_hexpand(True)
        content.set_margin_end(6)   # small gap before the action buttons
        row.append(content)

        # first line = task text (click to edit)
        t = Gtk.Label(label=task["text"])
        t.add_css_class("tasktext")
        t.set_wrap(True); t.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        t.set_xalign(0); t.set_halign(Gtk.Align.FILL)
        click = Gtk.GestureClick.new()
        click.connect("pressed", lambda g, n, x, y, tk=task: self.on_text_click(tk))
        t.add_controller(click)
        content.append(t)

        pill = self.reminder_pill(task)
        if pill is not None:
            content.append(pill)

        # children
        for c in task.get("children", []):
            content.append(self.render_child(c, task))

        # actions on the right
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        actions.set_valign(Gtk.Align.START)
        row.append(actions)

        if task["id"] in self._busy:
            b = Gtk.Label(label="⏳"); b.add_css_class("busy")
            actions.append(b)
        else:
            tb = Gtk.Button(label="＋ ticket")
            tb.add_css_class("ticket")
            tb.set_tooltip_text("Draft an English ticket and create it in Jira")
            tb.connect("clicked", lambda *_, tk=task: self.create_ticket(tk))
            actions.append(tb)

        rb = Gtk.Button(label="⏰")
        rb.add_css_class("rem")
        if task.get("remind"):
            rb.add_css_class("set")
        rb.set_tooltip_text("Reminder for any day")
        rb.connect("clicked", lambda b, tk=task: self.open_reminder(b, tk))
        actions.append(rb)

        db = Gtk.Button(label="✓")
        db.add_css_class("done")
        db.set_tooltip_text("Mark as done")
        db.connect("clicked", lambda *_, tk=task: self.mark_done(tk))
        actions.append(db)

        actions.append(self.kill_button(task))
        return row

    def render_child(self, c, task):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if c.get("kind") == "ticket":
            arrow = Gtk.Label(label="↗"); arrow.add_css_class("ticketrow")
            arrow.set_valign(Gtk.Align.START)
            row.append(arrow)
            lb = Gtk.LinkButton.new_with_label(c.get("url", ""), c.get("text", c.get("key", "")))
            lb.add_css_class("link")
            lb.set_halign(Gtk.Align.START)
            lb.set_valign(Gtk.Align.START)
            lb.set_tooltip_text("Open ticket in Jira")
            self._ellipsize_link(lb)
            row.append(lb)
            if c.get("status"):
                sl = Gtk.Label(label=c["status"]); sl.add_css_class("ticketmeta")
                sl.set_valign(Gtk.Align.START)
                row.append(sl)
            row.append(self.ticket_done_button(task, c))
        else:
            dot = Gtk.Label(label="•"); dot.add_css_class("dot")
            dot.set_valign(Gtk.Align.START)
            dot.set_size_request(10, -1)
            dot.set_xalign(1.0)
            row.append(dot)
            lbl = Gtk.Label(label=c.get("text", ""))
            lbl.add_css_class("bullet")
            lbl.set_wrap(True); lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_xalign(0); lbl.set_halign(Gtk.Align.FILL)
            row.append(lbl)
        return row

    def on_text_click(self, task):
        self.start_edit(task)

    # --------------------------------------------------- ticket done action
    def _ticket_done(self, child):
        return "done" in (child.get("status") or "").lower()

    def _all_tickets_done(self, task):
        tickets = [c for c in task.get("children", []) if c.get("kind") == "ticket"]
        return bool(tickets) and all(self._ticket_done(c) for c in tickets)

    def ticket_done_button(self, task, child):
        if child.get("id") in self._busy:
            b = Gtk.Label(label="⋯")
            b.add_css_class("busy")
            b.set_valign(Gtk.Align.START)
            return b
        if self._ticket_done(child):
            b = Gtk.Button(label="✓ Done")
            b.add_css_class("ticketdone"); b.add_css_class("done")
            b.set_valign(Gtk.Align.START)
            b.set_sensitive(False)
            b.set_tooltip_text("Already Done in Jira")
            return b
        b = Gtk.Button(label="done")
        b.add_css_class("ticketdone")
        b.set_valign(Gtk.Align.START)
        b.set_tooltip_text("Move this ticket to Done on the board")
        b.connect("clicked", lambda *_, tk=task, ch=child: self.transition_ticket(tk, ch))
        return b

    def transition_ticket(self, task, child):
        key = child.get("key")
        if not key or child.get("id") in self._busy:
            return
        self._busy.add(child["id"])
        self.set_status(f"{key}: moving to Done…")
        self.populate()

        def work():
            return B.jira_transition_done(key)

        def done(status):
            self._busy.discard(child["id"])
            child["status"] = status or "Done"
            if self._all_tickets_done(task):
                task["done"] = True
                self.set_status(f"{key} → {child['status']} · task completed", "ok")
            else:
                self.set_status(f"{key} → {child['status']}", "ok")
            B.save_tasks(self.store)
            if self._editing is task and task.get("done"):
                self.set_mode("add")
            self.populate()

        def fail(err):
            self._busy.discard(child["id"])
            self.set_status(f"{key}: {err}", "err")
            self.populate()

        run_async(work, done, fail)

    # ------------------------------------------------------------- sync
    def on_sync(self, *_):
        self.set_status("syncing…")
        self.sync_jira()

    def sync_jira(self):
        tickets = [(t, c) for t in self.store["tasks"] for c in t.get("children", [])
                   if c.get("kind") == "ticket" and c.get("key")]

        def work():
            return ({c["key"]: B.jira_status(c["key"]) for _t, c in tickets},
                    B.jira_my_open_count())

        def done(res):
            statuses, open_count = res
            for _t, c in tickets:
                st = statuses.get(c["key"])
                if st:
                    c["status"] = st
            B.save_tasks(self.store)
            project = B.config()["jira"]["project"]
            self.count_lbl.set_text(
                f"{project}: {open_count} open" if open_count is not None else f"{project}: —"
            )
            self.populate()
            self.set_status("synced with Jira", "ok")

        def fail(err):
            self.set_status("Jira: " + err, "err")

        run_async(work, done, fail)


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.example.taskpanel")
        self.win = None

    def do_activate(self):
        if self.win is None:
            self.win = Panel(self)
        self.win.present()


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    app = App()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
