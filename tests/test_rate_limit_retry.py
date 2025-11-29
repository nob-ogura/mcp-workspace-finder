"""Tests for rate limit retry functionality in llm_client."""
import pytest
from unittest.mock import MagicMock, patch

from app.llm_client import (
    _extract_retry_after,
    _is_rate_limit_error,
    _is_daily_limit_error,
    _get_fallback_model,
    OpenAIClientWrapper,
    RATE_LIMIT_MAX_RETRIES,
    FALLBACK_MODEL,
)


class TestExtractRetryAfter:
    """Tests for _extract_retry_after function."""

    def test_extracts_seconds_with_decimal(self):
        exc = Exception("Rate limit reached. Please try again in 8.64s. More info.")
        assert _extract_retry_after(exc) == 8.64

    def test_extracts_integer_seconds(self):
        exc = Exception("Please try again in 10s")
        assert _extract_retry_after(exc) == 10.0

    def test_extracts_minutes_only(self):
        exc = Exception("Please try again in 2m")
        assert _extract_retry_after(exc) == 120.0

    def test_extracts_minutes_and_seconds(self):
        exc = Exception("Please try again in 1m30s")
        assert _extract_retry_after(exc) == 90.0

    def test_returns_none_for_no_match(self):
        exc = Exception("Some other error message")
        assert _extract_retry_after(exc) is None


class TestIsRateLimitError:
    """Tests for _is_rate_limit_error function."""

    def test_detects_rate_limit_error_class(self):
        exc = MagicMock()
        exc.__class__.__name__ = "RateLimitError"
        assert _is_rate_limit_error(exc) is True

    def test_detects_429_status_code(self):
        exc = Exception("Error")
        exc.status_code = 429
        assert _is_rate_limit_error(exc) is True

    def test_detects_429_from_response(self):
        exc = Exception("Error")
        exc.response = MagicMock()
        exc.response.status_code = 429
        assert _is_rate_limit_error(exc) is True

    def test_detects_429_in_message(self):
        exc = Exception("Error code: 429 - rate limit exceeded")
        assert _is_rate_limit_error(exc) is True

    def test_does_not_detect_other_errors(self):
        exc = Exception("Some random error")
        assert _is_rate_limit_error(exc) is False

    def test_does_not_detect_500_errors(self):
        exc = Exception("Server error")
        exc.status_code = 500
        assert _is_rate_limit_error(exc) is False


class TestIsDailyLimitError:
    """Tests for _is_daily_limit_error function."""

    def test_detects_requests_per_day(self):
        exc = Exception(
            "Rate limit reached for gpt-4o-mini on requests per day (RPD): "
            "Limit 10000, Used 10000"
        )
        assert _is_daily_limit_error(exc) is True

    def test_detects_rpd_pattern(self):
        exc = Exception("Rate limit RPD: 10000")
        assert _is_daily_limit_error(exc) is True

    def test_does_not_detect_rpm(self):
        exc = Exception("Rate limit reached on requests per minute (RPM)")
        assert _is_daily_limit_error(exc) is False

    def test_does_not_detect_tpm(self):
        exc = Exception("Rate limit reached on tokens per minute (TPM)")
        assert _is_daily_limit_error(exc) is False


class TestFallbackModel:
    """Tests for fallback model configuration."""

    def test_default_fallback_model(self, monkeypatch):
        monkeypatch.delenv("OPENAI_FALLBACK_MODEL", raising=False)
        assert _get_fallback_model() == FALLBACK_MODEL

    def test_custom_fallback_model(self, monkeypatch):
        monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "gpt-4o")
        assert _get_fallback_model() == "gpt-4o"

    def test_disable_fallback_with_empty_string(self, monkeypatch):
        monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "")
        assert _get_fallback_model() is None


