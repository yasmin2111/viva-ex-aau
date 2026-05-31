# VIVA EX — Voice-Powered Exam Assistant for Blind Students

A system that helps blind and visually impaired students take written exams on their own, without needing a human helper to read questions or write answers.

The idea came from a simple problem: blind students in our university still depend on human scribes to sit for exams. We wanted to change that.

---

## What Does It Do?

The system scans a printed exam paper using a camera, reads the questions out loud to the student, listens to their spoken answers, and saves everything into a report at the end.

The student controls everything using voice commands — no screen, no keyboard, no mouse. Just talking.

It also handles a tricky case that most systems ignore: questions that include diagrams, tables, or charts. When the system detects something like that, it automatically generates a verbal spatial description of the visual element so the student can mentally picture it and answer properly.

---

## Core Features

- **Camera Scanning:** Captures a physical A4 exam sheet using a Jetson camera, corrects the perspective automatically, then extracts and structures the questions using an LLM.
- **Voice Detection Loop:** Runs locally on the device — listens for when the student starts and stops talking, then sends the audio for transcription.
- **Spatial Radar:** Detects visual elements in questions (diagrams, equations, shapes) and generates a mental description read aloud to the student.
- **Bilingual Support:** Works with both Arabic and English exams and voice commands.
- **Physical Print Output:** Connects to a Raspberry Pi companion device to physically print the student's answer report at the end of the exam.

---

## How It Works

```
Capture exam paper with camera
        ↓
Warp + flatten image to A4
        ↓
Extract questions via OCR + LLM
        ↓
Read question aloud (TTS)
        ↓
Detect if question has a visual element
        ↓
If yes → generate spatial description and read it
        ↓
Listen to student's spoken answer
        ↓
Process voice command (Next / Repeat / A / B / C / D / True / False)
        ↓
Save answer → repeat until exam is done
        ↓
Generate report → send to printer
```

---

## System Architecture

```
Student
   ↓
Frontend (HTML + JS)
   ↓
Backend (Python + Flask)
   ↓
┌─────────────────────────────┐
│   Running on NVIDIA Jetson  │
│                             │
│  - OpenCV / GStreamer cam   │
│  - Local audio SAD loop     │
│  - ALSA speaker output      │
│  - Groq LLM (Llama 3.3)     │
│  - Deepgram STT API         │
└─────────────────────────────┘
   ↓
Raspberry Pi (optional)
   ↓
Printed Answer Report
```

---

## Technologies Used

**Backend:**
- Python, Flask
- OpenCV, GStreamer
- NumPy, SoundDevice, SoundFile

**AI & Speech:**
- Groq API (Llama-3.3-70b) — question extraction and spatial descriptions
- Deepgram Nova-3 — speech to text
- gTTS + FFmpeg + ALSA — text to speech, played locally

**Frontend:**
- HTML5, CSS3, Vanilla JavaScript
- PDF.js for PDF rendering

---

## Project Structure

```
viva-ex/
│
├── backend/
│   └── app_jetson_local.py
│
├── frontend/
│   └── index.html
│
├── .env.example
├── .gitignore
└── README.md
```

---

## Setup

1. Install system dependencies:
```bash
sudo apt-get install portaudio19-dev ffmpeg
```

2. Install Python packages:
```bash
pip install flask sounddevice soundfile numpy requests python-dotenv gTTS
```

3. Copy `.env.example` to `.env` and add your API keys:
```
GROQ_API_KEY=your_key_here
DEEPGRAM_API_KEY=your_key_here
```

4. Run the backend:
```bash
python backend/app_jetson_local.py
```

Then open `frontend/index.html` in a browser.

---

## Future Plans

- Run the LLM fully offline on the Jetson (no internet needed)
- Add stereo depth camera support for better paper detection
- Build a mobile companion app

---

## About

Built as a graduation project at **Amman Arab University**.

Supervisor: Lial Alzabin
