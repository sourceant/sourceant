import os
import json


class ConfigManager:
    """Manages configuration settings for the application."""

    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.settings = {}

    def load_config(self):
        """Loads configuration from a JSON file."""
        if os.path.exists(self.config_path):
            with open(self.config_path, "r") as f:
                self.settings = json.load(f)
        return self.settings

    def get_setting(self, key, default=None):
        """Retrieves a specific setting by key."""
        return self.settings.get(key, default)


def initialize_system():
    """Initializes the main system components."""
    print("System initializing...")
    # In a real application, this would do more.
    return True


class DataProcessor:
    """A class to process data from a source."""

    def __init__(self, source_url):
        self.source_url = source_url
        self.data = []

    def fetch_data(self):
        """Fetches data from the source URL."""
        print(f"Fetching data from {self.source_url}")
        # Placeholder for data fetching logic
        self.data = [{"id": 1, "value": "alpha"}, {"id": 2, "value": "beta"}]
        return self.data

    def process(self):
        """Processes the fetched data."""
        if not self.data:
            self.fetch_data()

        processed_data = []
        for item in self.data:
            new_item = item.copy()
            new_item["processed"] = True
            processed_data.append(new_item)
        return processed_data


# Main execution block
if __name__ == "__main__":
    print("Application starting.")

    config_manager = ConfigManager()
    settings = config_manager.load_config()

    if initialize_system():
        processor = DataProcessor(source_url=settings.get("DATA_SOURCE"))
        results = processor.process()
        print("Processing complete.")
        print(results)
    else:
        print("System initialization failed.")

    print("Application finished.")