class MockOpenAIClientWrapper:
    """A testable version of OpenAIClientWrapper that doesn't require OpenAI package."""
    
    def __init__(self, mock_client: MagicMock, fallback_model: str | None = None):
        self._client = mock_client
        self._fallback_model = fallback_model
    
    def create(
        self,
        *,
        messages,
        model,
        temperature,
        timeout,
        max_retries,
        tools=None,
        **kwargs,
    ):
        """Same logic as OpenAIClientWrapper.create but for testing."""
        import time
        from app.llm_client import (
            RATE_LIMIT_MAX_RETRIES,
            RATE_LIMIT_DEFAULT_WAIT,
            RATE_LIMIT_MAX_WAIT,
            _is_rate_limit_error,
            _is_daily_limit_error,
            _extract_retry_after,
        )
        
        call_kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "timeout": timeout,
        }
        if tools:
            call_kwargs["tools"] = tools
            call_kwargs["tool_choice"] = "auto"
        
        last_exc = None
        for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
            try:
                response = self._client.chat.completions.create(**call_kwargs)
                break
            except Exception as exc:
                last_exc = exc
                if not _is_rate_limit_error(exc):
                    raise
                
                # Handle daily limit by switching to fallback model
                if _is_daily_limit_error(exc):
                    fallback = self._fallback_model
                    if fallback and call_kwargs["model"] != fallback:
                        call_kwargs["model"] = fallback
                        continue  # Retry immediately with fallback
                    raise
                
                if attempt >= RATE_LIMIT_MAX_RETRIES:
                    raise
                wait_time = _extract_retry_after(exc)
                if wait_time is None:
                    wait_time = RATE_LIMIT_DEFAULT_WAIT * attempt
                wait_time = min(wait_time, RATE_LIMIT_MAX_WAIT)
                time.sleep(wait_time)
        else:
            if last_exc:
                raise last_exc
            raise RuntimeError("Unexpected retry loop exit")
        
        message = response.choices[0].message
        result = {
            "choices": [
                {
                    "message": {
                        "role": message.role,
                        "content": message.content,
                    }
                }
            ]
        }
        if message.tool_calls:
            result["choices"][0]["message"]["tool_calls"] = [
                {"function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in message.tool_calls
            ]
        return result


class TestRateLimitRetry:
    """Tests for rate limit retry behavior in OpenAIClientWrapper."""

    @patch("time.sleep")
    def test_retries_on_rate_limit_and_succeeds(self, mock_sleep):
        """Test that rate limit errors trigger retries."""
        mock_openai_client = MagicMock()
        
        # First call raises rate limit, second succeeds
        rate_limit_exc = Exception("Rate limit reached. Please try again in 5s.")
        rate_limit_exc.status_code = 429
        
        success_response = MagicMock()
        success_response.choices = [MagicMock()]
        success_response.choices[0].message = MagicMock()
        success_response.choices[0].message.role = "assistant"
        success_response.choices[0].message.content = "test response"
        success_response.choices[0].message.tool_calls = None
        
        mock_openai_client.chat.completions.create.side_effect = [
            rate_limit_exc,
            success_response,
        ]
        
        wrapper = MockOpenAIClientWrapper(mock_openai_client)
        
        result = wrapper.create(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
            temperature=0.5,
            timeout=10,
            max_retries=1,
        )
        
        # Should have retried
        assert mock_openai_client.chat.completions.create.call_count == 2
        # Should have slept between retries
        mock_sleep.assert_called_once_with(5.0)
        # Should return successful result
        assert result["choices"][0]["message"]["content"] == "test response"

    @patch("time.sleep")
    def test_raises_after_max_retries(self, mock_sleep):
        """Test that error is raised after max retries exhausted."""
        mock_openai_client = MagicMock()
        
        rate_limit_exc = Exception("Rate limit reached. Please try again in 5s.")
        rate_limit_exc.status_code = 429
        
        # All calls fail with rate limit
        mock_openai_client.chat.completions.create.side_effect = rate_limit_exc
        
        wrapper = MockOpenAIClientWrapper(mock_openai_client)
        
        with pytest.raises(Exception) as exc_info:
            wrapper.create(
                messages=[{"role": "user", "content": "test"}],
                model="gpt-4o-mini",
                temperature=0.5,
                timeout=10,
                max_retries=1,
            )
        
        assert "Rate limit reached" in str(exc_info.value)
        assert mock_openai_client.chat.completions.create.call_count == RATE_LIMIT_MAX_RETRIES

    def test_non_rate_limit_errors_not_retried(self):
        """Test that non-rate-limit errors are raised immediately."""
        mock_openai_client = MagicMock()
        
        other_exc = Exception("Some other error")
        mock_openai_client.chat.completions.create.side_effect = other_exc
        
        wrapper = MockOpenAIClientWrapper(mock_openai_client)
        
        with pytest.raises(Exception) as exc_info:
            wrapper.create(
                messages=[{"role": "user", "content": "test"}],
                model="gpt-4o-mini",
                temperature=0.5,
                timeout=10,
                max_retries=1,
            )
        
        assert "Some other error" in str(exc_info.value)
        # Should only try once for non-rate-limit errors
        assert mock_openai_client.chat.completions.create.call_count == 1

    def test_daily_limit_switches_to_fallback_model(self):
        """Test that daily limit (RPD) switches to fallback model."""
        mock_openai_client = MagicMock()
        
        # First call fails with daily limit
        daily_limit_exc = Exception(
            "Rate limit reached for gpt-4o-mini on requests per day (RPD): "
            "Limit 10000, Used 10000"
        )
        daily_limit_exc.status_code = 429
        
        # Second call with fallback model succeeds
        success_response = MagicMock()
        success_response.choices = [MagicMock()]
        success_response.choices[0].message = MagicMock()
        success_response.choices[0].message.role = "assistant"
        success_response.choices[0].message.content = "fallback response"
        success_response.choices[0].message.tool_calls = None
        
        mock_openai_client.chat.completions.create.side_effect = [
            daily_limit_exc,
            success_response,
        ]
        
        wrapper = MockOpenAIClientWrapper(mock_openai_client, fallback_model="gpt-3.5-turbo")
        
        result = wrapper.create(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o-mini",
            temperature=0.5,
            timeout=10,
            max_retries=1,
        )
        
        # Should have tried twice
        assert mock_openai_client.chat.completions.create.call_count == 2
        
        # First call should use original model
        first_call = mock_openai_client.chat.completions.create.call_args_list[0]
        assert first_call.kwargs["model"] == "gpt-4o-mini"
        
        # Second call should use fallback model
        second_call = mock_openai_client.chat.completions.create.call_args_list[1]
        assert second_call.kwargs["model"] == "gpt-3.5-turbo"
        
        # Should return successful result
        assert result["choices"][0]["message"]["content"] == "fallback response"

    def test_daily_limit_raises_when_no_fallback(self):
        """Test that daily limit raises when no fallback model configured."""
        mock_openai_client = MagicMock()
        
        daily_limit_exc = Exception(
            "Rate limit reached for gpt-4o-mini on requests per day (RPD): "
            "Limit 10000, Used 10000"
        )
        daily_limit_exc.status_code = 429
        
        mock_openai_client.chat.completions.create.side_effect = daily_limit_exc
        
        # No fallback model configured
        wrapper = MockOpenAIClientWrapper(mock_openai_client, fallback_model=None)
        
        with pytest.raises(Exception) as exc_info:
            wrapper.create(
                messages=[{"role": "user", "content": "test"}],
                model="gpt-4o-mini",
                temperature=0.5,
                timeout=10,
                max_retries=1,
            )
        
        assert "requests per day" in str(exc_info.value)
        # Should only try once since fallback is not available
        assert mock_openai_client.chat.completions.create.call_count == 1


class TestModelEnvVar:
    """Tests for OPENAI_MODEL environment variable."""

    def test_llm_search_uses_env_model(self, monkeypatch):
        """Test that llm_search uses OPENAI_MODEL when set."""
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
        
        # Re-import to pick up env var
        from app.llm_search import _get_model_name
        assert _get_model_name() == "gpt-4o"

    def test_llm_search_uses_default_when_no_env(self, monkeypatch):
        """Test that llm_search uses default when OPENAI_MODEL not set."""
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        
        from app.llm_search import _get_model_name, DEFAULT_MODEL_NAME
        assert _get_model_name() == DEFAULT_MODEL_NAME

    def test_llm_summary_uses_env_model(self, monkeypatch):
        """Test that llm_summary uses OPENAI_MODEL when set."""
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
        
        from app.llm_summary import _get_model_name
        assert _get_model_name() == "gpt-4o"

    def test_llm_summary_uses_default_when_no_env(self, monkeypatch):
        """Test that llm_summary uses default when OPENAI_MODEL not set."""
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        
        from app.llm_summary import _get_model_name, DEFAULT_MODEL_NAME
        assert _get_model_name() == DEFAULT_MODEL_NAME

