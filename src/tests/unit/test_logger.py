from unittest.mock import patch

import pytest

# Import the functions/classes to be tested
from src.config import settings
from src.utils.logger import logger, setup_logger


@patch("src.config.settings.LOG_DRIVER", "console")
def test_console_driver(capsys):
    """Test that the console driver routes logs to stdout and stderr correctly."""
    setup_logger()

    logger.debug("This is a debug message")
    logger.info("This is an info message")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    logger.critical("This is a critical message")

    captured = capsys.readouterr()
    assert "This is a debug message" in captured.out
    assert "This is an info message" in captured.out
    assert "This is a warning message" in captured.err
    assert "This is an error message" in captured.err
    assert "This is a critical message" in captured.err


@patch("src.config.settings.LOG_DRIVER", "file")
@patch("src.utils.logger.RotatingFileHandler")
def test_file_driver(mock_rotating_file_handler):
    """Test that the file driver uses RotatingFileHandler."""
    mock_instance = mock_rotating_file_handler.return_value

    setup_logger()

    mock_rotating_file_handler.assert_called_once_with(
        settings.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    mock_instance.setFormatter.assert_called_once()
    assert mock_instance in logger.handlers


@patch("src.config.settings.LOG_DRIVER", "syslog")
@patch("src.utils.logger.SysLogHandler")
def test_syslog_driver(mock_syslog_handler):
    """Test that the syslog driver uses SysLogHandler."""
    mock_instance = mock_syslog_handler.return_value

    setup_logger()

    mock_syslog_handler.assert_called_once_with()
    mock_instance.setFormatter.assert_called_once()
    assert mock_instance in logger.handlers


@patch("src.config.settings.LOG_DRIVER", "invalid_driver")
def test_invalid_driver():
    """Test that an invalid LOG_DRIVER raises a ValueError."""
    with pytest.raises(ValueError) as excinfo:
        setup_logger()

    assert "Invalid LOG_DRIVER: invalid_driver" in str(excinfo.value)
