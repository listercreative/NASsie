import contextlib
import io
import os
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText

from core import SMBWizard, QR_PASSWORD_RESET_NOTE


def _center_over_parent(win, parent):
    # Tkinter Toplevels default to wherever the window manager feels like
    # (often the screen's top-left corner), not anywhere near the window
    # that spawned them - place it over its parent instead.
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 2
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class AddUserDialog(tk.Toplevel):
    def __init__(self, parent, existing_usernames=(), show_access_level=True):
        super().__init__(parent)
        self.title("Add User")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        # Editable combobox, not a plain Entry: existing usernames are one
        # click away (no risk of a typo against a name that already
        # exists), but typing a name that isn't in the list still works -
        # this can create a brand-new user, unlike group membership which
        # requires an existing one.
        ttk.Label(self, text="Username:").grid(row=0, column=0, sticky="e", padx=8, pady=6)
        self.username_var = tk.StringVar()
        self.username_entry = ttk.Combobox(self, textvariable=self.username_var, values=list(existing_usernames))
        self.username_entry.grid(row=0, column=1, padx=8, pady=6)

        ttk.Label(self, text="Password:").grid(row=1, column=0, sticky="e", padx=8, pady=6)
        self.password_entry = ttk.Entry(self, show="*")
        self.password_entry.grid(row=1, column=1, padx=8, pady=6)

        ttk.Label(self, text="Confirm Password:").grid(row=2, column=0, sticky="e", padx=8, pady=6)
        self.confirm_entry = ttk.Entry(self, show="*")
        self.confirm_entry.grid(row=2, column=1, padx=8, pady=6)

        # Only relevant when this user is being granted access to a share -
        # not shown for standalone user creation, which has no share (and
        # so no access level) to set at all.
        self.show_access_level = show_access_level
        self.read_only_var = tk.BooleanVar(value=False)
        next_row = 3
        if show_access_level:
            ttk.Checkbutton(
                self, text="Read-only access", variable=self.read_only_var
            ).grid(row=3, column=0, columnspan=2, pady=(0, 6))
            next_row = 4

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=next_row, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        self.username_entry.focus_set()
        _center_over_parent(self, parent)
        self.grab_set()
        self.wait_window(self)

    def _on_ok(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        confirm = self.confirm_entry.get()

        if not username:
            messagebox.showerror("Invalid input", "Username cannot be empty.", parent=self)
            return
        if not password:
            messagebox.showerror("Invalid input", "Password cannot be empty.", parent=self)
            return
        if password != confirm:
            messagebox.showerror("Invalid input", "Passwords do not match.", parent=self)
            return

        self.result = {"username": username, "password": password}
        if self.show_access_level:
            self.result["read_only"] = self.read_only_var.get()
        self.destroy()


class ChoiceDialog(tk.Toplevel):
    """Generic pick-one-from-a-list dialog, reused for revoke share access,
    assign to group, and remove from group."""
    def __init__(self, parent, title, prompt, choices, ok_label="OK"):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        ttk.Label(self, text=prompt).grid(row=0, column=0, columnspan=2, padx=8, pady=6)
        self.choice_var = tk.StringVar(value=choices[0])
        combo = ttk.Combobox(self, textvariable=self.choice_var, values=choices, state="readonly")
        combo.grid(row=1, column=0, columnspan=2, padx=8, pady=6)

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text=ok_label, command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        _center_over_parent(self, parent)
        self.grab_set()
        self.wait_window(self)

    def _on_ok(self):
        self.result = self.choice_var.get()
        self.destroy()


class QrCodeDialog(tk.Toplevel):
    """Shows a LockNAS bridge QR code for one just-created (or just-granted)
    user. Only ever constructible with a payload already in hand - Kelpie
    doesn't persist plaintext passwords, so this can't be regenerated later
    for an existing user from a "Manage Shares" style screen."""
    def __init__(self, parent, share_name, username, payload):
        super().__init__(parent)
        self.title(f"QR Code - {username}")
        self.resizable(False, False)
        self.transient(parent)

        try:
            import qrcode
            img = qrcode.make(payload)
            fd, png_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            try:
                img.save(png_path)
                # Keep a reference on self - PhotoImage has no strong
                # reference of its own, and Tk garbage-collects it out from
                # under the Label the instant nothing else in Python still
                # points to it.
                self._photo = tk.PhotoImage(file=png_path)
            finally:
                try:
                    os.remove(png_path)
                except Exception:
                    pass
        except Exception as e:
            # Without this, a failure here (e.g. a missing/incompletely
            # bundled Pillow codec) left a blank, empty Toplevel on screen -
            # the window itself is already created by this point (title set
            # above), but nothing was ever packed into it - instead of a
            # clear error.
            self.destroy()
            messagebox.showerror("QR code unavailable", f"Couldn't generate the QR code: {e}", parent=parent)
            return

        ttk.Label(
            self, text=f"Scan for easy external configuration of '{share_name}' as {username}", padding=8
        ).pack()
        ttk.Label(self, image=self._photo).pack(padx=12, pady=4)
        ttk.Label(self, text="Compatible with the LockNAS app's bridge QR scanner.", padding=(8, 0)).pack()
        ttk.Label(
            self,
            text="Contains this user's password in plain sight - don't leave it\n"
                 "on screen or let anyone photograph it who shouldn't have access.",
            foreground="#b00000", justify="center", padding=8,
        ).pack()
        ttk.Button(self, text="Close", command=self.destroy).pack(pady=(0, 10))

        _center_over_parent(self, parent)
        self.grab_set()
        self.wait_window(self)


class GUIWizard:
    def __init__(self):
        self.wizard = SMBWizard()
        self.pending_users = []

        # className sets WM_CLASS, which is what taskbars/docks/app-switchers
        # use to match this running window back to kelpie.desktop (and thus
        # its Icon=) - without it Tk defaults to the generic class "Tk" and
        # the tray/taskbar icon can end up generic even though the titlebar
        # icon (set below) looks right.
        self.root = tk.Tk(className="Kelpie")
        self.root.title("Kelpie")
        # Tk has no built-in "center on screen" - left alone, the window
        # manager decides placement, which is commonly the top-left corner
        # rather than anywhere near the middle of the display.
        width, height = 560, 600
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self._load_icon_image()
        self._set_window_icon()
        self._build_header()

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.create_tab = ttk.Frame(notebook)
        self.manage_tab = ttk.Frame(notebook)
        self.users_groups_tab = ttk.Frame(notebook)
        notebook.add(self.create_tab, text="Create Share")
        notebook.add(self.manage_tab, text="Manage Shares")
        notebook.add(self.users_groups_tab, text="Users & Groups")
        notebook.bind("<<NotebookTabChanged>>", lambda e: self._refresh_all_lists())

        self._build_create_tab()
        self._build_manage_tab()
        self._build_users_groups_tab()
        self._refresh_all_lists()
        self._bring_to_front()

    def _bring_to_front(self):
        # When Kelpie is launched by the "Launch Kelpie" checkbox
        # (WixShellExec, run from the installer's own process, not the
        # user's foreground one), Windows' foreground-lock restrictions
        # silently ignore a plain lift()/focus_force() from a background
        # process, leaving the window open but buried behind others.
        # Toggling -topmost forces a z-order change instead, which isn't
        # subject to that restriction, and reliably drags the window to
        # front as a side effect.
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def run(self):
        self.root.mainloop()

    def _load_icon_image(self):
        # Ships right next to this file both in the source tree and in the
        # installed package (see build.sh). A frozen PyInstaller --onefile
        # build extracts its data files (see build.ps1's --add-data) into a
        # temp dir at sys._MEIPASS instead - __file__ isn't a real path
        # there, so that has to be checked first.
        if getattr(sys, 'frozen', False):
            base_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "kelpie_icon.png")
        try:
            self._icon_image = tk.PhotoImage(file=icon_path)
        except tk.TclError:
            self._icon_image = None

    def _set_window_icon(self):
        if self._icon_image:
            self.root.iconphoto(True, self._icon_image)

    def _build_header(self):
        # The real logo, front and center - not the TUI's ASCII rendition -
        # so the app's branding is obvious the moment the window opens, not
        # just in the titlebar/taskbar.
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=12, pady=(12, 0))

        if self._icon_image:
            scale = max(1, self._icon_image.width() // 56)
            self._header_icon_image = self._icon_image.subsample(scale, scale)
            ttk.Label(header, image=self._header_icon_image).pack(side="left", padx=(0, 10))

        ttk.Label(header, text="Kelpie", font=("TkDefaultFont", 18, "bold")).pack(side="left")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=8, pady=(10, 0))

    def _build_create_tab(self):
        frame = self.create_tab

        form = ttk.Frame(frame)
        form.pack(fill="x", padx=8, pady=8)

        ttk.Label(form, text="Share Name:").grid(row=0, column=0, sticky="e", pady=4)
        self.name_entry = ttk.Entry(form, width=40)
        self.name_entry.grid(row=0, column=1, columnspan=2, sticky="w", pady=4)
        self.name_entry.bind("<KeyRelease>", self._on_name_changed)

        ttk.Label(form, text="Folder Path:").grid(row=1, column=0, sticky="e", pady=4)
        self.path_entry = ttk.Entry(form, width=32)
        self.path_entry.grid(row=1, column=1, sticky="w", pady=4)
        self._last_suggested_path = self.wizard.default_share_path()
        self.path_entry.insert(0, self._last_suggested_path)
        ttk.Button(form, text="Browse...", command=self._browse_path).grid(row=1, column=2, padx=4)

        users_label_frame = ttk.LabelFrame(frame, text="Users")
        users_label_frame.pack(fill="both", expand=False, padx=8, pady=8)

        self.users_list = ttk.Treeview(users_label_frame, columns=("username",), show="headings", height=5)
        self.users_list.heading("username", text="Username")
        self.users_list.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)

        users_list_scroll = ttk.Scrollbar(users_label_frame, orient="vertical", command=self.users_list.yview)
        self.users_list.configure(yscrollcommand=users_list_scroll.set)
        users_list_scroll.pack(side="left", fill="y", pady=4)

        users_btn_frame = ttk.Frame(users_label_frame)
        users_btn_frame.pack(side="left", fill="y", padx=4, pady=4)
        ttk.Button(users_btn_frame, text="Add User...", command=self._add_user).pack(fill="x", pady=2)
        ttk.Button(users_btn_frame, text="Remove Selected", command=self._remove_user).pack(fill="x", pady=2)

        action_frame = ttk.Frame(frame)
        action_frame.pack(fill="x", padx=8, pady=4)
        self.create_button = ttk.Button(action_frame, text="Create Share", command=self._on_create_share)
        self.create_button.pack(side="left")
        self.status_label = ttk.Label(action_frame, text="Idle")
        self.status_label.pack(side="left", padx=10)

        ttk.Label(frame, text="Log:").pack(anchor="w", padx=8)
        self.log_text = ScrolledText(frame, height=10, state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_manage_tab(self):
        frame = self.manage_tab

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=8)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.shares_list = ttk.Treeview(tree_frame, columns=("name", "path", "users"), show="headings")
        self.shares_list.heading("name", text="Share Name")
        self.shares_list.heading("path", text="Path")
        self.shares_list.heading("users", text="Users")
        # stretch=False so a long path or SID (e.g. an unresolvable ACE's
        # raw "*S-1-5-21-..." form) can push the row past the visible
        # width and actually reach the horizontal scrollbar below, rather
        # than Treeview silently squeezing columns to fit and truncating
        # the text with no way to see the rest.
        self.shares_list.column("name", width=110, stretch=False)
        self.shares_list.column("path", width=220, stretch=False)
        self.shares_list.column("users", width=320, stretch=False)
        self.shares_list.grid(row=0, column=0, sticky="nsew")

        shares_vscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.shares_list.yview)
        shares_vscroll.grid(row=0, column=1, sticky="ns")
        shares_hscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.shares_list.xview)
        shares_hscroll.grid(row=1, column=0, sticky="ew")
        self.shares_list.configure(yscrollcommand=shares_vscroll.set, xscrollcommand=shares_hscroll.set)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btn_frame, text="Refresh", command=self._refresh_manage_list).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Add User...", command=self._add_user_to_selected_share).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Delete Selected", command=self._delete_selected_share).pack(side="left", padx=4)

    def _build_users_groups_tab(self):
        frame = self.users_groups_tab

        # Both trees are hierarchical: a top-level row per group/user, with
        # its members/shares (or groups/shares) nested as child rows -
        # "show='tree'" uses just the indent/expand column, since the
        # hierarchy itself conveys the relationship.
        groups_frame = ttk.LabelFrame(frame, text="Groups")
        groups_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        groups_frame.rowconfigure(0, weight=1)
        groups_frame.columnconfigure(0, weight=1)
        self.groups_list = ttk.Treeview(groups_frame, show="tree", height=6)
        # A long nested line (e.g. "share: <name> (read-only)" next to a
        # raw SID entry) can exceed this without ever showing past the
        # window edge otherwise - stretch=False plus the horizontal
        # scrollbar below is what actually makes the rest reachable.
        self.groups_list.column("#0", width=400, stretch=False)
        self.groups_list.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)

        groups_vscroll = ttk.Scrollbar(groups_frame, orient="vertical", command=self.groups_list.yview)
        groups_vscroll.grid(row=0, column=1, sticky="ns", pady=4)
        groups_hscroll = ttk.Scrollbar(groups_frame, orient="horizontal", command=self.groups_list.xview)
        groups_hscroll.grid(row=1, column=0, sticky="ew", padx=(4, 0))
        self.groups_list.configure(yscrollcommand=groups_vscroll.set, xscrollcommand=groups_hscroll.set)

        # A grid of equal-width columns instead of left-packed rows of
        # varying button widths - packed rows left every row a different
        # total width (jagged/uneven), since each button only takes the
        # width its own label needs. Grouped by what the actions do:
        # group lifecycle, membership, then share access.
        groups_btn_frame = ttk.Frame(frame)
        groups_btn_frame.pack(fill="x", padx=8, pady=(0, 4))
        for col in range(3):
            groups_btn_frame.columnconfigure(col, weight=1, uniform="groups_btn")

        def group_button(text, command, row, col):
            ttk.Button(groups_btn_frame, text=text, command=command).grid(
                row=row, column=col, sticky="ew", padx=4, pady=2
            )

        group_button("New Group...", self._create_new_group, 0, 0)
        group_button("Delete Group", self._delete_selected_group, 0, 1)
        group_button("Add Member...", self._add_group_member, 1, 0)
        group_button("Remove Member...", self._remove_group_member, 1, 1)
        group_button("Assign to Share...", self._assign_group_to_share, 2, 0)
        group_button("Remove from Share...", self._unassign_group_from_share, 2, 1)
        group_button("Set Access Level...", self._set_access_level_for_group, 2, 2)

        users_frame = ttk.LabelFrame(frame, text="Users")
        users_frame.pack(fill="both", expand=True, padx=8, pady=(4, 4))
        users_frame.rowconfigure(0, weight=1)
        users_frame.columnconfigure(0, weight=1)
        self.system_users_list = ttk.Treeview(users_frame, show="tree", height=6)
        self.system_users_list.column("#0", width=400, stretch=False)
        self.system_users_list.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)

        system_users_vscroll = ttk.Scrollbar(
            users_frame, orient="vertical", command=self.system_users_list.yview
        )
        system_users_vscroll.grid(row=0, column=1, sticky="ns", pady=4)
        system_users_hscroll = ttk.Scrollbar(
            users_frame, orient="horizontal", command=self.system_users_list.xview
        )
        system_users_hscroll.grid(row=1, column=0, sticky="ew", padx=(4, 0))
        self.system_users_list.configure(
            yscrollcommand=system_users_vscroll.set, xscrollcommand=system_users_hscroll.set
        )

        # Two rows, not one - six buttons packed side="left" don't fit the
        # default 560px window width, and pack() doesn't wrap on its own;
        # the overflow buttons (this bit everyone with "Reset Password &
        # Show QR...") just get pushed off the visible edge instead of onto
        # a new row.
        users_btn_row1 = ttk.Frame(frame)
        users_btn_row1.pack(fill="x", padx=8, pady=(0, 2))
        ttk.Button(users_btn_row1, text="New User...", command=self._create_new_user).pack(side="left", padx=4)
        ttk.Button(users_btn_row1, text="Assign to Group...", command=self._assign_selected_user_to_group).pack(
            side="left", padx=4
        )
        ttk.Button(
            users_btn_row1, text="Remove from Group...", command=self._remove_selected_user_from_group
        ).pack(side="left", padx=4)

        users_btn_row2 = ttk.Frame(frame)
        users_btn_row2.pack(fill="x", padx=8, pady=(0, 2))
        ttk.Button(users_btn_row2, text="Revoke Access...", command=self._revoke_selected_user_access).pack(
            side="left", padx=4
        )
        ttk.Button(
            users_btn_row2, text="Change Access Level...", command=self._change_access_level_for_selected_user
        ).pack(side="left", padx=4)

        users_btn_row3 = ttk.Frame(frame)
        users_btn_row3.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Button(
            users_btn_row3, text="Reset Password & Show QR...", command=self._reset_password_for_selected_user
        ).pack(side="left", padx=4)
        ttk.Button(users_btn_row3, text="Delete User", command=self._delete_selected_user).pack(
            side="left", padx=4
        )

        ttk.Button(frame, text="Refresh", command=self._refresh_users_groups).pack(
            padx=8, pady=(0, 8), anchor="w"
        )

    def _populate_users_groups(self, groups, users, access_lookup, overrides, shares):
        # (group_name, share_name) -> read_only, for the access level shown
        # next to each of a group's assigned shares below.
        group_share_access = {
            (s["access_group"], s["name"]): s.get("access_group_read_only", False)
            for s in shares if s.get("access_group")
        }

        for item in self.groups_list.get_children():
            self.groups_list.delete(item)
        for g in groups:
            gid = self.groups_list.insert("", tk.END, text=g["name"], open=True)
            for m in g["members"]:
                self.groups_list.insert(gid, tk.END, text=f"user: {m}")
            for s in g["shares"]:
                level = "read-only" if group_share_access.get((g["name"], s)) else "full access"
                self.groups_list.insert(gid, tk.END, text=f"share: {s} ({level})")

        for item in self.system_users_list.get_children():
            self.system_users_list.delete(item)
        for u in users:
            uid = self.system_users_list.insert("", tk.END, text=u["username"], open=True)
            for g in u["groups"]:
                self.system_users_list.insert(uid, tk.END, text=f"group: {g}")
            for s in u["shares"]:
                suffix = " (read-only)" if access_lookup.get((s, u["username"])) else ""
                override = overrides.get((s, u["username"]))
                if override:
                    # This user's own read-only setting above is masked by
                    # a group grant - Windows/Samba/macOS all resolve
                    # group access without regard to it, so it's misleading
                    # to show just "(read-only)" with nothing else to
                    # explain why they can actually still write.
                    suffix += f" [overridden by group '{override[0]}': full access]"
                self.system_users_list.insert(uid, tk.END, text=f"share: {s}{suffix}")

    def _refresh_users_groups(self):
        shares = self.wizard.list_shares()
        groups = self.wizard.list_groups()
        self._populate_users_groups(
            groups, self.wizard.list_users(), self.wizard.build_access_lookup(shares),
            self.wizard.effective_share_access(shares, groups), shares
        )

    def _refresh_all_lists(self):
        # Bound to <<NotebookTabChanged>> - fires on every tab click.
        # list_shares/list_groups/list_users each shell out (PowerShell on
        # Windows especially, one process per call even after batching),
        # so running them synchronously here made every tab switch
        # visibly stall the whole window. Fetch in the background and
        # apply to the Treeviews on the main thread once ready.
        threading.Thread(target=self._refresh_all_lists_worker, daemon=True).start()

    def _refresh_all_lists_worker(self):
        shares = self.wizard.list_shares()
        groups = self.wizard.list_groups()
        users = self.wizard.list_users()

        access_lookup = self.wizard.build_access_lookup(shares)
        overrides = self.wizard.effective_share_access(shares, groups)

        def apply():
            self._populate_shares_list(shares, overrides)
            self._populate_users_groups(groups, users, access_lookup, overrides, shares)

        self.root.after(0, apply)

    def _selected_top_level_text(self, tree):
        # Action buttons work whether you selected the top-level row
        # (group/user) or one of its nested children - walk up to the
        # top-level ancestor and return its label either way.
        selection = tree.selection()
        if not selection:
            return None
        item = selection[0]
        while tree.parent(item):
            item = tree.parent(item)
        return tree.item(item, "text")

    def _browse_path(self):
        selected = filedialog.askdirectory(parent=self.root, title="Select Folder to Share")
        if selected:
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, selected)

    def _on_name_changed(self, event=None):
        # Keep the suggested path in sync with the share name, but only
        # while the user hasn't typed/browsed a path of their own.
        if self.path_entry.get() != self._last_suggested_path:
            return
        name = self.name_entry.get().strip()
        suggested = self.wizard.default_share_path(name) if name else self.wizard.default_share_path()
        self.path_entry.delete(0, tk.END)
        self.path_entry.insert(0, suggested)
        self._last_suggested_path = suggested

    def _add_user(self):
        existing = [u["username"] for u in self.wizard.list_users()]
        dialog = AddUserDialog(self.root, existing_usernames=existing)
        if dialog.result:
            self.pending_users.append(dialog.result)
            self.users_list.insert("", tk.END, values=(dialog.result["username"],))

    def _remove_user(self):
        selection = self.users_list.selection()
        for item in selection:
            index = self.users_list.index(item)
            del self.pending_users[index]
            self.users_list.delete(item)

    def _populate_shares_list(self, shares, overrides=None):
        # list_shares() only reflects live share config (Get-SmbShare /
        # smb.conf) - it has no idea whether the folder that config points
        # at still exists, since the OS never removes a share just because
        # its target got deleted out from under it. Flag that here instead,
        # so an orphaned share (folder deleted outside Kelpie) is visibly
        # different from a normal one rather than silently looking fine.
        overrides = overrides or {}
        for item in self.shares_list.get_children():
            self.shares_list.delete(item)
        for share in shares:
            user_labels = []
            for u in share.get("users", []):
                label = u["username"] + (" (read-only)" if u.get("read_only") else "")
                override = overrides.get((share.get("name"), u["username"]))
                if override:
                    # Their own read-only setting above is masked by a
                    # group grant - see effective_share_access().
                    label += f" [overridden by group '{override[0]}']"
                user_labels.append(label)
            users = ", ".join(user_labels) or "(none)"
            if share.get("access_group"):
                level = "read-only" if share.get("access_group_read_only") else "full access"
                users += f"; group '{share['access_group']}' ({level})"
            path = share.get("path") or "Unknown"
            if path != "Unknown" and not os.path.isdir(path):
                path = f"{path}  (folder missing!)"
            self.shares_list.insert(
                "", tk.END, values=(share.get("name", "?"), path, users)
            )

    def _refresh_manage_list(self):
        shares = self.wizard.list_shares()
        overrides = self.wizard.effective_share_access(shares, self.wizard.list_groups())
        self._populate_shares_list(shares, overrides)

    def _add_user_to_selected_share(self):
        selection = self.shares_list.selection()
        if not selection:
            messagebox.showinfo("Add user", "Select a share first.")
            return
        share_name = self.shares_list.item(selection[0], "values")[0]
        existing = [u["username"] for u in self.wizard.list_users()]
        dialog = AddUserDialog(self.root, existing_usernames=existing)
        if not dialog.result:
            return
        username = dialog.result["username"]
        password = dialog.result["password"]
        read_only = dialog.result.get("read_only", False)
        threading.Thread(
            target=self._grant_access_worker, args=(share_name, username, password, read_only), daemon=True
        ).start()

    def _reset_password_for_selected_user(self):
        # Passwords are stored as hashes everywhere (Samba, Windows, macOS)
        # - there's no way to retrieve an existing user's current password
        # to encode. The only honest way to show a QR for an
        # already-existing user is to reset it and encode the new one -
        # same underlying operation _add_user_to_selected_share uses, so
        # this reuses the same worker/QR-offer path, just starting from the
        # user's own screen instead of a share's.
        username = self._selected_top_level_text(self.system_users_list)
        if not username:
            messagebox.showinfo("Reset Password & Show QR", "Select a user first.")
            return
        user = next((u for u in self.wizard.list_users() if u["username"] == username), None)
        shares = user.get("shares", []) if user else []
        if not shares:
            messagebox.showinfo("Reset Password & Show QR", f"'{username}' doesn't have access to any share yet.")
            return

        if not messagebox.askokcancel("Reset Password & Show QR", QR_PASSWORD_RESET_NOTE):
            return

        if len(shares) == 1:
            share_name = shares[0]
        else:
            dialog = ChoiceDialog(
                self.root, "Choose share", "Reset password & show QR code for which share?",
                shares, ok_label="Next"
            )
            if not dialog.result:
                return
            share_name = dialog.result

        from tkinter import simpledialog
        password = simpledialog.askstring(
            "New password",
            f"New password for '{username}' (replaces their current one):",
            show="*", parent=self.root,
        )
        if not password:
            return

        # Preserve the user's current access level on this share - a
        # password reset shouldn't silently flip them back to read-write.
        share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
        share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
        read_only = share_user.get("read_only", False) if share_user else False

        threading.Thread(
            target=self._grant_access_worker, args=(share_name, username, password, read_only), daemon=True
        ).start()

    def _change_access_level_for_selected_user(self):
        username = self._selected_top_level_text(self.system_users_list)
        if not username:
            messagebox.showinfo("Change Access Level", "Select a user first.")
            return
        user = next((u for u in self.wizard.list_users() if u["username"] == username), None)
        shares = user.get("shares", []) if user else []
        if not shares:
            messagebox.showinfo("Change Access Level", f"'{username}' doesn't have access to any share yet.")
            return

        if len(shares) == 1:
            share_name = shares[0]
        else:
            dialog = ChoiceDialog(
                self.root, "Choose share", "Change access level for which share?", shares, ok_label="Next"
            )
            if not dialog.result:
                return
            share_name = dialog.result

        share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
        share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
        current_read_only = share_user.get("read_only", False) if share_user else False
        current_label = "read-only" if current_read_only else "read-write"
        new_label = "read-write" if current_read_only else "read-only"
        if not messagebox.askyesno(
            "Change Access Level",
            f"'{username}' is currently {current_label} on '{share_name}'.\n\nChange to {new_label}?",
        ):
            return

        threading.Thread(
            target=self._change_access_worker, args=(share_name, username, not current_read_only), daemon=True
        ).start()

    def _change_access_worker(self, share_name, username, read_only):
        buffer = io.StringIO()
        changed = False
        try:
            with contextlib.redirect_stdout(buffer):
                changed = self.wizard.change_share_access(share_name, username, read_only)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._change_access_done(share_name, username, read_only, changed, buffer.getvalue()))

    def _change_access_done(self, share_name, username, read_only, changed, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if changed:
            level = "read-only" if read_only else "read-write"
            messagebox.showinfo("Changed", f"'{username}' is now {level} on '{share_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not change '{username}''s access level — see log.")
        self._refresh_all_lists()

    def _grant_access_worker(self, share_name, username, password, read_only=False):
        buffer = io.StringIO()
        added = False
        try:
            with contextlib.redirect_stdout(buffer):
                added = self.wizard.grant_share_access(share_name, username, password, read_only)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(
            0, lambda: self._grant_access_done(share_name, username, password, added, buffer.getvalue())
        )

    def _grant_access_done(self, share_name, username, password, added, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if added:
            messagebox.showinfo("Added", f"Added '{username}' to share '{share_name}'.")
            self._offer_qr_codes(share_name, [{"username": username, "password": password}])
        else:
            messagebox.showerror("Failed", f"Could not add '{username}' to share '{share_name}' — see log.")
        self._refresh_all_lists()

    def _assign_selected_user_to_group(self):
        username = self._selected_top_level_text(self.system_users_list)
        if not username:
            messagebox.showinfo("Assign to group", "Select a user first.")
            return
        groups = self.wizard.list_groups()
        if not groups:
            messagebox.showinfo("Assign to group", "No groups exist to assign to.")
            return
        dialog = ChoiceDialog(
            self.root, "Assign to Group", f"Assign '{username}' to:",
            [g["name"] for g in groups], ok_label="Assign"
        )
        if not dialog.result:
            return
        threading.Thread(
            target=self._assign_group_worker, args=(username, dialog.result), daemon=True
        ).start()

    def _assign_group_worker(self, username, group_name):
        buffer = io.StringIO()
        ok = False
        try:
            with contextlib.redirect_stdout(buffer):
                ok = self.wizard.assign_user_to_group(username, group_name)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._assign_group_done(username, group_name, ok, buffer.getvalue()))

    def _assign_group_done(self, username, group_name, ok, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if ok:
            messagebox.showinfo("Added", f"Added '{username}' to group '{group_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not add '{username}' to group '{group_name}' — see log.")
        self._refresh_all_lists()

    def _remove_selected_user_from_group(self):
        username = self._selected_top_level_text(self.system_users_list)
        if not username:
            messagebox.showinfo("Remove from group", "Select a user first.")
            return
        user = next((u for u in self.wizard.list_users() if u["username"] == username), None)
        if not user or not user["groups"]:
            messagebox.showinfo("Remove from group", f"'{username}' isn't in any group.")
            return
        dialog = ChoiceDialog(
            self.root, "Remove from Group", f"Remove '{username}' from:",
            user["groups"], ok_label="Remove"
        )
        if not dialog.result:
            return
        threading.Thread(
            target=self._revoke_group_worker, args=(username, dialog.result), daemon=True
        ).start()

    def _add_group_member(self):
        group_name = self._selected_top_level_text(self.groups_list)
        if not group_name:
            messagebox.showinfo("Add member", "Select a group first.")
            return
        # A dropdown of existing users, not free text: adding a member
        # requires an already-existing account (unlike "Add User to Share",
        # which can create one) - _add_user_to_group_linux validates and
        # refuses otherwise, so free text here could only ever fail.
        usernames = [u["username"] for u in self.wizard.list_users()]
        if not usernames:
            messagebox.showinfo("Add member", "No existing users to add. Create one via 'Add User to Share' first.")
            return
        dialog = ChoiceDialog(
            self.root, "Add Member", f"Add which user to '{group_name}'?", usernames, ok_label="Add"
        )
        if not dialog.result:
            return
        threading.Thread(
            target=self._assign_group_worker, args=(dialog.result, group_name), daemon=True
        ).start()

    def _remove_group_member(self):
        group_name = self._selected_top_level_text(self.groups_list)
        if not group_name:
            messagebox.showinfo("Remove member", "Select a group first.")
            return
        group = next((g for g in self.wizard.list_groups() if g["name"] == group_name), None)
        if not group or not group["members"]:
            messagebox.showinfo("Remove member", f"'{group_name}' has no members.")
            return
        dialog = ChoiceDialog(
            self.root, "Remove Member", f"Remove which member of '{group_name}'?",
            group["members"], ok_label="Remove"
        )
        if not dialog.result:
            return
        threading.Thread(
            target=self._revoke_group_worker, args=(dialog.result, group_name), daemon=True
        ).start()

    def _revoke_group_worker(self, username, group_name):
        buffer = io.StringIO()
        ok = False
        try:
            with contextlib.redirect_stdout(buffer):
                ok = self.wizard.revoke_group_membership(username, group_name)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._revoke_group_done(username, group_name, ok, buffer.getvalue()))

    def _revoke_group_done(self, username, group_name, ok, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if ok:
            messagebox.showinfo("Removed", f"Removed '{username}' from group '{group_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not remove '{username}' from group '{group_name}' — see log.")
        self._refresh_all_lists()

    def _set_access_level_for_group(self):
        # Bulk-applies to every CURRENT member of the group, right now - not
        # a persistent group-level grant. Someone added to the group later
        # doesn't automatically inherit this.
        group_name = self._selected_top_level_text(self.groups_list)
        if not group_name:
            messagebox.showinfo("Set Access Level", "Select a group first.")
            return
        group = next((g for g in self.wizard.list_groups() if g["name"] == group_name), None)
        if not group or not group["members"]:
            messagebox.showinfo("Set Access Level", f"'{group_name}' has no members yet.")
            return
        if not group["shares"]:
            messagebox.showinfo("Set Access Level", f"'{group_name}' isn't attached to any share.")
            return

        if len(group["shares"]) == 1:
            share_name = group["shares"][0]
        else:
            dialog = ChoiceDialog(
                self.root, "Choose share", f"Set access level on which share for '{group_name}'?",
                group["shares"], ok_label="Next"
            )
            if not dialog.result:
                return
            share_name = dialog.result

        dialog = ChoiceDialog(
            self.root, "Access level", f"Set every current member of '{group_name}' to:",
            ["Read-write", "Read-only"], ok_label="Apply"
        )
        if not dialog.result:
            return
        read_only = dialog.result == "Read-only"

        if not messagebox.askyesno(
            "Set Access Level",
            f"Apply {dialog.result.lower()} access to all {len(group['members'])} current member(s) "
            f"of '{group_name}' on '{share_name}'?",
        ):
            return

        threading.Thread(
            target=self._set_group_access_worker, args=(group_name, share_name, read_only), daemon=True
        ).start()

    def _set_group_access_worker(self, group_name, share_name, read_only):
        buffer = io.StringIO()
        ok = False
        try:
            with contextlib.redirect_stdout(buffer):
                ok = self.wizard.change_group_access(group_name, share_name, read_only)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(
            0, lambda: self._set_group_access_done(group_name, share_name, read_only, ok, buffer.getvalue())
        )

    def _set_group_access_done(self, group_name, share_name, read_only, ok, log_output):
        if log_output.strip():
            self._append_log(log_output)
        level = "read-only" if read_only else "read-write"
        if ok:
            messagebox.showinfo("Done", f"Set '{group_name}''s members to {level} on '{share_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not change access level for '{group_name}' — see log.")
        self._refresh_all_lists()

    def _revoke_selected_user_access(self):
        username = self._selected_top_level_text(self.system_users_list)
        if not username:
            messagebox.showinfo("Revoke access", "Select a user first.")
            return
        user = next((u for u in self.wizard.list_users() if u["username"] == username), None)
        if not user or not user["shares"]:
            messagebox.showinfo("Revoke access", f"'{username}' has no share access to revoke.")
            return
        dialog = ChoiceDialog(
            self.root, "Revoke Access", f"Revoke '{username}' access to:", user["shares"], ok_label="Revoke"
        )
        if not dialog.result:
            return
        share_name = dialog.result
        threading.Thread(
            target=self._revoke_access_worker, args=(share_name, username), daemon=True
        ).start()

    def _revoke_access_worker(self, share_name, username):
        buffer = io.StringIO()
        revoked = False
        try:
            with contextlib.redirect_stdout(buffer):
                revoked = self.wizard.revoke_share_access(share_name, username)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._revoke_access_done(share_name, username, revoked, buffer.getvalue()))

    def _revoke_access_done(self, share_name, username, revoked, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if revoked:
            messagebox.showinfo("Revoked", f"Revoked '{username}''s access to '{share_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not revoke access — see log.")
        self._refresh_all_lists()

    def _delete_selected_user(self):
        username = self._selected_top_level_text(self.system_users_list)
        if not username:
            messagebox.showinfo("Delete user", "Select a user first.")
            return
        if not messagebox.askyesno(
            "Delete user", f"Delete user '{username}' entirely? This removes their account everywhere, not just one share."
        ):
            return
        threading.Thread(target=self._delete_user_worker, args=(username,), daemon=True).start()

    def _delete_user_worker(self, username):
        buffer = io.StringIO()
        deleted = False
        try:
            with contextlib.redirect_stdout(buffer):
                deleted = self.wizard.remove_user(username)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._delete_user_done(username, deleted, buffer.getvalue()))

    def _delete_user_done(self, username, deleted, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if deleted:
            messagebox.showinfo("Deleted", f"Deleted user '{username}'.")
        else:
            messagebox.showerror("Failed", f"Could not delete user '{username}' — see log.")
        self._refresh_all_lists()

    def _create_new_user(self):
        # A standalone account, not attached to any share or group - lets a
        # user get set up ahead of deciding what to grant them access to,
        # instead of forcing a throwaway share into existence just to get
        # the account created.
        existing = [u["username"] for u in self.wizard.list_users()]
        dialog = AddUserDialog(self.root, existing_usernames=existing, show_access_level=False)
        if not dialog.result:
            return
        username = dialog.result["username"]
        password = dialog.result["password"]
        threading.Thread(target=self._create_user_worker, args=(username, password), daemon=True).start()

    def _create_user_worker(self, username, password):
        buffer = io.StringIO()
        created = False
        try:
            with contextlib.redirect_stdout(buffer):
                created = self.wizard.add_user(username, password)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._create_user_done(username, created, buffer.getvalue()))

    def _create_user_done(self, username, created, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if created:
            messagebox.showinfo("Created", f"Created user '{username}'.")
        else:
            messagebox.showerror("Failed", f"Could not create user '{username}' — see log.")
        self._refresh_all_lists()

    def _delete_selected_group(self):
        group_name = self._selected_top_level_text(self.groups_list)
        if not group_name:
            messagebox.showinfo("Delete group", "Select a group first.")
            return
        if not messagebox.askyesno("Delete group", f"Delete group '{group_name}'?"):
            return
        threading.Thread(target=self._delete_group_worker, args=(group_name,), daemon=True).start()

    def _delete_group_worker(self, group_name):
        buffer = io.StringIO()
        deleted = False
        try:
            with contextlib.redirect_stdout(buffer):
                deleted = self.wizard.remove_group(group_name)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._delete_group_done(group_name, deleted, buffer.getvalue()))

    def _delete_group_done(self, group_name, deleted, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if deleted:
            messagebox.showinfo("Deleted", f"Deleted group '{group_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not delete group '{group_name}' — see log.")
        self._refresh_all_lists()

    def _create_new_group(self):
        # A standalone access-control group, not tied to any share until
        # explicitly assigned via "Assign to Share..." - unlike a share's
        # own auto-created ownership group (filesystem-permission-only,
        # never shown here), this one grants real, persistent SMB access
        # to whatever it's assigned to.
        name = simpledialog.askstring("New Group", "Group name:", parent=self.root)
        if not name or not name.strip():
            return
        threading.Thread(target=self._create_group_worker, args=(name.strip(),), daemon=True).start()

    def _create_group_worker(self, name):
        buffer = io.StringIO()
        system_name = None
        try:
            with contextlib.redirect_stdout(buffer):
                system_name = self.wizard.add_group(name)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._create_group_done(name, system_name, buffer.getvalue()))

    def _create_group_done(self, name, system_name, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if system_name:
            messagebox.showinfo("Created", f"Created group '{system_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not create group '{name}' — see log.")
        self._refresh_all_lists()

    def _assign_group_to_share(self):
        group_name = self._selected_top_level_text(self.groups_list)
        if not group_name:
            messagebox.showinfo("Assign to Share", "Select a group first.")
            return
        shares = [s["name"] for s in self.wizard.list_shares()]
        if not shares:
            messagebox.showinfo("Assign to Share", "No shares exist yet.")
            return
        share_dialog = ChoiceDialog(
            self.root, "Choose share", f"Grant '{group_name}' access to which share?", shares, ok_label="Next"
        )
        if not share_dialog.result:
            return
        share_name = share_dialog.result

        level_dialog = ChoiceDialog(
            self.root, "Access level", f"Grant '{group_name}' members:",
            ["Read-write", "Read-only"], ok_label="Assign"
        )
        if not level_dialog.result:
            return
        read_only = level_dialog.result == "Read-only"

        if not messagebox.askyesno(
            "Assign to Share",
            f"Grant '{group_name}' {level_dialog.result.lower()} access to '{share_name}'? "
            f"Every current and future member gets this access - not a one-time snapshot.",
        ):
            return

        threading.Thread(
            target=self._assign_group_share_worker, args=(group_name, share_name, read_only), daemon=True
        ).start()

    def _assign_group_share_worker(self, group_name, share_name, read_only):
        buffer = io.StringIO()
        ok = False
        try:
            with contextlib.redirect_stdout(buffer):
                ok = self.wizard.grant_group_share_access(group_name, share_name, read_only)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(
            0, lambda: self._assign_group_share_done(group_name, share_name, ok, buffer.getvalue())
        )

    def _assign_group_share_done(self, group_name, share_name, ok, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if ok:
            messagebox.showinfo("Assigned", f"Assigned '{group_name}' to '{share_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not assign '{group_name}' to '{share_name}' — see log.")
        self._refresh_all_lists()

    def _unassign_group_from_share(self):
        group_name = self._selected_top_level_text(self.groups_list)
        if not group_name:
            messagebox.showinfo("Remove from Share", "Select a group first.")
            return
        group = next((g for g in self.wizard.list_groups() if g["name"] == group_name), None)
        if not group or not group["shares"]:
            messagebox.showinfo("Remove from Share", f"'{group_name}' isn't assigned to any share.")
            return
        if len(group["shares"]) == 1:
            share_name = group["shares"][0]
        else:
            dialog = ChoiceDialog(
                self.root, "Choose share", f"Remove '{group_name}' from which share?",
                group["shares"], ok_label="Next"
            )
            if not dialog.result:
                return
            share_name = dialog.result

        if not messagebox.askyesno(
            "Remove from Share", f"Remove '{group_name}''s access to '{share_name}'?"
        ):
            return

        threading.Thread(
            target=self._unassign_group_share_worker, args=(group_name, share_name), daemon=True
        ).start()

    def _unassign_group_share_worker(self, group_name, share_name):
        buffer = io.StringIO()
        ok = False
        try:
            with contextlib.redirect_stdout(buffer):
                ok = self.wizard.revoke_group_share_access(group_name, share_name)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(
            0, lambda: self._unassign_group_share_done(group_name, share_name, ok, buffer.getvalue())
        )

    def _unassign_group_share_done(self, group_name, share_name, ok, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if ok:
            messagebox.showinfo("Removed", f"Removed '{group_name}''s access to '{share_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not remove '{group_name}''s access to '{share_name}' — see log.")
        self._refresh_all_lists()

    def _delete_selected_share(self):
        selection = self.shares_list.selection()
        if not selection:
            return
        name = self.shares_list.item(selection[0], "values")[0]
        if not messagebox.askyesno(
            "Remove share", f"Remove share '{name}'? This updates the live share configuration."
        ):
            return
        delete_folder = False
        share = next((s for s in self.wizard.list_shares() if s["name"] == name), None)
        path = share.get("path") if share else None
        if path:
            delete_folder = messagebox.askyesno(
                "Delete folder too?",
                f"Also permanently delete the folder and everything in it?\n\n{path}\n\n"
                "This cannot be undone.",
            )
        threading.Thread(target=self._delete_worker, args=(name, delete_folder), daemon=True).start()

    def _delete_worker(self, name, delete_folder=False):
        buffer = io.StringIO()
        removed = False
        try:
            with contextlib.redirect_stdout(buffer):
                removed = self.wizard.remove_share(name, delete_folder)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._delete_done(name, removed, delete_folder, buffer.getvalue()))

    def _delete_done(self, name, removed, delete_folder, log_output):
        if log_output.strip():
            self._append_log(log_output)
        if removed:
            messagebox.showinfo(
                "Removed", f"Removed share: {name}" + (" (folder deleted too)" if delete_folder else "")
            )
        else:
            messagebox.showerror("Failed", f"Could not remove share '{name}' — see log.")
        self._refresh_all_lists()

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _on_create_share(self):
        name = self.name_entry.get().strip()
        path = self.path_entry.get().strip() or self.wizard.default_share_path()

        if not name:
            messagebox.showerror("Invalid input", "Share name cannot be empty.")
            return
        path_ok, path_message = self.wizard.check_share_path(path)
        if not path_ok:
            messagebox.showerror("Invalid path", path_message)
            return

        self.wizard.share_name = name
        self.wizard.share_path = path
        self.wizard.users = list(self.pending_users)

        self.create_button.configure(state="disabled")
        self.status_label.configure(text="Working...")
        self._append_log(f"\n--- Creating share '{name}' ---\n")

        threading.Thread(target=self._apply_worker, daemon=True).start()

        self.name_entry.delete(0, tk.END)
        self.path_entry.delete(0, tk.END)
        self._last_suggested_path = self.wizard.default_share_path()
        self.path_entry.insert(0, self._last_suggested_path)
        self.pending_users = []
        for item in self.users_list.get_children():
            self.users_list.delete(item)

    def _apply_worker(self):
        # Captured up front, not re-read from self.wizard in _apply_done -
        # the "Create Share" button is disabled for the duration, but this
        # keeps the QR-offer step correct even if that ever changes.
        share_name = self.wizard.share_name
        users = list(self.wizard.users)
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                if self.wizard.has_admin_privileges():
                    self.wizard.dispatch_execution()
                else:
                    print("Not running with elevated privileges — requesting elevation via the OS's native prompt.")
                    self.wizard.elevate_and_apply({
                        "name": self.wizard.share_name,
                        "path": self.wizard.share_path,
                        "users": self.wizard.users
                    })
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")

        self.root.after(0, lambda: self._apply_done(buffer.getvalue(), share_name, users))

    def _apply_done(self, log_output, share_name, users):
        self._append_log(log_output)
        self.create_button.configure(state="normal")
        self.status_label.configure(text="Idle")
        self._refresh_all_lists()
        messagebox.showinfo("Done", "Configuration attempt finished — see the log for details.")
        if "Success." in log_output and users:
            self._offer_qr_codes(share_name, users)

    def _offer_qr_codes(self, share_name, users):
        # users: [{"username": ..., "password": ...}, ...] - only ever
        # offered right when a password was just set (share creation / add
        # user), since Kelpie never persists plaintext passwords and so has
        # no way to regenerate this later for an existing user.
        if not messagebox.askyesno(
            "QR Code",
            "Show a QR code for easy external configuration of this share?\n\n"
            "The code contains this user's password in plain sight - only display it "
            "somewhere private."
        ):
            return
        remaining = list(users)
        while remaining:
            if len(remaining) == 1:
                user = remaining[0]
            else:
                dialog = ChoiceDialog(
                    self.root, "Choose user", "Show QR code for which user?",
                    [u["username"] for u in remaining], ok_label="Show"
                )
                if not dialog.result:
                    return
                user = next(u for u in remaining if u["username"] == dialog.result)
            payload = self.wizard.build_locknas_qr_payload(share_name, user["username"], user["password"])
            QrCodeDialog(self.root, share_name, user["username"], payload)
            remaining = [u for u in remaining if u is not user]
            if remaining and not messagebox.askyesno("QR Code", "Show another user's QR code?"):
                return


def main():
    GUIWizard().run()


if __name__ == "__main__":
    main()
