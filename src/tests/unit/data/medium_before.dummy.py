# src/tests/unit/data/medium_before.py
import os
import json


class UserProfile:
    def __init__(self, username, email):
        self.username = username
        self.email = email
        self.is_active = True

    def deactivate(self):
        self.is_active = False

    def get_details(self):
        return {
            "username": self.username,
            "email": self.email,
            "active": self.is_active,
        }


class UserManager:
    def __init__(self, db_path="users.json"):
        self.db_path = db_path
        self.users = self._load_users()

    def _load_users(self):
        if not os.path.exists(self.db_path):
            return {}
        with open(self.db_path, "r") as f:
            return json.load(f)

    def _save_users(self):
        with open(self.db_path, "w") as f:
            json.dump(self.users, f, indent=4)

    def add_user(self, username, email):
        if username in self.users:
            raise ValueError("User already exists.")
        new_user = UserProfile(username, email)
        self.users[username] = new_user.get_details()
        self._save_users()
        return new_user

    def get_user(self, username):
        user_data = self.users.get(username)
        if not user_data:
            return None
        return UserProfile(user_data["username"], user_data["email"])

    def update_email(self, username, new_email):
        if username not in self.users:
            raise ValueError("User not found.")
        self.users[username]["email"] = new_email
        self._save_users()


class ReportGenerator:
    def generate_active_user_report(self, user_manager):
        active_users = []
        for username, details in user_manager.users.items():
            if details.get("active", True):
                active_users.append(username)
        return "Active Users: " + ", ".join(active_users)


# Main execution block
def main():
    print("User Management System Initializing...")
    user_manager = UserManager()

    try:
        user_manager.add_user("jdoe", "jdoe@example.com")
        user_manager.add_user("asmith", "asmith@example.com")
    except ValueError as e:
        print(f"Skipping user creation: {e}")

    user = user_manager.get_user("jdoe")
    if user:
        print(f"Found user: {user.get_details()}")
        user_manager.update_email("jdoe", "john.doe@newdomain.com")
        print(f"Updated user: {user_manager.get_user('jdoe').get_details()}")

    reporter = ReportGenerator()
    report = reporter.generate_active_user_report(user_manager)
    print(report)


if __name__ == "__main__":
    main()
