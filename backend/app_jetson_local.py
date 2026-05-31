# -*- coding: utf-8 -*-
"""
VIVA EX - Advanced Edge Exam & Study System V5 (NVIDIA Jetson Core Engine)
-------------------------------------------------------------------------
A voice-activated, multimodal exam scanning & speech-interactive assistant
designed for blind and visually impaired students to take exams independently.

This public-facing repository demonstrates secure API credential management
using environment variables (.env) and modular edge AI system orchestration.

Compatible with NVIDIA Jetson (local microphones, camera capture, physical printing companion SBCs).
"""

import os
import re
import json
import time
import uuid
import queue
import io
import threading
import base64
import subprocess
import requests
from flask import Flask, request, jsonify, render_template, send_file
from dotenv import load_dotenv

# Try importing specialized hardware/multimedia dependencies
try:
    import numpy as np
except ImportError:
    np = None

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    import cv2
except ImportError:
    cv2 = None

# ==============================================================================
# SECURE CONFIGURATION & API LOADERS
# ==============================================================================
load_dotenv()

# Securely load API Credentials from environment variables
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")

# Print a developer warning if credentials are not configured in the environment
if not GROQ_API_KEY:
    print("\n[WARNING] 'GROQ_API_KEY' is missing in environment variables. Llama-3.3 visual processing features will fail.")
if not DEEPGRAM_API_KEY:
    print("[WARNING] 'DEEPGRAM_API_KEY' is missing in environment variables. Offline/Edge Deepgram speech recognition will fail.\n")

# Edge audio & video parameters
JETSON_CAMERA_INDEX = int(os.getenv("JETSON_CAMERA_INDEX", "0"))
JETSON_CAMERA_WIDTH = int(os.getenv("JETSON_CAMERA_WIDTH", "1920"))
JETSON_CAMERA_HEIGHT = int(os.getenv("JETSON_CAMERA_HEIGHT", "1080"))
JETSON_USE_GSTREAMER = os.getenv("JETSON_USE_GSTREAMER", "0") == "1"
JETSON_GSTREAMER_PIPELINE = os.getenv(
    "JETSON_GSTREAMER_PIPELINE",
    "nvarguscamerasrc ! video/x-raw(memory:NVMM), width=1920, height=1080, format=NV12, framerate=30/1 ! "
    "nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! appsink"
)

JETSON_LOCAL_AUDIO_ENABLED = os.getenv("JETSON_LOCAL_AUDIO_ENABLED", "1") == "1"
JETSON_LOCAL_AUDIO_DEFAULT = os.getenv("JETSON_LOCAL_AUDIO_DEFAULT", "1") == "1"
JETSON_AUDIO_SAMPLE_RATE = int(os.getenv("JETSON_AUDIO_SAMPLE_RATE", "16000"))
JETSON_AUDIO_CHANNELS = int(os.getenv("JETSON_AUDIO_CHANNELS", "1"))
JETSON_AUDIO_BLOCKSIZE = int(os.getenv("JETSON_AUDIO_BLOCKSIZE", "0"))
JETSON_AUDIO_SILENCE_THRESHOLD = 0.025
JETSON_AUDIO_MIN_SPEECH_SEC = float(os.getenv("JETSON_AUDIO_MIN_SPEECH_SEC", "0.1"))
JETSON_AUDIO_SILENCE_SEC = float(os.getenv("JETSON_AUDIO_SILENCE_SEC", "0.6"))
JETSON_AUDIO_MAX_RECORD_SEC = float(os.getenv("JETSON_AUDIO_MAX_RECORD_SEC", "12"))

# Initialize local Flask Web application
app = Flask(__name__)
sessions = {}

# Placeholder for modular visual page boundary detector (e.g., A4 scanner)
class DocumentScannerStub:
    def __init__(self):
        self.enabled = (cv2 is not None)
    
    def scan_image(self, image_bytes):
        """Mock/Wrapper for visual perspective-warp and OCR"""
        # Under production this interfaces with Tesseract OCR or Vision LLM
        return {
            'success': True,
            'text': "1- What is the capital of Jordan? \n A. Amman \n B. Zarqa \n C. Irbid \n D. Aqaba \n\n 2- [BLANK] is the largest planet in our solar system. \n\n 3- The sun rises from the west. (True / False)",
            'pdf_path': "/tmp/scanned_exam_release.pdf"
        }

