from unittest.mock import patch, MagicMock

from src.utils.review_record_service import get_last_reviewed_sha, save_review_record


class TestGetLastReviewedSha:
    @patch("src.utils.review_record_service.STATELESS_MODE", True)
    def test_returns_none_in_stateless_mode(self):
        result = get_last_reviewed_sha("owner/repo", 1)
        assert result is None

    @patch("src.utils.review_record_service.STATELESS_MODE", False)
    @patch("src.utils.review_record_service.get_session")
    def test_returns_sha_when_record_exists(self, mock_get_session):
        mock_record = MagicMock()
        mock_record.reviewed_head_sha = "abc123"

        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = mock_record
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_get_session.return_value = iter([mock_session])

        result = get_last_reviewed_sha("owner/repo", 1)
        assert result == "abc123"

    @patch("src.utils.review_record_service.STATELESS_MODE", False)
    @patch("src.utils.review_record_service.get_session")
    def test_returns_none_when_no_record(self, mock_get_session):
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_get_session.return_value = iter([mock_session])

        result = get_last_reviewed_sha("owner/repo", 1)
        assert result is None

    @patch("src.utils.review_record_service.STATELESS_MODE", False)
    @patch("src.utils.review_record_service.get_session")
    def test_returns_none_on_exception(self, mock_get_session):
        mock_get_session.side_effect = RuntimeError("DB down")

        result = get_last_reviewed_sha("owner/repo", 1)
        assert result is None


class TestSaveReviewRecord:
    @patch("src.utils.review_record_service.STATELESS_MODE", True)
    @patch("src.utils.review_record_service.ReviewRecord")
    def test_noop_in_stateless_mode(self, mock_record_cls):
        save_review_record("owner/repo", 1, "abc", "def")
        mock_record_cls.assert_not_called()

    @patch("src.utils.review_record_service.STATELESS_MODE", False)
    @patch("src.utils.review_record_service.ReviewRecord")
    def test_creates_and_saves_record(self, mock_record_cls):
        mock_instance = MagicMock()
        mock_record_cls.return_value = mock_instance

        save_review_record("owner/repo", 42, "head123", "base456")

        mock_record_cls.assert_called_once_with(
            repository_full_name="owner/repo",
            pr_number=42,
            reviewed_head_sha="head123",
            reviewed_base_sha="base456",
            status="completed",
        )
        mock_instance.save.assert_called_once()
