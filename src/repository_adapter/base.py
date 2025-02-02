class RepositoryAdapter:
    def post_review(self, number: int, review_data: dict):
        """Abstract method to post a review on the repository."""
        raise NotImplementedError("Subclasses must implement post_review method")