scanner = DocumentScannerStub()

# ==============================================================================
# AUDIO DEVICE AUTO-DETECTION (ALSA & Headsets)
# ==============================================================================
def get_hyperx_mic_index():
    """Detects ALSA plughw index for gaming headsets (e.g. HyperX)"""
    try:
        out = subprocess.check_output(['arecord', '-l'], text=True)
        for line in out.split('\n'):
            if "HyperX" in line and "card" in line:
                card_num = line.split("card ")[1].split(":")[0]
                return f"plughw:{card_num},0"
    except Exception:
        pass
    return None

def get_hyperx_speaker_hw():
    """Detects hardware output card for headsets using aplay"""
    try:
        out = subprocess.check_output(['aplay', '-l'], text=True)
        for line in out.split('\n'):
            if "HyperX" in line and "card" in line:
                card_num = line.split("card ")[1].split(":")[0]
                return f"plughw:{card_num},0"
    except Exception:
        pass
    return "default"

DYNAMIC_MIC_INDEX = get_hyperx_mic_index()
DYNAMIC_SPEAKER = get_hyperx_speaker_hw()

# ==============================================================================
# SPATIAL EXPLANATION ENGINE & GENERAL LLM CLIENT (Groq Llama-3.3-70b)
# ==============================================================================
def call_groq_text(prompt, model_name="llama-3.3-70b-versatile"):
    """Orchestrates Groq SDK to call visual questions text abstractions"""
    if not GROQ_API_KEY:
        return "Warning: Groq API Key is not set up."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code != 200:
            return f"Error: API status {response.status_code}"
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        return f"Request failed: {str(e)}"

