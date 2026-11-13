# Transcription server

A small HTTP server that turns recorded audio into text for the
[voice-typing](../README.md) client. It is built on FastAPI and
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) and runs
either on a CPU or on an NVIDIA GPU with CUDA. The client's request format is
unchanged, so the server can be replaced by any endpoint that implements the same
API.

```
server/
├── server.py                 # the application
├── requirements.txt          # base dependencies (CPU is enough)
├── requirements-cuda.txt     # base + NVIDIA runtime wheels for Linux
├── .env.sample               # configuration template
├── run.sh                    # launcher for a shell or systemd
├── smoke_test.sh             # checks a running instance
└── deploy/
    └── whisper-transcribe.service
```

## Requirements

- Python 3.10 – 3.12, 64-bit.
- CPU: a modern x86-64 or arm64 machine. RAM depends on the model; `large-v3`
  needs a few gigabytes.
- GPU: an NVIDIA card supported by CUDA 12 and a matching driver. `large-v3` with
  `float16` needs roughly 4 GB of free video memory; smaller models need less.
- Disk space for the model: from tens of megabytes for `tiny` to about 3 GB for
  `large-v3`.
- Network access on the first start if the model is referenced by its Hugging Face
  id rather than a local directory.

## Install on CPU

```bash
cd server
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.sample .env           # Windows: copy .env.sample .env
```

Edit `.env`. A small model keeps CPU transcription responsive:

```dotenv
WHISPER_MODEL=Systran/faster-whisper-small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

Start the server:

```bash
./run.sh
```

Without the virtualenv activated, use the explicit command:

```bash
.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8766
```

`large-v3` also runs on a CPU, but it is much slower than the smaller models. On
CPU start with `small` or `medium` and `int8`, and move up only if accuracy is not
enough.

## Install on an NVIDIA GPU (Linux)

```bash
cd server
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-cuda.txt
cp .env.sample .env
./run.sh
```

With the default `WHISPER_DEVICE=auto` and `WHISPER_COMPUTE_TYPE=auto` the server
uses CUDA with `float16` when a usable GPU is present, and falls back to CPU with
`int8` otherwise. `run.sh` exports the CUDA libraries installed by the
`nvidia-*` wheels, which CTranslate2 does not find on its own.

Check what the server actually selected:

```bash
curl -s http://127.0.0.1:8766/health
```

## Windows and macOS

- **macOS** has no CUDA support in CTranslate2. Use the CPU path: install
  `requirements.txt`, set `WHISPER_DEVICE=cpu` and `WHISPER_COMPUTE_TYPE=int8`,
  then run `python -m uvicorn server:app --host 127.0.0.1 --port 8766`. The
  `run.sh` launcher needs bash; the direct `uvicorn` command works everywhere.
- **Windows, CPU** works the same way with `requirements.txt`.
- **Windows, GPU** requires the NVIDIA driver, the CUDA 12 runtime and cuDNN 9
  installed system-wide and available on `PATH`. The `nvidia-*` wheels in
  `requirements-cuda.txt` are Linux-only, so on Windows install those runtimes
  from NVIDIA instead. If that is more than you need, the CPU build is the
  simplest reliable option.

## Models

`WHISPER_MODEL` accepts a Hugging Face repository id, which is downloaded on first
start, or a path to a directory that already contains a CTranslate2 model.

| Model | Notes |
|---|---|
| `Systran/faster-whisper-tiny` | fastest, lowest accuracy |
| `Systran/faster-whisper-base` | fast, good for short commands |
| `Systran/faster-whisper-small` | a sensible default for CPU |
| `Systran/faster-whisper-medium` | more accurate, slower on CPU |
| `Systran/faster-whisper-large-v3` | the default; best accuracy, needs a GPU to feel fast |

`int8` is the usual choice on CPU; `float16` on a GPU; `int8_float16` reduces
video memory at a small accuracy cost.

Different devices and compute types can occasionally produce a different word or
punctuation mark on the same recording. Compare two setups with the same model and
the same compute type when the exact output matters.

## Configuration

All settings are read from the environment; `.env` is a convenient way to set
them for a manual start and for the bundled systemd unit. `run.sh` reads `.env`
as a shell file, so quote values that contain spaces, for example
`WHISPER_PROMPT="Use proper punctuation."`.

| Variable | Default | Meaning |
|---|---|---|
| `WHISPER_MODEL` | `Systran/faster-whisper-large-v3` | Model id or local directory |
| `WHISPER_DEVICE` | `auto` | `auto`, `cuda` or `cpu` |
| `WHISPER_COMPUTE_TYPE` | `auto` | `auto`, `float16`, `int8_float16`, `int8`, `float32` |
| `WHISPER_CPU_THREADS` | `0` | CPU threads; `0` lets CTranslate2 choose |
| `WHISPER_BEAM_SIZE` | `5` | `1` is faster, `5` is more accurate |
| `WHISPER_VAD` | `true` | Cut silence out before recognition, which reduces hallucinations |
| `WHISPER_LANGUAGE` | empty | Force one language, e.g. `ru`; empty uses the client's list |
| `WHISPER_HOTWORDS` | empty | Comma-separated words or names to bias towards |
| `WHISPER_PROMPT` | empty | Overrides the built-in per-language dictation prompt |
| `WHISPER_PROMPT_RU` | built in | Prompt used for Russian |
| `WHISPER_PROMPT_EN` | built in | Prompt used for English |
| `WHISPER_LOG_TEXT` | `false` | Put the recognised text into the log |
| `WHISPER_MAX_UPLOAD_BYTES` | `67108864` | Largest accepted upload, 64 MB |
| `WHISPER_TOKEN` | empty | Shared secret; empty disables authentication |
| `HOST` | `127.0.0.1` | Bind address; `0.0.0.0` serves other machines |
| `PORT` | `8766` | Bind port |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error` |

