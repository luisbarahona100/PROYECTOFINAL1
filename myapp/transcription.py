# transcription.py

from flask import Blueprint, jsonify, request
import threading
import sounddevice as sd
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
import queue
import asyncio

transcription = Blueprint('transcription', __name__)

# Variable para almacenar el texto transcrito
texto_transcrito = ""
is_transcribing = False
transcription_thread = None
audio_queue = queue.Queue()

class MyEventHandler(TranscriptResultStreamHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ultimo_transcrito = ""

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        results = transcript_event.transcript.results
        for result in results:
            for alt in result.alternatives:
                self.ultimo_transcrito = alt.transcript

    def get_transcrito(self):
        return self.ultimo_transcrito

def mic_stream():
    def callback(indata, frames, time, status):
        if status:
            print(status, flush=True)
        audio_queue.put(bytes(indata))

    stream = sd.RawInputStream(
        channels=1,
        samplerate=16000,
        callback=callback,
        blocksize=1024 * 2,
        dtype="int16",
    )

    with stream:
        while is_transcribing:
            sd.sleep(100)

async def transcribir_audio():
    global texto_transcrito
    try:
        client = TranscribeStreamingClient(region="us-east-2")
        stream = await client.start_stream_transcription(
            language_code="es-US",
            media_sample_rate_hz=16000,
            media_encoding="pcm"
        )

        handler = MyEventHandler(stream.output_stream)

        async def write_chunks():
            try:
                while is_transcribing:
                    if not audio_queue.empty():
                        chunk = audio_queue.get()
                        await stream.input_stream.send_audio_event(audio_chunk=chunk)
                await stream.input_stream.end_stream()
            except Exception as e:
                print(f"Error in write_chunks: {e}", flush=True)

        await asyncio.gather(write_chunks(), handler.handle_events())
        texto_transcrito = handler.get_transcrito()
    except Exception as e:
        print(f"Error in transcribir_audio: {e}", flush=True)

def start_transcription_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(transcribir_audio())

@transcription.route('/start_transcription', methods=['POST'])
def start_transcription():
    global is_transcribing, transcription_thread
    if not is_transcribing:
        is_transcribing = True
        transcription_thread = threading.Thread(target=start_transcription_thread)
        transcription_thread.start()
        mic_stream_thread = threading.Thread(target=mic_stream)
        mic_stream_thread.start()
    return jsonify({'status': 'Transcription started'})

@transcription.route('/stop_transcription', methods=['POST'])
def stop_transcription():
    global is_transcribing, transcription_thread
    if is_transcribing:
        is_transcribing = False
        transcription_thread.join()
    return jsonify({'status': 'Transcription stopped'})
