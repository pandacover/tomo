from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from tomo.speech import SPEECH_PRIVACY_MESSAGE, WindowsSpeechInput, format_speech_error, recognize_once, recognition_result_text, stop_recognition


class FakeAsyncOperation:
    def __init__(self, result: object) -> None:
        self.result = result

    def __await__(self):
        async def _result():
            return self.result

        return _result().__await__()


class FakeRecognizer:
    def __init__(self, text: str = "open notes") -> None:
        self.text = text
        self.stopped = False
        self.closed = False

    def compile_constraints_async(self) -> FakeAsyncOperation:
        return FakeAsyncOperation(SimpleNamespace(status=0))

    def recognize_async(self) -> FakeAsyncOperation:
        return FakeAsyncOperation(SimpleNamespace(text=self.text))

    def stop_recognition_async(self) -> FakeAsyncOperation:
        self.stopped = True
        return FakeAsyncOperation(None)

    def close(self) -> None:
        self.closed = True


def test_recognize_once_returns_text():
    recognizer = FakeRecognizer("hello world")

    assert asyncio.run(recognize_once(recognizer)) == "hello world"


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


def wait_until(predicate, timeout: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")