`auto` for the device means CUDA when a GPU can be used and CPU otherwise. An
explicit `WHISPER_DEVICE` is never overridden silently: if CUDA was requested
explicitly and cannot be initialised, the server fails at startup with the
underlying error. At startup the model also runs once on a second of silence, so
a CUDA setup that loads but cannot run (no cuBLAS or cuDNN, as with the CPU-only
`requirements.txt` on a machine with an NVIDIA driver) is caught there: an
automatic choice that fails is retried on the CPU with a warning.

## API

The client sends the language list and its version, and uploads the recording as
`multipart/form-data` in the `file` field:

```bash
curl -s -X POST 'http://127.0.0.1:8766/transcribe?languages=ru,en&app=voice-typing-1.0' \
     -F 'file=@sample.wav'
```

```json
{
  "text": "Recognised text.",
  "language": "en",
  "language_probability": 0.99,
  "duration_s": 3.2,
  "elapsed_s": 0.7
}
```

- `languages` — comma-separated list the client allows. One language is used
  directly; with several, the server detects the language and falls back to the
  first entry when detection picks something outside the list.
- `app` — free-form client identifier, written to the log.
- `token` — the shared secret, when one is configured. The `X-API-Key` header is
  accepted as an alternative.

`GET /health` needs no authentication and reports the model, the effective device
and compute type, and whether a token is required.

| Status | Reason |
|---|---|
| 200 | Transcription succeeded |
| 400 | Empty upload, or no decodable audio stream |
| 401 | Missing or wrong token |
| 413 | Upload larger than `WHISPER_MAX_UPLOAD_BYTES` |
| 500 | Engine error; the body contains `text`, `language` and `error` |

Audio is decoded by PyAV, so wav, flac, ogg, mp3 and mp4 are accepted as long as
the container has an audio stream. The client sends 16 kHz mono wav.

## Security

- The only authentication is a shared token. Without `WHISPER_TOKEN` anyone who
  can reach the port can transcribe audio.
- The server speaks plain HTTP. Bind it to `127.0.0.1`, or put it behind a reverse
  proxy that terminates TLS.
- Dictation is private: `WHISPER_LOG_TEXT=false` keeps the recognised text out of
  the log. The audio itself is never written to disk by the server.
- Startup logs a warning when the bind address is not a loopback address and no
  token is set.

## Run under systemd (Linux)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/whisper-transcribe.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now whisper-transcribe
systemctl --user status whisper-transcribe
journalctl --user -u whisper-transcribe -n 50 --no-pager
```

The unit assumes the repository is at `~/voice-typing`. Adjust
`WorkingDirectory` and `EnvironmentFile` if it is elsewhere. `run.sh` reads
`server/.env` itself as well; the unit also passes the same file through
`EnvironmentFile`, which is harmless and keeps `systemctl show` informative.

## Smoke test

The server does not generate audio, so supply any short recording:

```bash
# Linux example: five seconds from the default microphone
arecord -f S16_LE -r 16000 -c 1 -d 5 sample.wav

./smoke_test.sh sample.wav
./smoke_test.sh sample.wav http://127.0.0.1:8766 my-token
```

The script calls `/health`, posts the file with the same query the client uses and
fails when the returned text is empty.

## Client configuration

In the client's `config.py`:

```python
transcribe_server = 'http://127.0.0.1:8766/transcribe'
languages = 'ru,en'
send_token = False   # True and token = '...' when the server has WHISPER_TOKEN set
```

When the server runs on another machine, replace `127.0.0.1` with its address, set
`HOST=0.0.0.0` on the server and configure `WHISPER_TOKEN` on both sides.

## Troubleshooting

**The log says the model was loaded on CPU although a GPU is present.**
`WHISPER_DEVICE` is explicitly `cpu`, or CUDA cannot be initialised. Check the
device count with `.venv/bin/python -c "import ctranslate2;
print(ctranslate2.get_cuda_device_count())"`. Zero means the driver, the CUDA
runtime or the `nvidia-*` wheels are missing.

**`Library libcublas.so.12 is not found`, `Could not locate cudnn_ops64_9.dll` or a similar library error.**
Install `requirements-cuda.txt` and start the server through `run.sh`, which adds
the wheel libraries to `LD_LIBRARY_PATH`. On Windows install cuDNN 9 from NVIDIA
and put its `bin` directory on `PATH`.

**`CUDA out of memory`.**
Use a smaller model, `int8_float16` instead of `float16`, or free the GPU for
another process.

**The first request is very slow.**
The model is downloaded and loaded on startup. Watch the log; later requests do
not pay that cost.

**HTTP 413.**
Raise `WHISPER_MAX_UPLOAD_BYTES`, or shorten the recording.

**HTTP 401.**
The server has `WHISPER_TOKEN` set: send the same value as `?token=` or in the
`X-API-Key` header, and set `send_token = True` plus `token` in the client.

**HTTP 400, "no audio stream in file".**
The upload is not decodable audio. The client sends 16 kHz mono wav; other
formats are accepted only when the container really has an audio stream.

**Transcription is slow on CPU.**
Use `small` or `medium`, `int8`, and set `WHISPER_CPU_THREADS` to the number of
physical cores. `WHISPER_BEAM_SIZE=1` also helps at a small accuracy cost.
