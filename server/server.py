#!/usr/bin/env python3
"""HTTP transcription server for the voice-typing client.

Client contract (voice_typing.py):
    POST /transcribe?languages=ru,en&app=voice-typing-1.6[&token=...]
    multipart/form-data, field "file" - wav or any format PyAV can decode
    response: JSON {"text": "<text>", "language": "<code>"}

The engine is faster-whisper (CTranslate2). It uses an NVIDIA GPU through CUDA
when one is available and the CPU otherwise. See WHISPER_DEVICE and
WHISPER_COMPUTE_TYPE below for how the choice is made.
"""
from __future__ import annotations

import hmac
import io
import logging
import os
import threading
import time

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

# --- settings ---------------------------------------------------------------

SAMPLE_RATE = 16000
MODEL_PATH = os.getenv("WHISPER_MODEL", "Systran/faster-whisper-large-v3")
HOST = os.getenv("HOST", "127.0.0.1")


def _cuda_device_count() -> int:
    """Number of usable CUDA devices.

    Zero when the CUDA runtime is missing as well as when the machine simply has
    no GPU, so a CPU-only install does not need the NVIDIA packages at all.
    """
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001 - a missing CUDA runtime is a normal CPU setup
        return 0


# "auto" means: CUDA with float16 when a GPU can be used, CPU with int8 otherwise.
# An explicit device is always honoured, and an explicit compute type always wins
# over the automatic one.
REQUESTED_DEVICE = os.getenv("WHISPER_DEVICE", "auto").strip().lower() or "auto"
REQUESTED_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto").strip().lower() or "auto"
DEVICE = "cuda" if REQUESTED_DEVICE == "auto" and _cuda_device_count() > 0 else REQUESTED_DEVICE
if DEVICE == "auto":
    DEVICE = "cpu"
COMPUTE_TYPE = REQUESTED_COMPUTE_TYPE
if COMPUTE_TYPE == "auto":
    COMPUTE_TYPE = "float16" if DEVICE == "cuda" else "int8"

BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "5"))
VAD_FILTER = os.getenv("WHISPER_VAD", "true").lower() in ("1", "true", "yes")
FORCE_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "").strip() or None
HOTWORDS = os.getenv("WHISPER_HOTWORDS", "").strip() or None
# initial_prompt sets the style of the output: with it whisper adds punctuation
# and capital letters. The prompt is chosen per language; WHISPER_PROMPT overrides
# both defaults.
DEFAULT_PROMPT = os.getenv("WHISPER_PROMPT", "").strip() or None
PROMPTS = {
    "ru": os.getenv("WHISPER_PROMPT_RU", "").strip()
    or "Это диктовка. Расставляй знаки препинания, заглавные буквы и абзацы.",
    "en": os.getenv("WHISPER_PROMPT_EN", "").strip()
    or "This is dictation. Use proper punctuation, capitalisation and paragraphs.",
}
AUTH_TOKEN = os.getenv("WHISPER_TOKEN", "").strip()
MAX_UPLOAD_BYTES = int(os.getenv("WHISPER_MAX_UPLOAD_BYTES", str(64 * 1024 * 1024)))
LOG_TEXT = os.getenv("WHISPER_LOG_TEXT", "false").lower() in ("1", "true", "yes")
CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", "0"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("whisper-transcribe")

if not AUTH_TOKEN and HOST not in ("127.0.0.1", "localhost", "::1"):
    log.warning(
        "WHISPER_TOKEN is empty while HOST is %s: anyone who can reach this port "
        "can transcribe audio and read the result",
        HOST,
    )

# --- model ------------------------------------------------------------------

log.info("Loading model %s (device=%s, compute_type=%s)", MODEL_PATH, DEVICE, COMPUTE_TYPE)
_t0 = time.time()


def _new_model() -> WhisperModel:
    """Create the model on DEVICE/COMPUTE_TYPE and run it once.

    Creating it succeeds even when the libraries that inference needs (cuBLAS,
    cuDNN) cannot be loaded, for instance on a machine with an NVIDIA driver and
    the CPU-only requirements.txt. One encoder pass over a second of silence
    makes such a setup fail here, at startup, instead of on every request.
    """
    m = WhisperModel(
        MODEL_PATH,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        cpu_threads=CPU_THREADS,
    )
    m.detect_language(audio=np.zeros(SAMPLE_RATE, dtype=np.float32))
    return m


