from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable


VoiceStateCallback = Callable[[str], None]
VoiceTextCallback = Callable[[str], None]
VoiceErrorCallback = Callable[[str], None]
SPEECH_PRIVACY_NOT_ACCEPTED = "-2147199735"
SPEECH_PRIVACY_MESSAGE = (
    "Windows speech recognition is disabled by privacy settings. Open Windows Settings > Privacy & security > "
    "Speech and turn on speech recognition/online speech recognition, then restart Tomo."
)


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
        try:
            asyncio.set_event_loop(loop)
            recognizer = self.recognizer_factory()
            with self._lock:
                if generation != self._generation:
                    return
                self._recognizer = recognizer
                self._loop = loop
            self.on_state("listening")
            text = loop.run_until_complete(recognize_once(recognizer))
            if text and self._is_current(generation):
                self.on_final(text)
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


async def recognize_once(recognizer: object) -> str:
    compile_result = await call_async(recognizer.compile_constraints_async())
    status = getattr(compile_result, "status", None)
    if status is not None and int(status) != 0:
        raise RuntimeError(f"Windows speech recognition constraints failed to compile: {status}")
    result = await call_async(recognizer.recognize_async())
    return recognition_result_text(result)


async def stop_recognition(recognizer: object) -> None:
    operation = getattr(recognizer, "stop_recognition_async", None)
    if callable(operation):
        await call_async(operation())


async def call_async(operation: Awaitable[object]) -> object:
    return await operation


def recognition_result_text(result: object) -> str:
    text = getattr(result, "text", "")
    return str(text).strip()


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