# ==============================================================================
# EDGE AUDIO PROCESSING LOOP & DEEPGRAM SPEECH ENGINE
# ==============================================================================
class JetsonLocalAudioEngine:
    def __init__(self):
        self.enabled = JETSON_LOCAL_AUDIO_ENABLED
        self.thread = None
        self.running = False
        self.stop_event = threading.Event()
        self.queue = queue.Queue()
        self.status_lock = threading.Lock()
        self.last_partial = ''
        self.last_final = ''
        self.last_error = None
        self.last_event_at = 0
        self.stream = None

    def set_status(self, partial=None, final=None, error=None):
        with self.status_lock:
            if partial is not None:
                self.last_partial = partial
            if final is not None:
                self.last_final = final
                if final:
                    self.last_event_at = time.time()
            if error is not None:
                self.last_error = error

    def poll(self):
        items = []
        while True:
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                break
        with self.status_lock:
            return {
                'enabled': self.enabled,
                'running': self.running,
                'partial': self.last_partial,
                'final': self.last_final,
                'error': self.last_error,
                'events': items,
                'last_event_at': self.last_event_at
            }

    def _push_event(self, kind, text, extra=None):
        payload = {'kind': kind, 'text': text, 'ts': time.time()}
        if extra:
            payload.update(extra)
        self.queue.put(payload)

    def start(self, lang='ar'):
        if not self.enabled:
            raise RuntimeError('Local audio is disabled in configurations.')
        if np is None or sd is None or sf is None:
            raise RuntimeError('Dependencies NumPy or SoundDevice or SoundFile are missing.')
        if self.running:
            return {'success': True, 'message': 'Engine is already listening'}
        
        self.current_lang = lang
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.running = True
        self.thread.start()
        
        self.set_status(partial='Microphone active...', final='', error=None)
        self._push_event('status', 'listening')
        return {'success': True, 'message': 'Engine listening started'}

    def stop(self):
        if not self.running:
            return {'success': True, 'message': 'Engine already idle'}
        self.stop_event.set()
        try:
            if self.stream is not None:
                self.stream.abort(ignore_errors=True)
                self.stream.close(ignore_errors=True)
        except Exception:
            pass
        self.running = False
        self.set_status(partial='', final='', error=None)
        self._push_event('status', 'stopped')
        return {'success': True, 'message': 'Engine listening stopped'}

    def _loop(self):
        import sounddevice as sd
        devices = sd.query_devices()
        target_device = None
        
        # Detect headset mic in devices list
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0 and 'HyperX' in dev['name']:
                target_device = i
                break
                
        if target_device is None:
            for i, dev in enumerate(devices):
                if dev['max_input_channels'] > 0 and 'USB' in dev['name']:
                    target_device = i
                    break
                    
        if target_device is None:
            target_device = sd.default.device[0]

        native_sample_rate = 32000
        audio_chunks = []
        speech_started = False
        speech_frames = 0
        silence_frames = 0
        
        max_frames = int(max(1, JETSON_AUDIO_MAX_RECORD_SEC * native_sample_rate))
        min_speech_frames = int(max(1, JETSON_AUDIO_MIN_SPEECH_SEC * native_sample_rate))
        silence_limit_frames = int(max(1, JETSON_AUDIO_SILENCE_SEC * native_sample_rate))

        def callback(indata, frames, _time_info, status):
            audio_queue.put(indata.copy())

        audio_queue = queue.Queue()
        try:
            stream_kwargs = {
                'device': target_device,
                'samplerate': native_sample_rate,
                'channels': 1,
                'dtype': 'float32',
                'callback': callback
            }
            if JETSON_AUDIO_BLOCKSIZE > 0:
                stream_kwargs['blocksize'] = JETSON_AUDIO_BLOCKSIZE

            with sd.InputStream(**stream_kwargs) as stream:
                self.stream = stream
                while not self.stop_event.is_set():
                    try:
                        block = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    
                    mono = block[:, 0] if len(block.shape) > 1 else block
                    level = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0

                    if level >= JETSON_AUDIO_SILENCE_THRESHOLD:
                        if not speech_started:
                            speech_started = True
                            audio_chunks = []
                            speech_frames = 0
                            silence_frames = 0
                            self.set_status(partial='User is speaking...', error=None)
                            self._push_event('status', 'recording')
                        audio_chunks.append(block)
                        speech_frames += len(mono)
                        silence_frames = 0

                    elif speech_started:
                        audio_chunks.append(block)
                        speech_frames += len(mono)
                        silence_frames += len(mono)

                    if speech_started and (silence_frames >= silence_limit_frames or speech_frames >= max_frames):
                        if speech_frames >= min_speech_frames and audio_chunks:
                            self._transcribe_utterance(audio_chunks, native_sample_rate)
                        speech_started = False
                        audio_chunks = []
                        speech_frames = 0
                        silence_frames = 0
                        self.set_status(partial='Waiting for voice...')
                        self._push_event('status', 'listening')
        except Exception as e:
            self.set_status(error=str(e), partial='')
            self._push_event('error', str(e))
        finally:
            self.running = False
            self.stream = None

    def transcribe_file(self, wav_bytes):
        """Sends raw WAV bytes to Deepgram Nova-3 API for low-latency STT"""
        if not DEEPGRAM_API_KEY:
            print("[INFO] Speech skipped - DEEPGRAM_API_KEY placeholder active.")
            return "Sample Voice Answer", "en"
            
        lang_code = getattr(self, 'current_lang', 'ar')
        url = f"https://api.deepgram.com/v1/listen?language={lang_code}&model=nova-3&smart_format=false&punctuate=false&filler_words=false"
        
        headers = {
            "Authorization": f"Token {DEEPGRAM_API_KEY}",
            "Content-Type": "audio/wav"
        }
        try:
            response = requests.post(url, headers=headers, data=wav_bytes, timeout=5)
            if response.status_code == 200:
                res_json = response.json()
                alternatives = res_json.get('results', {}).get('channels', [{}])[0].get('alternatives', [])
                if alternatives:
                    text = alternatives[0].get('transcript', '').strip()
                    return text, lang_code
            return "", None
        except Exception as e:
            print(f"[ERROR] STT Transcribe failed: {e}")
            return "", None

    def _transcribe_utterance(self, audio_chunks, sample_rate):
        self._push_event('status', 'processing')
        data = np.concatenate(audio_chunks, axis=0)
        
        try:
            wav_io = io.BytesIO()
            sf.write(wav_io, data, sample_rate, format='WAV', subtype='PCM_16')
            wav_bytes = wav_io.getvalue()
            
            text, detected_lang = self.transcribe_file(wav_bytes)
            if text:
                self.set_status(partial='', final=text, error=None)
                self._push_event('final', text, {'language': detected_lang})
            else:
                self._push_event('status', 'listening') 
        except Exception as e:
            self.set_status(error=str(e))
            self._push_event('error', str(e))

local_audio = JetsonLocalAudioEngine()

