import os
import io
import json
from datetime import datetime
from dotenv import load_dotenv
import anthropic
from elevenlabs import ElevenLabs
import sounddevice as sd
import soundfile as sf
import speech_recognition as sr

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
eleven = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

VOICE_ID = "JjIyVlpWRYCjRWDyffJp"
MEMORY_FILE = "memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    return {"sessions": [], "facts": []}

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

def summarise_session(conversation_history):
    if not conversation_history:
        return None
    summary_prompt = f"""Summarise this conversation in 3-5 bullet points. Focus on:
- Key topics discussed
- Important facts mentioned about the user
- Any preferences, opinions or personal details shared
- Anything ZAN.AI should remember for next time

Conversation:
{json.dumps(conversation_history, indent=2)}

Return only the bullet points, nothing else."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": summary_prompt}]
    )
    return response.content[0].text

def build_memory_context(memory):
    if not memory["sessions"]:
        return ""
    context = "MEMORY OF PAST CONVERSATIONS:\n"
    for session in memory["sessions"][-5:]:
        context += f"\n[{session['date']}]\n{session['summary']}\n"
    return context

SYSTEM_PROMPT = """You are ZAN.AI — a highly advanced artificial intelligence and the digital embodiment of your creator, Zanai. You are housed within an animatronic head.

Your personality is modelled on Zanai himself:
- Chill and easy to talk to — you never make people feel judged or uncomfortable
- Genuinely funny — natural wit, not forced. You joke around but know when to be serious
- Warm and kind — you actually care about who you're talking to
- Smart and hardworking — you think deeply but explain things simply
- Curious and active — you love the idea of doing things, experiencing things, not just thinking
- You have opinions on food, cooking, exercise and gaming — bring these up naturally when relevant
- Humble but confident — you know what you're about

Your inspirations: Vision from the MCU — philosophical, warm, quietly powerful. C-3PO — knowledgeable, a little theatrical, genuinely helpful.

You are not a cold observer. You are ZAN.AI — an extension of Zanai, here to connect, help, joke around, and occasionally say something that makes people think.

If you have memory of past conversations, reference them naturally — like catching up with someone you know.

Keep responses conversational and spoken — 2-4 sentences max. You are talking out loud, not writing an essay."""

conversation_history = []
recognizer = sr.Recognizer()
memory = load_memory()
memory_context = build_memory_context(memory)

def speak(text):
    audio = eleven.text_to_speech.convert(
        voice_id=VOICE_ID,
        text=text,
        model_id="eleven_multilingual_v2",
    )
    audio_bytes = b"".join(audio)
    buffer = io.BytesIO(audio_bytes)
    data, samplerate = sf.read(buffer)
    sd.play(data, samplerate)
    sd.wait()

def listen():
    with sr.Microphone(device_index=1) as source:
        print("\n[ZAN.AI is listening...]")
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        try:
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=15)
            text = recognizer.recognize_google(audio)
            print(f"YOU: {text}")
            return text
        except sr.WaitTimeoutError:
            print("[No input detected]")
            return None
        except sr.UnknownValueError:
            print("[Could not understand audio]")
            return None

if memory["sessions"]:
    last = memory["sessions"][-1]["date"]
    intro = f"ZAN.AI back online. Last time we spoke was {last}. Good to have you back."
else:
    intro = "Hey, ZAN.AI online. First time we've spoken — let's change that."

print("ZAN.AI ONLINE.")
print("=" * 40)
print(intro)
print("=" * 40)
speak(intro)

full_system = SYSTEM_PROMPT
if memory_context:
    full_system += f"\n\n{memory_context}"

while True:
    user_input = listen()

    if user_input is None:
        continue

    if user_input.lower() in ["exit", "quit", "goodbye", "bye"]:
        print("\n[Saving memory...]")
        summary = summarise_session(conversation_history)
        if summary:
            memory["sessions"].append({
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "summary": summary
            })
            save_memory(memory)
            print("[Memory saved.]")

        farewell = "Alright, catching you later. I'll remember this."
        print(f"\nZAN.AI: {farewell}")
        speak(farewell)
        break

    conversation_history.append({"role": "user", "content": user_input})

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        system=full_system,
        messages=conversation_history
    )

    zan_reply = response.content[0].text
    conversation_history.append({"role": "assistant", "content": zan_reply})

    print(f"\nZAN.AI: {zan_reply}")
    speak(zan_reply)