def _load_model() -> WhisperModel:
    """Load the model, falling back to CPU only when the device was chosen
    automatically. An explicit WHISPER_DEVICE=cuda fails loudly instead of
    silently running somewhere the operator did not ask for.
    """
    global DEVICE, COMPUTE_TYPE
    try:
        return _new_model()
    except Exception:
        if REQUESTED_DEVICE != "auto" or (DEVICE, COMPUTE_TYPE) == ("cpu", "int8"):
            raise
        log.exception(
            "Could not run the model on %s/%s, falling back to CPU (int8)",
            DEVICE,
            COMPUTE_TYPE,
        )
        DEVICE, COMPUTE_TYPE = "cpu", "int8"
        return _new_model()


model = _load_model()
log.info("Model loaded on %s/%s in %.1f s", DEVICE, COMPUTE_TYPE, time.time() - _t0)

# One model instance - one request at a time; requests are short dictations.
_lock = threading.Lock()

app = FastAPI(title="whisper-transcribe", version="1.0")


def _check_token(request: Request, token: str | None) -> None:
    if not AUTH_TOKEN:
        return
    supplied = token or request.headers.get("x-api-key") or ""
    # compare_digest, so a wrong token cannot be guessed from response times
    if not hmac.compare_digest(supplied, AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="invalid token")


def _guess_language(languages: str | None) -> tuple[str | None, list[str]]:
    """languages=ru,en - the languages the client allows.

    A single language is fixed, which is faster and more stable. With several,
    whisper detects the language itself, but the allowed list is kept so the
    first entry can be used when detection is wrong on a short phrase.
    """
    langs = [x.strip() for x in (languages or "").split(",") if x.strip()]
    if FORCE_LANGUAGE:
        return FORCE_LANGUAGE, langs
    if len(langs) == 1:
        return langs[0], langs
    return None, langs


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": MODEL_PATH,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "auth_required": bool(AUTH_TOKEN),
    }


# A plain def, not async: FastAPI runs it in its thread pool, so while one
# recording is being transcribed the server still answers /health and accepts
# the next request, which then waits for the lock.
@app.post("/transcribe")
def transcribe(
    request: Request,
    file: UploadFile = File(...),
    languages: str | None = Query(default=None),
    client_app: str | None = Query(default=None, alias="app"),
    token: str | None = Query(default=None),
):
    _check_token(request, token)

    audio = file.file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="empty file")
    if len(audio) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large")

    language, allowed = _guess_language(languages)
    started = time.time()

    try:
        # The model is not thread-safe around the CUDA context - serialise access.
        with _lock:
            pcm = decode_audio(io.BytesIO(audio), sampling_rate=SAMPLE_RATE)
            if pcm.size == 0:
                raise HTTPException(status_code=400, detail="no audio stream in file")

            if language is None:
                # A cheap encoder pass detects the language, so that both the
                # transcription language and its prompt can be chosen. Detection
                # is unreliable on short phrases - then the first allowed language
                # is used instead.
                detected, _prob, _all = model.detect_language(audio=pcm)
                if allowed and detected not in allowed:
                    log.info(
                        "Detected %s outside the allowed %s - using %s",
                        detected,
                        allowed,
                        allowed[0],
                    )
                    language = allowed[0]
                else:
                    language = detected

            segments, info = model.transcribe(
                pcm,
                language=language,
                beam_size=BEAM_SIZE,
                vad_filter=VAD_FILTER,
                vad_parameters={"min_silence_duration_ms": 300} if VAD_FILTER else None,
                condition_on_previous_text=False,
                hotwords=HOTWORDS,
                initial_prompt=DEFAULT_PROMPT or PROMPTS.get(language),
            )
            # segments is a lazy generator, so it must be consumed inside the lock.
            text = "".join(segment.text for segment in segments)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - the client needs a readable error
        log.exception("Transcription failed (app=%s, bytes=%d)", client_app, len(audio))
        return JSONResponse(
            status_code=500,
            content={"text": "", "language": "", "error": f"{type(exc).__name__}: {exc}"},
        )

    elapsed = time.time() - started
    text = " ".join(text.split())
    log.info(
        "app=%s bytes=%d lang=%s dur=%.2fs elapsed=%.2fs %s",
        client_app,
        len(audio),
        info.language,
        info.duration,
        elapsed,
        f"text={text!r}" if LOG_TEXT else f"chars={len(text)}",
    )
    return {
        "text": text,
        "language": info.language,
        "language_probability": round(info.language_probability, 4),
        "duration_s": round(info.duration, 3),
        "elapsed_s": round(elapsed, 3),
    }
