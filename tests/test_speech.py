from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import tomo.speech as speech
from tomo.speech import SPEECH_PRIVACY_MESSAGE, WindowsSpeechInput, format_speech_error, recognize_continuous, recognize_once, recognition_result_text, stop_recognition


class FakeAsyncOperation:
    def __init__(self, result: object) -> None:
        self.result = result

    def __await__(self):
        async def _result():
            return self.result

        return _result().__await__()


class FakeRecognizer:
    def __init__(self, text: str = "open notes", *, status: int = 0) -> None:
        self.text = text
        self.status = status
        self.stopped = False
        self.closed = False

    def compile_constraints_async(self) -> FakeAsyncOperation:
        return FakeAsyncOperation(SimpleNamespace(status=0))

    def recognize_async(self) -> FakeAsyncOperation:
        return FakeAsyncOperation(SimpleNamespace(text=self.text, status=self.status))

    def stop_recognition_async(self) -> FakeAsyncOperation:
        self.stopped = True
        return FakeAsyncOperation(None)

    def close(self) -> None:
        self.closed = True


def test_recognize_once_returns_text():
    recognizer = FakeRecognizer("hello world")

    outcome = asyncio.run(recognize_once(recognizer))

    assert outcome.text == "hello world"
    assert outcome.retry is False


def test_stop_recognition_calls_winrt_stop():
    recognizer = FakeRecognizer()

    asyncio.run(stop_recognition(recognizer))

    assert recognizer.stopped is True


def test_windows_speech_input_emits_final_text():
    recognizer = FakeRecognizer("open notes")
    states: list[str] = []
    finals: list[str] = []
    errors: list[str] = []
    speech = WindowsSpeechInput(
        on_state=states.append,
        on_partial=lambda text: None,
        on_final=finals.append,
        on_error=errors.append,
        recognizer_factory=lambda: recognizer,
    )

    assert speech.start() is True
    wait_until(lambda: finals)

    assert finals == ["open notes"]
    assert errors == []
    assert states[0] == "listening"
    assert states[-1] == "idle"
    assert recognizer.closed is True


def test_windows_speech_input_retries_timeout_until_final_text():
    recognizer = SequenceRecognizer(
        [
            SimpleNamespace(text="", status=6),
            SimpleNamespace(text="", status=7),
            SimpleNamespace(text="", status=8),
            SimpleNamespace(text="send this", status=0),
        ]
    )
    finals: list[str] = []
    errors: list[str] = []
    speech = WindowsSpeechInput(
        on_state=lambda state: None,
        on_partial=lambda text: None,
        on_final=finals.append,
        on_error=errors.append,
        recognizer_factory=lambda: recognizer,
    )

    assert speech.start() is True
    wait_until(lambda: finals)

    assert finals == ["send this"]
    assert errors == []
    assert recognizer.calls == 4


def test_recognize_continuous_accumulates_segments_until_completed():
    recognizer = ContinuousRecognizer(["first part", "second part"])

    text = asyncio.run(recognize_continuous(recognizer, lambda generation: True, 1))

    assert text == "first part second part"
    assert recognizer.continuous_recognition_session.started is True
    assert recognizer.continuous_recognition_session.stopped is True
    assert recognizer.continuous_recognition_session.auto_stop_silence_timeout.total_seconds() == 2.0
    assert recognizer.continuous_recognition_session.result_handler_removed is True
    assert recognizer.continuous_recognition_session.completed_handler_removed is True


def test_recognize_continuous_waits_without_initial_timeout_until_cancel():
    current = True
    recognizer = ContinuousRecognizer([], complete_on_start=False)

    async def run_recognition() -> str:
        nonlocal current
        task = asyncio.create_task(recognize_continuous(recognizer, lambda generation: current, 1))
        await asyncio.sleep(0.03)
        assert task.done() is False
        current = False
        return await task

    text = asyncio.run(run_recognition())

    assert text == ""
    assert recognizer.continuous_recognition_session.stopped is True


def test_recognize_continuous_stops_after_silence_without_completed(monkeypatch):
    monkeypatch.setattr(speech, "CONTINUOUS_SILENCE_TIMEOUT_SECONDS", 0.01)
    recognizer = ContinuousRecognizer(["captured text"], complete_on_start=False)

    text = asyncio.run(recognize_continuous(recognizer, lambda generation: True, 1))

    assert text == "captured text"
    assert recognizer.continuous_recognition_session.stopped is True
    assert recognizer.continuous_recognition_session.completed_handler_removed is True