# ==============================================================================
# FLASK ENDPOINTS & MULTIMODAL ROUTING
# ==============================================================================
@app.route('/')
def index():
    return "VIVA EX Engine API running. Please serve Frontend via local index.html."

@app.route('/api/ai_explain', methods=['POST'])
def api_ai_explain():
    """Smart Spatial Radar explanation for Blind Students"""
    try:
        data = request.get_json()
        question_text = data.get('question_text', '')
        lang = data.get('lang', 'ar')
        
        prompt = (
            f"Explain this visual question spatially for a blind student in {lang}. "
            f"Describe tables, shapes, charts, or maps mathematically so they can mentally reconstruct it. "
            f"Question:\n{question_text}"
        )
        explanation = call_groq_text(prompt)
        return jsonify({'explanation': explanation})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/scan_jetson_camera', methods=['POST'])
def api_scan_jetson_camera():
    """Captures A4 exam paper, warps perspective, performs OCR and JSON structures it"""
    try:
        # Mock/Template Vision LLM extraction pipeline
        raw_ocr_text = scanner.scan_image(None)['text']
        
        prompt = (
            f"Extract questions from this OCR text into a JSON list of objects: "
            f"number, text, type ('mcq', 'tf', 'essay'), options (list of letters and texts), section_label. "
            f"OCR:\n{raw_ocr_text}"
        )
        ai_structured_json = call_groq_text(prompt)
        
        # Real system parses JSON regex blocks and normalizes them
        return jsonify({
            'success': True,
            'session_id': str(uuid.uuid4()),
            'questions': [
                {
                    'id': 1, 'number': 1, 'type': 'mcq',
                    'text': "What is the capital of Jordan?",
                    'options': [
                        {'letter': 'a', 'text': "Amman"},
                        {'letter': 'b', 'text': "Zarqa"},
                        {'letter': 'c', 'text': "Irbid"},
                        {'letter': 'd', 'text': "Aqaba"}
                    ],
                    'section_label': "Geography", 'section': 1, 'answer': None
                },
                {
                    'id': 2, 'number': 2, 'type': 'essay',
                    'text': "[BLANK] is the largest planet in our solar system.",
                    'options': [], 'section_label': "Astronomy", 'section': 2, 'answer': None
                },
                {
                    'id': 3, 'number': 3, 'type': 'tf',
                    'text': "The sun rises from the west.",
                    'options': [], 'section_label': "General Science", 'section': 3, 'answer': None
                }
            ],
            'language': 'en',
            'total': 3
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/jetson_audio/start', methods=['POST'])
def api_jetson_audio_start():
    try:
        data = request.get_json() or {}
        exam_lang = data.get('lang', 'ar')
        result = local_audio.start(lang=exam_lang)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/jetson_audio/stop', methods=['POST'])
def api_jetson_audio_stop():
    try:
        result = local_audio.stop()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/jetson_audio/poll', methods=['GET'])
def api_jetson_audio_poll():
    return jsonify(local_audio.poll())

@app.route('/api/jetson_audio/status', methods=['GET'])
def api_jetson_audio_status():
    return jsonify({
        'enabled': local_audio.enabled,
        'running': local_audio.running,
        'sample_rate': JETSON_AUDIO_SAMPLE_RATE,
        'channels': JETSON_AUDIO_CHANNELS
    })

@app.route('/api/tts', methods=['POST'])
def api_tts():
    """Generates audio speech using gTTS and plays it directly on local Jetson ALSA Speaker"""
    try:
        from gtts import gTTS
        data = request.get_json()
        text = data.get('text', '')
        lang = data.get('lang', 'ar')
        
        # Save temp file
        temp_mp3 = "/tmp/speech_out.mp3"
        temp_wav = "/tmp/speech_out.wav"
        
        tts = gTTS(text=text, lang=lang)
        tts.save(temp_mp3)
        
        # Convert MP3 to WAV for direct ALSA plughw play
        os.system(f"ffmpeg -i {temp_mp3} -ar 16000 -ac 1 {temp_wav} -y > /dev/null 2>&1")
        # Play directly to dynamic ALSA headphone plughw card
        os.system(f"aplay -D {DYNAMIC_SPEAKER} {temp_wav} -q > /dev/null 2>&1")
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # SSL context required locally on Edge to allow Microphone/Camera browser policies
    app.run(host='0.0.0.0', debug=True, port=5005)
