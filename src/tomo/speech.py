from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import NamedTuple


VoiceStateCallback = Callable[[str], None]
VoiceTextCallback = Callable[[str], None]
VoiceErrorCallback = Callable[[str], None]
SPEECH_PRIVACY_NOT_ACCEPTED = "-2147199735"
SPEECH_PRIVACY_MESSAGE = (
    "Windows speech recognition is disabled by privacy settings. Open Windows Settings > Privacy & security > "
    "Speech and turn on speech recognition/online speech recognition, then restart Tomo."
)
RETRYABLE_EMPTY_RECOGNITION_STATUSES = {
    6,  # Unknown; Windows can return this as an empty one-shot result while audio is still active.
    7,  # TimeoutExceeded
    8,  # PauseLimitExceeded
}
CONTINUOUS_SILENCE_TIMEOUT_SECONDS = 2.0


class WindowsSpeechInput:
    def __init__(
        self,
        *,
        on_state: VoiceStateCallback,
        on_partial: VoiceTextCallback,
        on_final: VoiceTextCallback,
        on_error: VoiceErrorCallback,
        recognizer_factory: Callable[[], object] | None = None,
    ) -> None:
        self.on_state = on_state
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_error = on_error
        self.recognizer_factory = recognizer_factory or create_windows_recognizer
        self._lock = threading.Lock()
        self._listening = False
        self._generation = 0
        self._recognizer: object | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def listening(self) -> bool:
        with self._lock:
            return self._listening

    def start(self) -> bool:
        with self._lock:
            if self._listening:
                return False
            self._listening = True
            self._generation += 1
            generation = self._generation
        threading.Thread(target=self._run_recognition, args=(generation,), daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop_current_recognition()
        self._mark_idle()

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
        self.stop()

    def shutdown(self) -> None:
        self.cancel()

    def _run_recognition(self, generation: int) -> None:
        loop = asyncio.new_event_loop()
        recognizer: object | None = None
        delivered_final = False
        try:
            asyncio.set_event_loop(loop)
            recognizer = self.recognizer_factory()
            with self._lock:
                if generation != self._generation:
                    return
                self._recognizer = recognizer
                self._loop = loop
            self.on_state("listening")
            text = loop.run_until_complete(recognize_continuous(recognizer, self._is_current, generation))
            if text and self._is_current(generation):
                self.on_final(text)
                delivered_final = True
        except Exception as exc:  # noqa: BLE001
            if self._is_current(generation):
                self.on_error(format_speech_error(exc))
        finally:
            dispose_recognizer(recognizer)
            loop.close()
            with self._lock:
                if generation == self._generation:
                    self._recognizer = None
                    self._loop = None
                    self._listening = False
                    if not delivered_final:
                        self.on_state("idle")

    def _stop_current_recognition(self) -> None:
        with self._lock:
            recognizer = self._recognizer
            loop = self._loop
            self._recognizer = None
            self._loop = None
            self._listening = False
        if recognizer is None or loop is None or loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(stop_recognition(recognizer), loop)
        try:
            future.result(timeout=1)
        except Exception:
            future.cancel()

    def _mark_idle(self) -> None:
        self.on_state("idle")

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return self._generation == generation


def create_windows_recognizer() -> object:
    if os.name != "nt":
        raise RuntimeError("Voice input uses Windows native speech recognition and is only available on Windows.")
    from winrt.windows.media.speechrecognition import (
        SpeechRecognitionScenario,
        SpeechRecognitionTopicConstraint,
        SpeechRecognizer,
    )

    recognizer = SpeechRecognizer()
    recognizer.constraints.append(SpeechRecognitionTopicConstraint(SpeechRecognitionScenario.DICTATION, "dictation"))
    return recognizer


class RecognitionOutcome(NamedTuple):
    text: str
    retry: bool = False
    error: str = ""


async def _compile_constraints(recognizer: object) -> None:
    compile_result = await call_async(recognizer.compile_constraints_async())
    status = getattr(compile_result, "status", None)
    if status is not None and int(status) != 0:
        raise RuntimeError(f"Windows speech recognition constraints failed to compile: {status}")


async def recognize_once(recognizer: object) -> RecognitionOutcome:
    await _compile_constraints(recognizer)
    result = await call_async(recognizer.recognize_async())
    return recognition_result_outcome(result)


async def recognize_continuous(
    recognizer: object,
    is_current: Callable[[int], bool],
    generation: int,
) -> str:
    await _compile_constraints(recognizer)

    session = getattr(recognizer, "continuous_recognition_session", None)
    if session is None:
        return await recognize_continuous_fallback(recognizer, is_current, generation)

    session.auto_stop_silence_timeout = timedelta(
        seconds=CONTINUOUS_SILENCE_TIMEOUT_SECONDS
    )
    parts: list[str] = []
    last_result_at: float | None = None
    completed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_result_generated(sender: object, event_args: object) -> None:
        nonlocal last_result_at
        result = getattr(event_args, "result", None)
        text = recognition_result_text(result)
        if text:
            parts.append(text)
            last_result_at = loop.time()

    def handle_completed(sender: object, event_args: object) -> None:
        loop.call_soon_threadsafe(completed.set)

    token = session.add_result_generated(handle_result_generated)
    completed_token = session.add_completed(handle_completed)
    try:
        await call_async(session.start_async())
        while is_current(generation) and not completed.is_set():
            now = loop.time()
            if last_result_at is not None and now - last_result_at >= CONTINUOUS_SILENCE_TIMEOUT_SECONDS:
                break
            await asyncio.sleep(0.05)
    finally:
        try:
            await stop_continuous_session(session)
        finally:
            remove = getattr(session, "remove_result_generated", None)
            if callable(remove):
                remove(token)
            remove_completed = getattr(session, "remove_completed", None)
            if callable(remove_completed):
                remove_completed(completed_token)

    return " ".join(parts).strip()


async def recognize_continuous_fallback(
    recognizer: object,
    is_current: Callable[[int], bool],
    generation: int,
) -> str:
    parts: list[str] = []
    while is_current(generation):
        outcome = await recognize_once(recognizer)
        if outcome.text:
            parts.append(outcome.text)
            break
        if not outcome.retry:
            raise RuntimeError(
                outcome.error or "Windows speech recognition stopped without transcribed text."
            )
    return " ".join(parts).strip()


async def stop_recognition(recognizer: object) -> None:
    session = getattr(recognizer, "continuous_recognition_session", None)
    if session is not None:
        operation = getattr(session, "stop_async", None)
        if callable(operation):
            await stop_continuous_session(session)
            return
    operation = getattr(recognizer, "stop_recognition_async", None)
    if callable(operation):
        await call_async(operation())


async def call_async(operation: Awaitable[object]) -> object:
    return await operation


async def stop_continuous_session(session: object) -> None:
    operation = getattr(session, "stop_async", None)
    if not callable(operation):
        return
    try:
        await call_async(operation())
    except OSError as exc:
        if getattr(exc, "winerror", None) == -2146233079:
            return
        raise


def recognition_result_text(result: object) -> str:
    text = getattr(result, "text", "")
    return str(text).strip()


def recognition_result_outcome(result: object) -> RecognitionOutcome:
    text = recognition_result_text(result)
    if text:
        return RecognitionOutcome(text=text)
    status = getattr(result, "status", None)
    status_code = recognition_status_code(status)
    if status is None or status_code in RETRYABLE_EMPTY_RECOGNITION_STATUSES:
        return RecognitionOutcome(text="", retry=True)
    return RecognitionOutcome(
        text="",
        retry=False,
        error=f"Windows speech recognition ended without text: status {status_code if status_code is not None else status}",
    )


def recognition_status_code(status: object) -> int | None:
    try:
        return int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def format_speech_error(exc: Exception) -> str:
    message = str(exc)
    if SPEECH_PRIVACY_NOT_ACCEPTED in message or "speech privacy policy was not accepted" in message.lower():
        return SPEECH_PRIVACY_MESSAGE
    return message


def dispose_recognizer(recognizer: object | None) -> None:
    if recognizer is None:
        return
    close = getattr(recognizer, "close", None)
    if callable(close):
        close()
