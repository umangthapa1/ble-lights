# Piper voices

Default voice: **en_US-lessac-medium**, US English, 22,050 Hz.

From the project root, install and download it with:

```sh
python -m pip install -r requirements-voice.txt
python -m piper.download_voices en_US-lessac-medium --data-dir voices
```

Both `en_US-lessac-medium.onnx` and `en_US-lessac-medium.onnx.json` are required.
Downloaded models are ignored by Git. To use another voice, set `PIPER_MODEL`
to its `.onnx` path; its matching `.onnx.json` must be next to it.

Voice source and licensing details:
[Lessac medium model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD).

Piper software: [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl).
