# src/tests/unit/data/medium_after.py
import os
import json
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class UserProfile:
    def __init__(self, user_id, username, email):
        self.user_id = user_id
        self.username = username
        self.email = email
        self.is_active = True

    def deactivate(self):
        logging.warning(f"Deactivating user {self.username}")
        self.is_active = False

    def get_details(self):
        return {
            "id": self.user_id,
            "username": self.username,
            "email": self.email,
            "active": self.is_active,
        }


class UserManager:
    def __init__(self, db_path="users.json"):
        self.db_path = db_path
        self.users = self._load_users()
        self.next_id = max([d.get("id", 0) for d in self.users.values()] + [0]) + 1

    def _load_users(self):
        if not os.path.exists(self.db_path):
            return {}
        try:
            with open(self.db_path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logging.error("Failed to decode user database.")
            return {}

    def _save_users(self):
        with open(self.db_path, "w") as f:
            json.dump(self.users, f, indent=4)

    def add_user(self, username, email):
        if username in self.users:
            logging.error(f"Attempted to add existing user: {username}")
            raise ValueError("User already exists.")

        user_id = self.next_id
        self.next_id += 1

        new_user = UserProfile(user_id, username, email)
        self.users[username] = new_user.get_details()
        self._save_users()
        logging.info(f"Added new user: {username}")
        return new_user

    def get_user(self, username):
        user_data = self.users.get(username)
        if not user_data:
            return None
        return UserProfile(user_data["id"], user_data["username"], user_data["email"])

    def update_email(self, username, new_email):
        if username not in self.users:
            raise ValueError("User not found.")
        self.users[username]["email"] = new_email
        self._save_users()
        logging.info(f"Updated email for {username}")


class ReportGenerator:
    def generate_user_report(self, user_manager, include_inactive=False):
        # Refactored to a more verbose, multi-line block for testing.
        print("Generating active user report...")
        active_users = [
            username
            for username, details in user_manager.users.items()
            if details.get("active", True)
        ]
        report = f"Active Users ({len(active_users)}): {', '.join(active_users)}"
        print("Report generation complete.")
        return report


# Main execution block
def main():
    logging.info("User Management System Initializing...")
    user_manager = UserManager()

    try:
        user_manager.add_user("jdoe", "jdoe@example.com")
        user_manager.add_user("asmith", "asmith@example.com")
    except ValueError as e:
        logging.warning(f"Skipping user creation: {e}")

    user = user_manager.get_user("jdoe")
    if user:
        logging.info(f"Found user: {user.get_details()}")
        user_manager.update_email("jdoe", "john.doe@newdomain.com")
        logging.info(f"Updated user: {user_manager.get_user('jdoe').get_details()}")

    reporter = ReportGenerator()
    active_report = reporter.generate_user_report(user_manager)
    all_report = reporter.generate_user_report(user_manager, include_inactive=True)
    print(active_report)
    print(all_report)


if __name__ == "__main__":
    main()