def test_recognize_continuous_ignores_winrt_stop_cleanup_race(monkeypatch):
    monkeypatch.setattr(speech, "CONTINUOUS_SILENCE_TIMEOUT_SECONDS", 0.01)
    recognizer = ContinuousRecognizer(
        ["captured text"],
        complete_on_start=False,
        stop_error=OSError(22, "The text associated with this error code could not be found.", None, -2146233079),
    )

    text = asyncio.run(recognize_continuous(recognizer, lambda generation: True, 1))

    assert text == "captured text"
    assert recognizer.continuous_recognition_session.result_handler_removed is True
    assert recognizer.continuous_recognition_session.completed_handler_removed is True


def test_windows_speech_input_rejects_duplicate_start():
    recognizer = BlockingRecognizer()
    speech = WindowsSpeechInput(
        on_state=lambda state: None,
        on_partial=lambda text: None,
        on_final=lambda text: None,
        on_error=lambda error: None,
        recognizer_factory=lambda: recognizer,
    )

    assert speech.start() is True
    wait_until(lambda: recognizer.started)
    assert speech.start() is False
    speech.cancel()


def test_windows_speech_input_reports_factory_errors():
    errors: list[str] = []
    speech = WindowsSpeechInput(
        on_state=lambda state: None,
        on_partial=lambda text: None,
        on_final=lambda text: None,
        on_error=errors.append,
        recognizer_factory=lambda: (_ for _ in ()).throw(RuntimeError("no recognizer")),
    )

    assert speech.start() is True
    wait_until(lambda: bool(errors))

    assert errors == ["no recognizer"]


def test_recognition_result_text_returns_stripped_text():
    assert recognition_result_text(SimpleNamespace(text="  open notes  ")) == "open notes"


def test_format_speech_error_explains_windows_privacy_setting():
    error = OSError(
        "[WinError -2147199735] The speech privacy policy was not accepted prior to attempting a speech recognition."
    )

    assert format_speech_error(error) == SPEECH_PRIVACY_MESSAGE


class BlockingRecognizer(FakeRecognizer):
    def __init__(self) -> None:
        super().__init__("")
        self.started = False
        self.release = threading.Event()

    def recognize_async(self):
        self.started = True

        class Operation:
            def __await__(inner_self):
                async def _wait():
                    while not self.release.is_set():
                        await asyncio.sleep(0.01)

                return _wait().__await__()

        return Operation()

    def stop_recognition_async(self) -> FakeAsyncOperation:
        self.release.set()
        return super().stop_recognition_async()


class SequenceRecognizer(FakeRecognizer):
    def __init__(self, results: list[object]) -> None:
        super().__init__("")
        self.results = results
        self.calls = 0

    def recognize_async(self) -> FakeAsyncOperation:
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return FakeAsyncOperation(result)


class ContinuousSession:
    def __init__(
        self,
        segments: list[str],
        *,
        complete_on_start: bool = True,
        stop_error: OSError | None = None,
    ) -> None:
        self.segments = segments
        self.complete_on_start = complete_on_start
        self.stop_error = stop_error
        self.result_handler = None
        self.completed_handler = None
        self.started = False
        self.stopped = False
        self.result_handler_removed = False
        self.completed_handler_removed = False
        self.auto_stop_silence_timeout = None

    def add_result_generated(self, handler):
        self.result_handler = handler
        return "result-token"

    def remove_result_generated(self, token) -> None:
        assert token == "result-token"
        self.result_handler_removed = True

    def add_completed(self, handler):
        self.completed_handler = handler
        return "completed-token"

    def remove_completed(self, token) -> None:
        assert token == "completed-token"
        self.completed_handler_removed = True

    def start_async(self) -> FakeAsyncOperation:
        self.started = True
        for segment in self.segments:
            self.result_handler(None, SimpleNamespace(result=SimpleNamespace(text=segment, status=0)))
        if self.complete_on_start:
            self.completed_handler(None, SimpleNamespace())
        return FakeAsyncOperation(None)

    def stop_async(self) -> FakeAsyncOperation:
        self.stopped = True
        if self.stop_error is not None:
            raise self.stop_error
        return FakeAsyncOperation(None)


class ContinuousRecognizer(FakeRecognizer):
    def __init__(
        self,
        segments: list[str],
        *,
        complete_on_start: bool = True,
        stop_error: OSError | None = None,
    ) -> None:
        super().__init__("")
        self.continuous_recognition_session = ContinuousSession(
            segments,
            complete_on_start=complete_on_start,
            stop_error=stop_error,
        )


def wait_until(predicate, timeout: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")
