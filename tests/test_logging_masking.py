import logging
from io import StringIO

from app.logging_utils import install_log_masking, mask_sensitive_text


def test_mask_sensitive_text_masks_token_email_and_domain():
    text = (
        "token=xoxp-1234567890ABCDEFGHIJ "
        "email=alice@example.com "
        "domain=internal.example.org"
    )

    masked = mask_sensitive_text(text)

    assert masked == (
        "token=xoxp********************* "
        "email=a***e@e*****e.com "
        "domain=i******l.e*****e.org"
    )


def test_logging_formatter_masks_output():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    try:
        install_log_masking()

        logger = logging.getLogger("app.test")
        logger.warning(
            "token=%s email=%s domain=%s",
            "xoxb-ABCD1234SECRET",
            "debug.user@example.com",
            "internal.example.net",
        )

        handler.flush()
        output = stream.getvalue()
    finally:
        root.removeHandler(handler)

    assert "xoxb-ABCD1234SECRET" not in output
    assert "debug.user@example.com" not in output
    assert "internal.example.net" not in output

    assert "xoxb**************" in output
    assert "d********r@e*****e.com" in output
    assert "i******l.e*****e.net" in output
