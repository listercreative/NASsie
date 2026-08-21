import getpass

from rich.console import Console
from rich.panel import Panel

from core import SMBWizard, QR_PASSWORD_RESET_NOTE

console = Console()


class CLIWizard(SMBWizard):
    def _pick_or_type_username(self, prompt="   Username: "):
        # Existing usernames one selection away (no risk of a typo against a
        # name that already exists), but typing a new one still works -
        # this can create a brand-new user, unlike group membership which
        # requires an existing one.
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
        # creation / add user) - Kelpie never persists plaintext passwords,
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
        if not self.share_name:
            console.print("[red]Error: Name cannot be empty.[/red]")
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

        console.print("\n[bold]3. User Configuration (Enter username, then password. Empty username to finish)[/bold]")
        self.users = []
        while True:
            username = self._pick_or_type_username()
            if not username: break
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

    def manage_users_and_groups(self):
        while True:
            print("\n=== Users & Groups ===")
            print("1. Users")
            print("2. Groups")
            print("3. Back")
            choice = input("Select an option: ").strip()
            if choice == '1':
                self._manage_users_screen()
            elif choice == '2':
                self._manage_groups_screen()
            elif choice == '3':
                return
            else:
                print("Invalid option.")

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
                for g in u["groups"]:
                    print(f"\tgroup: {g}")
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

            sub_options = ["Assign to group"]
            if user["groups"]:
                sub_options.append("Remove from group")
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
            elif action == "Assign to group":
                groups = self.list_groups()
                if not groups:
                    print("No groups exist to assign to.")
                    continue
                print("Groups:")
                for gi, g in enumerate(groups):
                    print(f"  {gi}. {g['name']}")
                gidx = input("Which group number? ").strip()
                if not gidx.isdigit() or not (0 <= int(gidx) < len(groups)):
                    print("Invalid group number.")
                    continue
                group_name = groups[int(gidx)]['name']
                if self.assign_user_to_group(user['username'], group_name):
                    print(f"Added '{user['username']}' to group '{group_name}'.")
                else:
                    print("Failed to assign group (or elevation was cancelled).")
            elif action == "Remove from group":
                print("Groups:")
                for gi, gname in enumerate(user["groups"]):
                    print(f"  {gi}. {gname}")
                gidx = input("Which group number to remove from? ").strip()
                if not gidx.isdigit() or not (0 <= int(gidx) < len(user["groups"])):
                    print("Invalid group number.")
                    continue
                group_name = user["groups"][int(gidx)]
                if self.revoke_group_membership(user['username'], group_name):
                    print(f"Removed '{user['username']}' from group '{group_name}'.")
                else:
                    print("Failed to remove from group (or elevation was cancelled).")
            elif action == "Revoke share access":
                print("Shares:")
                for si, sname in enumerate(user["shares"]):
                    print(f"  {si}. {sname}")
                sidx = input("Which share number to revoke access to? ").strip()
                if not sidx.isdigit() or not (0 <= int(sidx) < len(user["shares"])):
                    print("Invalid share number.")
                    continue
                share_name = user["shares"][int(sidx)]
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

    def _manage_groups_screen(self):
        # Same two-step picker as _manage_users_screen: pick the group by
        # number, then a numbered menu of just that group's actions. Groups
        # shown here are only the admin-created access-control kind (New
        # Group) - a share's own auto-created ownership group is filesystem-
        # permission-only and never shown, see core.py's _MANAGED_GROUP_PREFIX.
        while True:
            groups = self.list_groups()

            console.print("\n[bold]--- Groups ---[/bold]")
            if not groups:
                print("No groups yet.")
            for i, g in enumerate(groups):
                print(f"{i}. {g['name']}")
                for m in g["members"]:
                    print(f"\tuser: {m}")
                for s in g["shares"]:
                    print(f"\tshare: {s}")

            choice = input(
                "\nEnter a group number to manage, 'n' for a new group, or press Enter to return: "
            ).strip().lower()
            if not choice:
                return
            if choice == 'n':
                name = input("New group name: ").strip()
                if not name:
                    continue
                system_name = self.add_group(name)
                if system_name:
                    print(f"Created group '{system_name}'.")
                else:
                    print("Failed to create group (or elevation was cancelled).")
                continue
            if not choice.isdigit() or not (0 <= int(choice) < len(groups)):
                print("Invalid option.")
                continue
            group = groups[int(choice)]

            sub_options = ["Add member"]
            if group["members"]:
                sub_options.append("Remove member")
            if group["members"] and group["shares"]:
                sub_options.append("Set Access Level")
            sub_options.append("Assign to share")
            if group["shares"]:
                sub_options.append("Remove from share")
            sub_options.append("Delete group")
            sub_options.append("Back")

            print(f"\n--- {group['name']} ---")
            for si, label in enumerate(sub_options, start=1):
                print(f"{si}. {label}")
            sub = input("Select an option: ").strip()
            if not sub.isdigit() or not (1 <= int(sub) <= len(sub_options)):
                print("Invalid option.")
                continue
            action = sub_options[int(sub) - 1]

            if action == "Back":
                continue
            elif action == "Add member":
                # A pick-list of existing users, not free text: adding a
                # member requires an already-existing account (unlike a
                # share's "Add user", which can create one) - the underlying
                # action validates and refuses otherwise, so free text here
                # could only ever fail.
                users = self.list_users()
                if not users:
                    print("No existing users to add. Create one via a share's 'Add user' first.")
                    continue
                print("Users:")
                for ui, u in enumerate(users):
                    print(f"  {ui}. {u['username']}")
                uidx = input("Which user number to add? ").strip()
                if not uidx.isdigit() or not (0 <= int(uidx) < len(users)):
                    print("Invalid user number.")
                    continue
                username = users[int(uidx)]['username']
                if self.assign_user_to_group(username, group['name']):
                    print(f"Added '{username}' to group '{group['name']}'.")
                else:
                    print("Failed to add member (or elevation was cancelled).")
            elif action == "Remove member":
                print("Members:")
                for mi, mname in enumerate(group["members"]):
                    print(f"  {mi}. {mname}")
                midx = input("Which member number to remove? ").strip()
                if not midx.isdigit() or not (0 <= int(midx) < len(group["members"])):
                    print("Invalid member number.")
                    continue
                member_name = group["members"][int(midx)]
                if self.revoke_group_membership(member_name, group['name']):
                    print(f"Removed '{member_name}' from group '{group['name']}'.")
                else:
                    print("Failed to remove member (or elevation was cancelled).")
            elif action == "Set Access Level":
                # Bulk-applies to every CURRENT member of the group, right
                # now - not a persistent group-level grant. Someone added
                # to the group later doesn't automatically inherit this.
                if len(group["shares"]) == 1:
                    share_name = group["shares"][0]
                else:
                    print("Shares:")
                    for si, sname in enumerate(group["shares"]):
                        print(f"  {si}. {sname}")
                    sidx = input("Set access level on which share? ").strip()
                    if not sidx.isdigit() or not (0 <= int(sidx) < len(group["shares"])):
                        print("Invalid share number.")
                        continue
                    share_name = group["shares"][int(sidx)]
                level = input("Set every current member to (r)ead-write or (o)nly read-only? [r/o] ").strip().lower()
                if level not in ('r', 'o'):
                    print("Invalid option.")
                    continue
                read_only = level == 'o'
                confirm = input(
                    f"Apply {'read-only' if read_only else 'read-write'} access to all "
                    f"{len(group['members'])} current member(s) of '{group['name']}' on '{share_name}'? [y/N] "
                ).strip().lower()
                if confirm not in ('y', 'yes'):
                    continue
                if self.change_group_access(group['name'], share_name, read_only):
                    print(f"Set '{group['name']}''s members to {'read-only' if read_only else 'read-write'} on '{share_name}'.")
                else:
                    print("Failed to change access level (or elevation was cancelled).")
            elif action == "Assign to share":
                # A real, persistent grant (unlike "Set Access Level" above)
                # - any current or future member of the group gets this
                # access, since Windows/Samba/macOS all resolve group
                # membership live at connect time.
                shares = self.list_shares()
                if not shares:
                    print("No shares exist yet.")
                    continue
                print("Shares:")
                for si, s in enumerate(shares):
                    print(f"  {si}. {s['name']}")
                sidx = input("Grant access to which share? ").strip()
                if not sidx.isdigit() or not (0 <= int(sidx) < len(shares)):
                    print("Invalid share number.")
                    continue
                share_name = shares[int(sidx)]["name"]
                level = input("Grant members (r)ead-write or (o)nly read-only? [r/o] ").strip().lower()
                if level not in ('r', 'o'):
                    print("Invalid option.")
                    continue
                read_only = level == 'o'
                if self.grant_group_share_access(group['name'], share_name, read_only):
                    print(f"Assigned '{group['name']}' to '{share_name}' ({'read-only' if read_only else 'read-write'}).")
                else:
                    print("Failed to assign group to share (or elevation was cancelled).")
            elif action == "Remove from share":
                if len(group["shares"]) == 1:
                    share_name = group["shares"][0]
                else:
                    print("Shares:")
                    for si, sname in enumerate(group["shares"]):
                        print(f"  {si}. {sname}")
                    sidx = input("Remove access from which share? ").strip()
                    if not sidx.isdigit() or not (0 <= int(sidx) < len(group["shares"])):
                        print("Invalid share number.")
                        continue
                    share_name = group["shares"][int(sidx)]
                confirm = input(f"Remove '{group['name']}''s access to '{share_name}'? [y/N] ").strip().lower()
                if confirm not in ('y', 'yes'):
                    continue
                if self.revoke_group_share_access(group['name'], share_name):
                    print(f"Removed '{group['name']}''s access to '{share_name}'.")
                else:
                    print("Failed to remove group's access (or elevation was cancelled).")
            else:
                confirm = input(f"Delete group '{group['name']}'? [y/N] ").strip().lower()
                if confirm not in ('y', 'yes'):
                    continue
                if self.remove_group(group["name"]):
                    print(f"Deleted group '{group['name']}'.")
                else:
                    print("Failed to delete group (or elevation was cancelled).")

    def start(self):
        while True:
            options = ["Create New Share", "Manage Existing Shares", "Manage Users & Groups"]
            if self.gui_available():
                options.append("Launch Desktop UI")
            options.append("Exit")

            print("\n=== Kelpie Menu ===")
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
            elif selected == "Manage Users & Groups":
                self.manage_users_and_groups()
            elif selected == "Launch Desktop UI":
                try:
                    from gui import GUIWizard
                    GUIWizard().run()
                except Exception as e:
                    print(f"Could not launch the desktop UI: {e}")
            elif selected == "Exit":
                print("Goodbye!")
                break
