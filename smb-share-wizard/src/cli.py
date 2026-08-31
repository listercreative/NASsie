import getpass

from rich.console import Console
from rich.panel import Panel

from core import SMBWizard, QR_PASSWORD_RESET_NOTE

console = Console()


class CLIWizard(SMBWizard):
    def _pick_or_type_username(self, prompt="   Username: "):
        # Existing usernames one selection away (no risk of a typo against a
        # name that already exists), but typing a new one still works -
        # this can create a brand-new user.
        users = self.list_users()
        if not users:
            return console.input(prompt).strip()
        print("   Existing users:")
        for i, u in enumerate(users):
            print(f"     {i}. {u['username']}")
        raw = console.input(f"{prompt}(number to pick existing, or type new): ").strip()
        if raw.isdigit() and int(raw) < len(users):
            return users[int(raw)]['username']
        return raw

    def _offer_qr_code(self, share_name, username, password):
        # Only ever offered right when a password was just set (share
        # creation / add user) - NASsie never persists plaintext passwords,
        # so this can't be regenerated later for an existing user.
        confirm = console.input("\nShow a QR code for easy external configuration? [y/N] ").strip().lower()
        if confirm not in ('y', 'yes'):
            return
        try:
            import qrcode
        except ImportError:
            console.print("[red]The 'qrcode' package isn't installed.[/red]")
            return
        console.print(
            "[bold red]Contains this user's password in plain sight - only display "
            "it somewhere private.[/bold red]"
        )
        console.print("[dim](Compatible with the LockNAS app's bridge QR scanner.)[/dim]")
        payload = self.build_locknas_qr_payload(share_name, username, password)
        qr = qrcode.QRCode()
        qr.add_data(payload)
        qr.make()
        qr.print_ascii(tty=True)
        console.input("\nPress Enter to continue...")

    def collect_user_input(self):
        console.print(Panel("[bold blue]New SMB Share Creation[/bold blue]", expand=False))

        self.share_name = console.input("[bold]1. Enter the name for the share: [/bold]").strip()
        name_ok, name_message = self.check_share_name(self.share_name)
        if not name_ok:
            console.print(f"[red]Error: {name_message}[/red]")
            return False

        default_path = self.default_share_path(self.share_name)
        console.print(f"[bold]2. Enter path (default: {default_path}) or use [D] for directory picker:[/bold]")
        path_input = console.input("[cyan]> [/cyan]").strip()

        if path_input.upper() == 'D':
            selected_dir = self.select_directory()
            self.share_path = selected_dir if selected_dir else default_path
        elif path_input:
            self.share_path = path_input
        else:
            self.share_path = default_path

        path_ok, path_message = self.check_share_path(self.share_path)
        if not path_ok:
            console.print(f"[red]Error: {path_message}[/red]")
            return False

        console.print("\n[bold]3. User Configuration (Enter username, then password. Empty username to finish)[/bold]")
        self.users = []
        while True:
            username = self._pick_or_type_username()
            if not username: break
            username_ok, username_message = self.check_username(username)
            if not username_ok:
                console.print(f"[red]Error: {username_message}[/red]")
                continue
            password = getpass.getpass(f"   Password for {username}: ")
            read_only = console.input(f"   Read-only access for {username}? [y/N] ").strip().lower() in ('y', 'yes')
            self.users.append({'username': username, 'password': password, 'read_only': read_only})

        return True

    def manage_shares(self):
        shares = self.list_shares()
        if not shares:
            print("\nNo existing shares found.")
            return

        print("\n--- Existing Shares ---")
        for i, share in enumerate(shares):
            users = ", ".join(
                u["username"] + (" (read-only)" if u.get("read_only") else "")
                for u in share.get("users", [])
            ) or "(none)"
            print(f"{i}. {share['name']} ({share.get('path', 'Unknown')}) - users: {users}")

        choice = input("\nEnter share number to manage, or press Enter to return: ").strip()
        if not choice.isdigit():
            return
        idx = int(choice)
        if not (0 <= idx < len(shares)):
            print("Invalid index.")
            return
        share = shares[idx]

        print(f"\n--- {share['name']} ---")
        print("1. Add user")
        print("2. Delete share")
        print("3. Back")
        sub = input("Select an option: ").strip()

        if sub == '1':
            username = self._pick_or_type_username()
            if not username:
                console.print("[red]Username cannot be empty.[/red]")
                return
            password = getpass.getpass(f"   Password for {username}: ")
            read_only = input(f"   Read-only access for {username}? [y/N] ").strip().lower() in ('y', 'yes')
            if self.grant_share_access(share['name'], username, password, read_only):
                print(f"Added '{username}' to share '{share['name']}'.")
                self._offer_qr_code(share['name'], username, password)
            else:
                print("Failed to add user (or elevation was cancelled).")
        elif sub == '2':
            confirm = input(f"Delete share '{share['name']}'? [y/N] ").strip().lower()
            if confirm not in ('y', 'yes'):
                return
            delete_folder = False
            if share.get('path'):
                also_delete = input(
                    f"Also permanently delete the folder and everything in it - "
                    f"'{share['path']}'? This cannot be undone. [y/N] "
                ).strip().lower()
                delete_folder = also_delete in ('y', 'yes')
            if self.remove_share(share['name'], delete_folder):
                print(f"Removed share: {share['name']}" + (" (folder deleted too)" if delete_folder else ""))
            else:
                print("Failed to remove share (or elevation was cancelled).")

    def _manage_users_screen(self):
        # Two-step picker throughout (pick the user by number, then a
        # numbered menu of just that user's actions) rather than the old
        # letter+concatenated-number shorthand ("q0", "a2", ...) - that
        # convention reads as unclear (users tried typing "<n>" literally),
        # and this instead matches how manage_shares() already works.
        while True:
            users = self.list_users()
            access_lookup = self.build_access_lookup(self.list_shares())

            console.print("\n[bold]--- Users ---[/bold]")
            if not users:
                print("No users found.")
            for i, u in enumerate(users):
                print(f"{i}. {u['username']}")
                for s in u["shares"]:
                    suffix = " (read-only)" if access_lookup.get((s, u["username"])) else ""
                    print(f"\tshare: {s}{suffix}")

            choice = input(
                "\nEnter a user number to manage, 'n' for a new user, or press Enter to return: "
            ).strip().lower()
            if not choice:
                return

            if choice == 'n':
                # A standalone account - lets a user get set up ahead of
                # deciding what to grant them access to, instead of forcing
                # a throwaway share into existence just to create it.
                username = console.input("   Username: ").strip()
                if not username:
                    console.print("[red]Username cannot be empty.[/red]")
                    continue
                password = getpass.getpass(f"   Password for {username}: ")
                if not password:
                    console.print("[red]Password cannot be empty.[/red]")
                    continue
                if self.add_user(username, password):
                    print(f"Created user '{username}'.")
                else:
                    print("Failed to create user (or elevation was cancelled).")
                continue

            if not choice.isdigit() or not (0 <= int(choice) < len(users)):
                print("Invalid option.")
                continue
            user = users[int(choice)]

            sub_options = []
            if user["shares"]:
                sub_options.append("Revoke share access")
                sub_options.append("Change Access Level")
                sub_options.append("Reset Password & Show QR")
            sub_options.append("Delete user")
            sub_options.append("Back")

            print(f"\n--- {user['username']} ---")
            for si, label in enumerate(sub_options, start=1):
                print(f"{si}. {label}")
            sub = input("Select an option: ").strip()
            if not sub.isdigit() or not (1 <= int(sub) <= len(sub_options)):
                print("Invalid option.")
                continue
            action = sub_options[int(sub) - 1]

            if action == "Back":
                continue
            elif action == "Revoke share access":
                print("Shares:")
                for si, sname in enumerate(user["shares"]):
                    print(f"  {si}. {sname}")
                sidx = input("Which share number to revoke access to? ").strip()
                if not sidx.isdigit() or not (0 <= int(sidx) < len(user["shares"])):
                    print("Invalid share number.")
                    continue
                share_name = user["shares"][int(sidx)]
                confirm = input(f"Revoke '{user['username']}''s access to '{share_name}'? [y/N] ").strip().lower()
                if confirm not in ('y', 'yes'):
                    continue
                if self.revoke_share_access(share_name, user["username"]):
                    print(f"Revoked '{user['username']}''s access to '{share_name}'.")
                else:
                    print("Failed to revoke access (or elevation was cancelled).")
            elif action == "Change Access Level":
                if len(user["shares"]) == 1:
                    share_name = user["shares"][0]
                else:
                    print("Shares:")
                    for si, sname in enumerate(user["shares"]):
                        print(f"  {si}. {sname}")
                    sidx = input("Change access level for which share number? ").strip()
                    if not sidx.isdigit() or not (0 <= int(sidx) < len(user["shares"])):
                        print("Invalid share number.")
                        continue
                    share_name = user["shares"][int(sidx)]
                share = next((s for s in self.list_shares() if s["name"] == share_name), None)
                share_user = next((u for u in (share or {}).get("users", []) if u["username"] == user['username']), None)
                current_read_only = share_user.get("read_only", False) if share_user else False
                current_label = "read-only" if current_read_only else "read-write"
                new_label = "read-write" if current_read_only else "read-only"
                confirm = input(
                    f"'{user['username']}' is currently {current_label} on '{share_name}'. "
                    f"Change to {new_label}? [y/N] "
                ).strip().lower()
                if confirm not in ('y', 'yes'):
                    continue
                if self.change_share_access(share_name, user['username'], not current_read_only):
                    print(f"'{user['username']}' is now {new_label} on '{share_name}'.")
                else:
                    print("Failed to change access level (or elevation was cancelled).")
            elif action == "Reset Password & Show QR":
                # Same underlying operation "Add user" performs when the
                # user already exists - grant_share_access resets the
                # password rather than failing.
                console.print(f"\n[yellow]{QR_PASSWORD_RESET_NOTE}[/yellow]\n")
                if len(user["shares"]) == 1:
                    share_name = user["shares"][0]
                else:
                    print("Shares:")
                    for si, sname in enumerate(user["shares"]):
                        print(f"  {si}. {sname}")
                    sidx = input("Reset password & show QR code for which share number? ").strip()
                    if not sidx.isdigit() or not (0 <= int(sidx) < len(user["shares"])):
                        print("Invalid share number.")
                        continue
                    share_name = user["shares"][int(sidx)]
                password = getpass.getpass(
                    f"   New password for {user['username']} (replaces their current one): "
                )
                if not password:
                    console.print("[red]Password cannot be empty.[/red]")
                    continue
                # Preserve the user's current access level on this share -
                # a password reset shouldn't silently flip them back to
                # read-write.
                share = next((s for s in self.list_shares() if s["name"] == share_name), None)
                share_user = next((u for u in (share or {}).get("users", []) if u["username"] == user['username']), None)
                read_only = share_user.get("read_only", False) if share_user else False
                if self.grant_share_access(share_name, user['username'], password, read_only):
                    print(f"Reset '{user['username']}''s password on share '{share_name}'.")
                    self._offer_qr_code(share_name, user['username'], password)
                else:
                    print("Failed to reset password (or elevation was cancelled).")
            else:
                confirm = input(f"Delete user '{user['username']}' entirely? [y/N] ").strip().lower()
                if confirm not in ('y', 'yes'):
                    continue
                if self.remove_user(user["username"]):
                    print(f"Deleted user '{user['username']}'.")
                else:
                    print("Failed to delete user (or elevation was cancelled).")

    def start(self):
        while True:
            options = ["Create New Share", "Manage Existing Shares", "Manage Users"]
            if self.gui_available():
                options.append("Launch Desktop UI")
            options.append("Exit")

            print("\n=== NASsie Menu ===")
            for i, opt in enumerate(options, start=1):
                print(f"{i}. {opt}")
            choice = input("Select an option: ").strip()

            try:
                selected = options[int(choice) - 1]
            except (ValueError, IndexError):
                print("Invalid option.")
                continue

            if selected == "Create New Share":
                if self.collect_user_input():
                    if self.has_admin_privileges():
                        self.dispatch_execution()
                    else:
                        self.elevate_and_apply({
                            "name": self.share_name,
                            "path": self.share_path,
                            "users": self.users
                        })
                    # Checked against live share state, not a return value -
                    # dispatch_execution()/elevate_and_apply() don't report
                    # success directly, and this works the same regardless
                    # of which path ran.
                    if any(s['name'] == self.share_name for s in self.list_shares()):
                        for user in self.users:
                            self._offer_qr_code(self.share_name, user['username'], user['password'])
            elif selected == "Manage Existing Shares":
                self.manage_shares()
            elif selected == "Manage Users":
                self._manage_users_screen()
            elif selected == "Launch Desktop UI":
                try:
                    from gui import GUIWizard
                    GUIWizard().run()
                except Exception as e:
                    print(f"Could not launch the desktop UI: {e}")
            elif selected == "Exit":
                print("Goodbye!")
                break
